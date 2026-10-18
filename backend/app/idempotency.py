import hashlib
import json
import threading
from typing import Callable
from uuid import UUID

from .errors import ApiError
from .models import ErrorCode


def fingerprint(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class IdempotencyCache:
    """In-process replay cache for Idempotency-Key. Single worker only (matches the MVP
    plan); a multi-worker deployment needs this in the database. Only successful
    responses are cached, so a request rejected for a fixable reason can be retried
    under the same key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[tuple[UUID, str, str], tuple[str, int, dict]] = {}

    def run(
        self,
        *,
        user_id: UUID,
        operation: str,
        key: str,
        request_fingerprint: str,
        produce: Callable[[], tuple[int, dict]],
    ) -> tuple[int, dict]:
        scope = (user_id, operation, key)
        with self._lock:
            hit = self._seen.get(scope)
            if hit is not None:
                if hit[0] != request_fingerprint:
                    raise ApiError(
                        ErrorCode.IDEMPOTENCY_KEY_REUSED,
                        "This Idempotency-Key was already used with a different request.",
                    )
                return hit[1], hit[2]
            status, body = produce()
            self._seen[scope] = (request_fingerprint, status, body)
            return status, body

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()
