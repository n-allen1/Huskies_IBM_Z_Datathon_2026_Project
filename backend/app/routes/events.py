from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from ..auth import require_roles
from ..events import event_stream
from ..http import get_services
from ..models import Role
from ..services import Services

router = APIRouter(prefix="/api/v1/events")


@router.get("/stream")
async def stream_events(
    types: list[str] | None = Query(None),  # unknown names match nothing; the contract declares no 400 here
    lastEventId: str | None = Query(None),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    _=Depends(require_roles(*Role)),
    services: Services = Depends(get_services),
):
    """Server-sent events: `id:`, `event:` and `data:` (an EventEnvelope) per frame, a heartbeat every 15 s, and
    replay from Last-Event-ID (header, or the `lastEventId` query parameter for clients that cannot set headers)."""
    stream = event_stream(
        services.events,
        last_event_id=last_event_id or lastEventId,
        types=set(types) if types else None,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
