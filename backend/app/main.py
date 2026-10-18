import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI

from . import demo_incidents
from .config import APP_VERSION, Settings
from .errors import install_error_handlers
from .embedding_classifier import EmbeddingClassifier, FallbackClassifier, GraniteEncoder
from .eta_model import XgboostEta
from .idempotency import IdempotencyCache
from .inference import Classifier, EtaEstimator, HaversineEta, KeywordClassifier
from .events import EventBus
from .routes import audit, events, incidents, recommendations, resources, system
from .seed_standin import build_seed
from .services import Services
from .store import InMemoryStore, Store


ROUTERS = (system.router, resources.router, incidents.router, recommendations.router, events.router, audit.router)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_classifier(settings: Settings) -> Classifier:
    if settings.classifier == "embedding":
        encoder = GraniteEncoder(device=settings.embedding_device, dtype=settings.embedding_dtype)
        return FallbackClassifier(
            EmbeddingClassifier(
                encoder, min_similarity=settings.embedding_min_similarity, min_margin=settings.embedding_min_margin
            ), KeywordClassifier()
        )
    return KeywordClassifier()


def _default_estimator(settings: Settings) -> EtaEstimator:
    straight_line = HaversineEta(settings.assumed_speed_kmh)
    return XgboostEta(straight_line, baseline_speed_kmh=settings.assumed_speed_kmh) if settings.eta_model == "xgboost" else straight_line


def _default_store(settings: Settings, clock: Callable[[], datetime]) -> Store:
    if settings.store == "memory":
        return InMemoryStore(build_seed, clock)
    if settings.store != "postgres":
        raise ValueError(f"STORE must be 'memory' or 'postgres', not {settings.store!r}")
    if not settings.database_url:
        raise ValueError("STORE=postgres needs DATABASE_URL, or DB_USER and DB_PASSWORD (with DB_HOST, DB_PORT, DB_NAME)")
    from .pg_store import PostgresStore  # imported here so the in-memory API does not need SQLAlchemy

    return PostgresStore(settings.database_url, build_seed, clock)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Loading the model takes seconds; do it off the request path. Until it is ready, the keyword
    # fallback answers and /health reports "degraded".
    for name, component in (("classifier", app.state.services.classifier), ("eta", app.state.services.estimator)):
        warm_up = getattr(component, "warm_up", None)
        if warm_up is not None:
            threading.Thread(target=warm_up, name=f"{name}-warm-up", daemon=True).start()
    yield


def create_app(
    *,
    settings: Settings | None = None,
    store: Store | None = None,
    classifier: Classifier | None = None,
    estimator: EtaEstimator | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> FastAPI:
    """Everything with a side effect or a model behind it is injectable, so tests and the
    DB/AI tracks can swap in their pieces without touching routes."""
    settings = settings or Settings.from_env()
    # Contract-first: contracts/openapi.json is the only spec. FastAPI's generated spec and
    # docs UI are disabled so a second, drifting description of the API cannot exist.
    app = FastAPI(title="DispatchZ API", version=APP_VERSION, openapi_url=None, docs_url=None, redoc_url=None, lifespan=_lifespan)
    install_error_handlers(app)
    app.state.services = Services(
        store=store or _default_store(settings, clock),
        settings=settings,
        classifier=classifier or _default_classifier(settings),
        estimator=estimator or _default_estimator(settings),
        clock=clock,
        idempotency=IdempotencyCache(),
        events=EventBus(clock=clock),
    )
    if settings.seed_incidents:
        demo_incidents.seed(app.state.services)
    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()
