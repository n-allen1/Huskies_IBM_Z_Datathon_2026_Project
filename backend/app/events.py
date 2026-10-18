"""In-process server-sent events: a ring buffer of recent events plus live subscribers.

Single worker only (docs/mvp-plan.md): ids are one process-wide sequence, so after a restart a client's
Last-Event-ID may be ahead of the server; that is detected and answered with `stream.resync_required`.
Producers run in FastAPI's worker threads; consumers are async generators on the event loop, so delivery
goes through `call_soon_threadsafe`.
"""

import asyncio
import json
import threading
from collections import deque
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

SCHEMA_VERSION = "1"
HEARTBEAT_SECONDS = 15.0
RESYNC = "stream.resync_required"
HEARTBEAT = "stream.heartbeat"
_CONTROL = {RESYNC, HEARTBEAT}  # always delivered, never filtered or de-duplicated


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class _Subscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop, start_seq: int):
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.start_seq = start_seq
        self.overflowed = False

    def push(self, envelope: dict) -> None:
        try:
            self.loop.call_soon_threadsafe(self._offer, envelope)
        except RuntimeError:  # the loop is gone (shutdown); nobody is listening
            pass

    def _offer(self, envelope: dict) -> None:
        try:
            self.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            self.overflowed = True  # a slow consumer is told to resync instead of blocking producers


class EventBus:
    def __init__(self, capacity: int = 1000, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._lock = threading.Lock()
        self._buffer: deque[dict] = deque(maxlen=capacity)
        self._seq = 0
        self._subscribers: set[_Subscriber] = set()
        self._clock = clock

    @property
    def last_id(self) -> str:
        return f"{self._seq:012d}"

    def _control(self, type_: str, payload: dict | None = None) -> dict:
        return {
            "id": self.last_id, "type": type_, "timestamp": _iso(self._clock()), "entity": {"type": "none"},
            "version": 1, "schemaVersion": SCHEMA_VERSION, "payload": payload or {},
        }

    def publish(self, type_: str, entity_type: str, entity_id: str | None, version: int, payload: dict) -> dict:
        with self._lock:
            self._seq += 1
            entity = {"type": entity_type, **({"id": entity_id} if entity_id else {})}
            envelope = {
                "id": f"{self._seq:012d}", "type": type_, "timestamp": _iso(self._clock()), "entity": entity,
                "version": max(1, version), "schemaVersion": SCHEMA_VERSION, "payload": payload,
            }
            self._buffer.append(envelope)
            subscribers = list(self._subscribers)
        for sub in subscribers:
            sub.push(envelope)
        return envelope

    def since(self, last_event_id: str) -> list[dict] | None:
        """Events after `last_event_id`, or None when they cannot be replayed (unknown, too old, or from a
        previous server run) and the client must refetch."""
        try:
            last = int(last_event_id)
        except ValueError:
            return None
        with self._lock:
            if last > self._seq or last < 0:
                return None  # ahead of us: the server restarted
            if last == self._seq:
                return []
            first = int(self._buffer[0]["id"]) if self._buffer else self._seq + 1
            if last < first - 1:
                return None  # the gap is no longer in the buffer
            return [e for e in self._buffer if int(e["id"]) > last]

    def subscribe(self) -> _Subscriber:
        with self._lock:
            sub = _Subscriber(asyncio.get_running_loop(), self._seq)
            self._subscribers.add(sub)
            return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    def reset(self) -> None:
        """Demo reset: drop history and tell every live client to refetch."""
        with self._lock:
            self._buffer.clear()
            subscribers = list(self._subscribers)
        resync = self._control(RESYNC, {"reason": "demo data was reset"})
        for sub in subscribers:
            sub.push(resync)


def frame(envelope: dict) -> str:
    """One SSE frame. Heartbeats carry no `id:` line so they never move the client's Last-Event-ID."""
    lines = [] if envelope["type"] == HEARTBEAT else [f"id: {envelope['id']}"]
    lines += [f"event: {envelope['type']}", f"data: {json.dumps(envelope, separators=(',', ':'))}"]
    return "\n".join(lines) + "\n\n"


async def event_stream(
    bus: EventBus,
    *,
    last_event_id: str | None = None,
    types: set[str] | None = None,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    max_frames: int | None = None,
) -> AsyncIterator[str]:
    """Replay what the client missed (or ask it to resync), then stream live events with heartbeats.
    `max_frames` exists so tests can end the otherwise endless stream."""
    sub = bus.subscribe()  # subscribe first so nothing published during replay is lost
    sent_frames = 0
    seen = sub.start_seq
    try:
        if last_event_id is not None:
            replay = bus.since(last_event_id)
            if replay is None:
                yield frame(bus._control(RESYNC, {"reason": "Last-Event-ID is unknown or too old"}))
                sent_frames += 1
            else:
                seen = int(last_event_id)
                for envelope in replay:
                    seen = int(envelope["id"])
                    if types is None or envelope["type"] in types:
                        yield frame(envelope)
                        sent_frames += 1
        while max_frames is None or sent_frames < max_frames:
            try:
                envelope = await asyncio.wait_for(sub.queue.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield frame(bus._control(HEARTBEAT))
                sent_frames += 1
                continue
            if sub.overflowed:
                sub.overflowed = False
                while not sub.queue.empty():
                    sub.queue.get_nowait()
                envelope = bus._control(RESYNC, {"reason": "client too slow; events were dropped"})
            if envelope["type"] not in _CONTROL:
                if int(envelope["id"]) <= seen:
                    continue  # already delivered by the replay
                seen = int(envelope["id"])
                if types is not None and envelope["type"] not in types:
                    continue
            yield frame(envelope)
            sent_frames += 1
    finally:
        bus.unsubscribe(sub)
