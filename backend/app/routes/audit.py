from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..auth import require_roles
from ..http import get_services, page_body
from ..models import Role, User
from ..pagination import paginate
from ..services import Services

router = APIRouter(prefix="/api/v1/audit")

Limit = Annotated[int, Query(ge=1, le=200)]


@router.get("/events")
def list_audit_events(
    actorId: UUID | None = None,
    entityType: str | None = None,
    entityId: str | None = None,
    action: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
    sort: str | None = None,
    user: User = Depends(require_roles(Role.supervisor, Role.system_admin)),
    services: Services = Depends(get_services),
):
    events = [e for e in services.store.list_audit_events() if e.tenantId == user.tenantId]  # tenant isolation
    if (sort or "-occurredAt").startswith("-"):
        events.reverse()  # entries with an identical timestamp then come out newest first, not oldest first
    if actorId:
        events = [e for e in events if e.actorId == actorId]
    if entityType:
        events = [e for e in events if e.entityType == entityType]
    if entityId:
        events = [e for e in events if e.entityId == entityId]
    if action:
        events = [e for e in events if e.action == action]
    if from_:
        events = [e for e in events if e.occurredAt >= from_]
    if to:
        events = [e for e in events if e.occurredAt <= to]
    items, page = paginate(
        events, limit=limit, cursor=cursor, sort=sort, default_sort="-occurredAt",
        getters={"occurredAt": lambda e: e.occurredAt, "action": lambda e: e.action, "entityType": lambda e: e.entityType},
    )
    return JSONResponse(page_body(items, page))
