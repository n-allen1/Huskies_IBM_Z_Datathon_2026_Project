import platform
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..auth import current_user, require_roles
from ..config import APP_VERSION
from ..errors import ApiError
from ..http import dump, get_services, ok
from ..models import Component, DemoResetResult, ErrorCode, Health, HealthStatus, Role, User
from ..services import Services

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def get_health(services: Services = Depends(get_services)):
    try:
        services.store.ping()
        database = Component(name="database", status=HealthStatus.ok, detail=type(services.store).__name__)
    except Exception as exc:  # health must report, not raise
        database = Component(name="database", status=HealthStatus.down, detail=str(exc)[:200])
    def describe(component) -> tuple[str, str]:
        fn = getattr(component, "describe", None)
        return fn() if fn else ("ok", type(component).__name__)

    clf_status, clf_detail = describe(services.classifier)
    eta_status, eta_detail = describe(services.estimator)
    inference = Component(
        name="inference",
        status=HealthStatus.ok if clf_status == eta_status == "ok" else HealthStatus.degraded,
        detail=f"classifier: {clf_detail}; eta: {eta_detail}",
    )
    down = database.status == HealthStatus.down
    body = Health(
        status=HealthStatus.down if down else (HealthStatus.degraded if inference.status != HealthStatus.ok else HealthStatus.ok),
        version=APP_VERSION, time=datetime.now(timezone.utc),
        components=[database, inference], architecture=platform.machine(), syntheticDataOnly=True,
    )
    return JSONResponse(dump(body), status_code=503 if down else 200)


@router.get("/auth/me")
def get_current_user(user: User = Depends(require_roles(*Role))):
    return ok(user)


@router.post("/admin/demo/reset")
def reset_demo_data(
    user: User = Depends(require_roles(Role.system_admin)), services: Services = Depends(get_services)
):
    if not services.settings.demo_mode:
        raise ApiError(ErrorCode.FORBIDDEN, "Demo reset is disabled; set DEMO_MODE=true to enable it.")
    counts = services.reset_demo(user)
    return ok(DemoResetResult(
        resetAt=services.clock(), seedVersion=services.store.seed_version, seed=services.store.seed, counts=counts,
    ))
