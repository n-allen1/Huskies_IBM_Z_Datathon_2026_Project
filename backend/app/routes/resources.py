from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from .. import eligibility
from ..auth import require_roles
from ..errors import ApiError
from ..http import IdemKey, dump, get_services, idempotent, ok, page_body
from ..models import (
    ErrorCode, FacilityType, Role, UnitCapabilities, UnitDiscipline, UnitStatus, UnitStatusEventCreate, User,
)
from ..pagination import paginate
from ..services import Services

router = APIRouter(prefix="/api/v1/resources")

_UNIT_READERS = (
    Role.dispatcher, Role.supervisor, Role.fleet_equipment_admin, Role.hr_training_admin,
    Role.department_leadership, Role.system_admin,
)
_REGISTRY_READERS = (
    Role.call_taker, Role.dispatcher, Role.supervisor, Role.hr_training_admin,
    Role.fleet_equipment_admin, Role.department_leadership, Role.system_admin,
)

Limit = Annotated[int, Query(ge=1, le=200)]


def _unit(services: Services, unit_id: UUID):
    unit = services.store.get_unit(unit_id)
    if unit is None:
        raise ApiError(ErrorCode.NOT_FOUND, "Unit not found.")
    return unit


@router.get("/units")
def list_units(
    status: list[UnitStatus] | None = Query(None),
    discipline: UnitDiscipline | None = None,
    agencyId: UUID | None = None,
    stationId: UUID | None = None,
    capabilityId: str | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
    sort: str | None = None,
    _=Depends(require_roles(*_UNIT_READERS)),
    services: Services = Depends(get_services),
):
    units = services.store.list_units()
    if status:
        units = [u for u in units if u.status in status]
    if discipline:
        units = [u for u in units if u.discipline == discipline]
    if agencyId:
        units = [u for u in units if u.agencyId == agencyId]
    if stationId:
        units = [u for u in units if u.stationId == stationId]
    if capabilityId:
        snap = services.store.snapshot()
        views = eligibility.build_views(snap)
        now = services.clock()

        def has(unit) -> bool:
            caps = eligibility.unit_capabilities(views[unit.unitId], snap.capability_definitions, now)
            return any(c.capabilityId == capabilityId and c.state.value == "available" for c in caps)

        units = [u for u in units if has(u)]
    items, page = paginate(
        units, limit=limit, cursor=cursor, sort=sort, default_sort="callSign",
        getters={
            "callSign": lambda u: u.callSign, "status": lambda u: u.status.value,
            "unitType": lambda u: u.unitType, "discipline": lambda u: u.discipline.value,
        },
    )
    return JSONResponse(page_body(items, page))


@router.get("/units/{unitId}")
def get_unit(unitId: UUID, _=Depends(require_roles(*_UNIT_READERS)), services: Services = Depends(get_services)):
    return ok(_unit(services, unitId))


@router.get("/units/{unitId}/capabilities")
def get_unit_capabilities(
    unitId: UUID, _=Depends(require_roles(*_UNIT_READERS)), services: Services = Depends(get_services)
):
    _unit(services, unitId)
    snap = services.store.snapshot()
    view = eligibility.build_views(snap)[unitId]
    return ok(UnitCapabilities(
        unitId=unitId, evaluatedAt=snap.taken_at,
        capabilities=eligibility.unit_capabilities(view, snap.capability_definitions, snap.taken_at),
    ))


@router.get("/facilities")
def list_facilities(
    type: FacilityType | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
    sort: str | None = None,
    _=Depends(require_roles(*_REGISTRY_READERS)),
    services: Services = Depends(get_services),
):
    facilities = services.store.list_facilities()
    if type:
        facilities = [f for f in facilities if f.type == type]
    items, page = paginate(
        facilities, limit=limit, cursor=cursor, sort=sort, default_sort="name",
        getters={"name": lambda f: f.name, "type": lambda f: f.type.value},
    )
    return JSONResponse(page_body(items, page))


@router.get("/capabilities")
def list_capability_definitions(
    limit: Limit = 50,
    cursor: str | None = None,
    sort: str | None = None,
    _=Depends(require_roles(*_REGISTRY_READERS)),
    services: Services = Depends(get_services),
):
    items, page = paginate(
        services.store.list_capability_definitions(), limit=limit, cursor=cursor, sort=sort, default_sort="capabilityId",
        getters={"capabilityId": lambda d: d.capabilityId, "label": lambda d: d.label, "kind": lambda d: d.kind.value},
    )
    return JSONResponse(page_body(items, page))


@router.post("/units/{unitId}/status-events")
def create_unit_status_event(
    unitId: UUID,
    body: UnitStatusEventCreate,
    idempotency_key: IdemKey,
    user: User = Depends(require_roles(Role.dispatcher, Role.supervisor, Role.fleet_equipment_admin, Role.system_admin)),
    services: Services = Depends(get_services),
):
    return idempotent(
        services, user, f"createUnitStatusEvent:{unitId}", idempotency_key, body,
        lambda: (201, dump(services.record_unit_status(user, unitId, body))),
    )
