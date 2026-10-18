import base64
import json
from typing import Any, Callable, Sequence, TypeVar

from .errors import ApiError
from .models import ErrorCode

T = TypeVar("T")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        offset = json.loads(base64.urlsafe_b64decode(cursor.encode()))["o"]
        if isinstance(offset, int) and offset >= 0:
            return offset
    except Exception:
        pass
    raise ApiError(ErrorCode.VALIDATION_ERROR, "Invalid cursor.", [("cursor", "Cursor is not valid.")])


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def sort_items(
    items: Sequence[T],
    sort: str | None,
    getters: dict[str, Callable[[T], Any]],
    default: str,
) -> list[T]:
    """Comma-separated fields, '-' prefix for descending. Unknown fields are a 400."""
    out = list(items)
    fields = [f.strip() for f in (sort or default).split(",") if f.strip()]
    for field in reversed(fields):  # stable sorts, least significant key first
        desc = field.startswith("-")
        name = field.lstrip("-")
        if name not in getters:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                "Unsupported sort field.",
                [("sort", f"'{name}' is not sortable; use one of {sorted(getters)}.")],
            )
        getter = getters[name]
        out.sort(key=lambda x: (getter(x) is None, getter(x)), reverse=desc)
    return out


def paginate(
    items: Sequence[T],
    *,
    limit: int,
    cursor: str | None,
    sort: str | None,
    getters: dict[str, Callable[[T], Any]],
    default_sort: str,
) -> tuple[list[T], dict]:
    ordered = sort_items(items, sort, getters, default_sort)
    start = _decode_cursor(cursor)
    end = start + limit
    page = {
        "nextCursor": _encode_cursor(end) if end < len(ordered) else None,
        "limit": limit,
        "totalEstimate": len(ordered),
    }
    return ordered[start:end], page
