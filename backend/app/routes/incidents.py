from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..auth import require_roles
from ..http import IdemKey, dump, get_services, idempotent as _idempotent, ok, page_body
from ..models import (
    ClassificationCreate,
    IncidentCategory,
    IncidentCreate,
    IncidentPriority,
    IncidentStatus,
    IncidentUpdate,
    RecommendationCreate,
    Role,
    User,
)
from ..pagination import paginate
from ..services import Services

router = APIRouter(prefix="/api/v1/incidents")

R = Role
_READ = (R.call_taker, R.dispatcher, R.supervisor, R.system_admin)

Limit = Annotated[int, Query(ge=1, le=200)]


@router.get("")
def list_incidents(
    status: list[IncidentStatus] | None = Query(None),
    priority: IncidentPriority | None = None,
    category: IncidentCategory | None = None,
    createdAfter: datetime | None = None,
    createdBefore: datetime | None = None,
    q: str | None = Query(None, max_length=200),
    limit: Limit = 50,
    cursor: str | None = None,
    sort: str | None = None,
    _=Depends(require_roles(*_READ)),
    services: Services = Depends(get_services),
):
    incidents = services.store.list_incidents()
    if status:
        incidents = [i for i in incidents if i.status in status]
    if priority:
        incidents = [i for i in incidents if i.priority == priority]
    if category:
        incidents = [i for i in incidents if i.confirmedCategory == category]
    if createdAfter:
        incidents = [i for i in incidents if i.createdAt >= createdAfter]
    if createdBefore:
        incidents = [i for i in incidents if i.createdAt <= createdBefore]
    if q:
        needle = q.lower()
        incidents = [
            i for i in incidents
            if needle in i.narrative.lower() or needle in i.number.lower() or needle in (i.location.address or "").lower()
        ]
    items, page = paginate(
        incidents, limit=limit, cursor=cursor, sort=sort, default_sort="-createdAt",
        getters={
            "createdAt": lambda i: i.createdAt, "updatedAt": lambda i: i.updatedAt, "number": lambda i: i.number,
            "priority": lambda i: i.priority.value, "status": lambda i: i.status.value,
        },
    )
    return JSONResponse(page_body(items, page))


@router.post("")
def create_incident(
    body: IncidentCreate,
    idempotency_key: IdemKey,
    user: User = Depends(require_roles(R.call_taker, R.dispatcher, R.system_admin)),
    services: Services = Depends(get_services),
):
    return _idempotent(
        services, user, "createIncident", idempotency_key, body,
        lambda: (201, dump(services.create_incident(user, body))),
    )


@router.get("/{incidentId}")
def get_incident(incidentId: UUID, _=Depends(require_roles(*_READ)), services: Services = Depends(get_services)):
    return ok(services.get_incident(incidentId))


@router.patch("/{incidentId}")
def update_incident(
    incidentId: UUID,
    body: IncidentUpdate,
    user: User = Depends(require_roles(R.call_taker, R.dispatcher, R.supervisor, R.system_admin)),
    services: Services = Depends(get_services),
):
    return ok(services.update_incident(user, incidentId, body))


@router.post("/{incidentId}/classifications")
def create_classification(
    incidentId: UUID,
    body: ClassificationCreate,
    user: User = Depends(require_roles(R.call_taker, R.dispatcher, R.supervisor)),
    services: Services = Depends(get_services),
):
    return JSONResponse(dump(services.create_classification(user, incidentId, body)), status_code=201)


@router.get("/{incidentId}/requirements")
def get_incident_requirements(
    incidentId: UUID, _=Depends(require_roles(*_READ)), services: Services = Depends(get_services)
):
    return ok(services.requirements(incidentId))


@router.get("/{incidentId}/recommendations")
def list_incident_recommendations(
    incidentId: UUID,
    limit: Limit = 50,
    cursor: str | None = None,
    sort: str | None = None,
    _=Depends(require_roles(*_READ)),
    services: Services = Depends(get_services),
):
    items, page = paginate(
        services.list_recommendations(incidentId), limit=limit, cursor=cursor, sort=sort, default_sort="-createdAt",
        getters={"createdAt": lambda r: r.createdAt},
    )
    return JSONResponse(page_body(items, page))


@router.post("/{incidentId}/recommendations")
def create_recommendation(
    incidentId: UUID,
    body: RecommendationCreate,
    idempotency_key: IdemKey,
    user: User = Depends(require_roles(R.dispatcher, R.supervisor)),
    services: Services = Depends(get_services),
):
    return _idempotent(
        services, user, f"createRecommendation:{incidentId}", idempotency_key, body,
        lambda: (201, dump(services.create_recommendation(user, incidentId, body))),
    )
