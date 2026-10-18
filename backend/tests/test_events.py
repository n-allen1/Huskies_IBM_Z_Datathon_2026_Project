"""The event bus and the SSE stream. The stream is tested as an async generator because Starlette's TestClient
buffers a whole response and so cannot read an endless one; a real-server check is a curl on the VM."""

import asyncio
import json
import threading

from app.events import EventBus, event_stream, frame

from .conftest import NOW


def make_bus(capacity=1000) -> EventBus:
    return EventBus(capacity=capacity, clock=lambda: NOW)


def publish(bus, type_="incident.created", entity="incident", entity_id="x", version=1, **payload):
    return bus.publish(type_, entity, entity_id, version, payload)


def parse(raw: str) -> dict:
    fields = dict(line.split(": ", 1) for line in raw.strip().split("\n"))
    return {**fields, "envelope": json.loads(fields["data"])}


async def read(bus, n, **kw):
    """Consume n frames from a fresh stream."""
    return [parse(f) async for f in event_stream(bus, max_frames=n, **kw)]


async def with_stream(bus, n, action, **kw):
    """Start a stream, run `action` (in a worker thread, like a real request) once it is subscribed, return its frames."""
    task = asyncio.create_task(read(bus, n, **kw))
    await asyncio.sleep(0.05)  # let the stream subscribe
    await asyncio.to_thread(action)
    return await asyncio.wait_for(task, timeout=3)


# --- the bus ------------------------------------------------------------------------------------------

def test_ids_are_a_zero_padded_increasing_sequence():
    bus = make_bus()
    a, b = publish(bus), publish(bus)
    assert (a["id"], b["id"]) == ("000000000001", "000000000002") and bus.last_id == b["id"]
    assert a["schemaVersion"] == "1" and a["entity"] == {"type": "incident", "id": "x"} and a["timestamp"].endswith("Z")


def test_replay_returns_exactly_what_was_missed_or_none_when_it_cannot():
    bus = make_bus(capacity=3)
    for _ in range(3):
        publish(bus)
    assert [e["id"] for e in bus.since("000000000001")] == ["000000000002", "000000000003"]
    assert bus.since("000000000003") == []  # up to date
    assert bus.since("000000000000") is not None  # the whole buffer is still there
    publish(bus), publish(bus)  # events 1 and 2 fall out of the 3-slot buffer
    assert bus.since("000000000002") is not None and bus.since("000000000001") is None  # a gap that cannot be filled
    assert bus.since("000000009999") is None  # ahead of the server: it restarted
    assert bus.since("garbage") is None


def test_frame_format_follows_the_contract():
    env = publish(make_bus(), "unit.status_changed", "unit", "u1", status="out_of_service")
    lines = frame(env).split("\n")
    assert lines[0] == f"id: {env['id']}" and lines[1] == "event: unit.status_changed" and lines[2].startswith("data: ") and frame(env).endswith("\n\n")
    assert json.loads(lines[2][6:]) == env
    heartbeat = frame({"id": "000000000009", "type": "stream.heartbeat", "payload": {}})
    assert "id:" not in heartbeat  # a heartbeat must never move the client's Last-Event-ID


# --- the stream ---------------------------------------------------------------------------------------

def test_live_events_arrive_in_order_from_worker_threads():
    bus = make_bus()
    frames = asyncio.run(with_stream(bus, 2, lambda: (publish(bus, "incident.created"), publish(bus, "incident.updated"))))
    assert [f["event"] for f in frames] == ["incident.created", "incident.updated"]
    assert [f["id"] for f in frames] == ["000000000001", "000000000002"]


def test_a_new_stream_does_not_replay_history_unless_asked():
    bus = make_bus()
    publish(bus, "incident.created")
    frames = asyncio.run(with_stream(bus, 1, lambda: publish(bus, "incident.updated")))
    assert [f["event"] for f in frames] == ["incident.updated"]


def test_types_filter_delivers_only_the_requested_types():
    bus = make_bus()
    frames = asyncio.run(with_stream(
        bus, 1, lambda: (publish(bus, "incident.created"), publish(bus, "unit.status_changed", "unit"), publish(bus, "incident.updated")),
        types={"unit.status_changed"},
    ))
    assert [f["event"] for f in frames] == ["unit.status_changed"]


def test_unknown_types_simply_match_nothing():
    bus = make_bus()
    publish(bus)
    frames = asyncio.run(read(bus, 1, types={"no.such.type"}, heartbeat_seconds=0.05))
    assert [f["event"] for f in frames] == ["stream.heartbeat"]


def test_reconnecting_replays_missed_events_without_duplicates():
    bus = make_bus()
    for t in ("incident.created", "incident.updated", "unit.status_changed"):
        publish(bus, t)
    frames = asyncio.run(with_stream(bus, 3, lambda: publish(bus, "recommendation.created", "recommendation"), last_event_id="000000000001"))
    assert [f["id"] for f in frames] == ["000000000002", "000000000003", "000000000004"]  # two replayed, one live, none twice


def test_a_last_event_id_that_cannot_be_replayed_asks_the_client_to_resync():
    bus = make_bus()
    publish(bus)
    for bad in ("000000009999", "garbage"):
        first = asyncio.run(read(bus, 1, last_event_id=bad))[0]
        assert first["event"] == "stream.resync_required" and first["id"] == bus.last_id  # moves the client to "now"


def test_idle_streams_send_heartbeats_without_an_id():
    frames = asyncio.run(read(make_bus(), 2, heartbeat_seconds=0.05))
    assert [f["event"] for f in frames] == ["stream.heartbeat", "stream.heartbeat"] and all("id" not in f for f in frames)


def test_demo_reset_tells_connected_clients_to_resync():
    bus = make_bus()
    publish(bus)
    frames = asyncio.run(with_stream(bus, 1, bus.reset))
    assert frames[0]["event"] == "stream.resync_required" and "reset" in frames[0]["envelope"]["payload"]["reason"]
    assert bus.since("000000000000") is None or bus.since("000000000000") == []


def test_a_finished_or_disconnected_stream_leaves_no_subscriber_behind():
    async def scenario():
        bus = make_bus()
        await read(bus, 1, heartbeat_seconds=0.01)
        assert not bus._subscribers  # finished normally
        task = asyncio.create_task(read(bus, 5, heartbeat_seconds=60))
        await asyncio.sleep(0.05)
        assert len(bus._subscribers) == 1
        task.cancel()  # what happens when the client disconnects
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not bus._subscribers

    asyncio.run(scenario())


def test_a_consumer_that_cannot_keep_up_is_told_to_resync_instead_of_blocking_publishers():
    async def scenario():
        bus = make_bus()
        gen = event_stream(bus, max_frames=1, heartbeat_seconds=60)
        task = asyncio.create_task(anext(gen))
        await asyncio.sleep(0.05)
        sub = next(iter(bus._subscribers))
        sub.overflowed = True  # as if its queue had filled
        await asyncio.to_thread(publish, bus)
        first = parse(await asyncio.wait_for(task, timeout=3))
        await gen.aclose()
        return first

    assert asyncio.run(scenario())["event"] == "stream.resync_required"


# --- the endpoint -------------------------------------------------------------------------------------------

def test_the_stream_endpoint_requires_authentication(api):
    api.call("streamEvents", role=None, expect=401)


def test_publishing_from_many_threads_keeps_ids_unique_and_ordered():
    bus = make_bus()
    threads = [threading.Thread(target=lambda: [publish(bus) for _ in range(50)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [e["id"] for e in bus.since("000000000000")]
    assert len(ids) == 400 == len(set(ids)) and ids == sorted(ids)


class RacyBus(EventBus):
    """An event is published between the stream subscribing and the replay being computed: it is then both in
    the replay and in the live queue, and must be delivered once."""

    def since(self, last_event_id):
        publish(self, "unit.status_changed", "unit")
        return super().since(last_event_id)


def test_an_event_that_lands_during_replay_is_not_delivered_twice():
    bus = RacyBus(clock=lambda: NOW)
    publish(bus, "incident.created")
    frames = asyncio.run(read(bus, 2, last_event_id="000000000001", heartbeat_seconds=0.1))
    assert [f["event"] for f in frames] == ["unit.status_changed", "stream.heartbeat"]  # the live copy was skipped
