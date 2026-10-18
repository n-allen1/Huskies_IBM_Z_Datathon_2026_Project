"""PostgreSQL implementation of `Store` (schema: database/alembic migrations 0001-0003).

Reference data lives in the relational tables from 0001/0002 (see pg_resources.py); what the
API writes at run time is kept in the `api_*` tables from 0003, each holding the full contract
model as JSON next to the columns used for lookup. Every method runs in its own transaction,
as the in-memory store's methods are each atomic; optimistic concurrency uses the version
column exactly as `InMemoryStore` does.

Unlike `InMemoryStore`, construction does not wipe anything: the seed is loaded only into a
database that has no units yet, so data survives an API restart. `reset()` is the demo reset.
"""

import json
from datetime import datetime
from typing import Callable, Type, TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError

from . import pg_resources
from .models import (
    AuditEvent,
    CapabilityDefinition,
    Classification,
    Facility,
    Incident,
    IncidentCategory,
    Recommendation,
    RecommendationReview,
    Unit,
    UnitStatusEvent,
)
from .store import IncidentExtra, ResourceSnapshot, VersionConflict, _Seed

M = TypeVar("M", bound=BaseModel)

# The Alembic revision this code was written against; start-up refuses any other schema.
SCHEMA_REVISION = "0003_api_transactional_store"

# Truncating these (and the sequence) returns the run-time data to empty. The resource tables are
# replaced by pg_resources.write_seed.
_RUNTIME_TABLES = "api_incident, api_audit_event"


def normalize_url(url: str) -> str:
    """Accept the usual postgres:// and postgresql:// spellings; the driver is psycopg 3."""
    parsed = make_url(url)
    if parsed.drivername in ("postgres", "postgresql"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


def _payload(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"))


class PostgresStore:
    def __init__(self, database_url: str, load_seed: Callable[[], _Seed], clock: Callable[[], datetime]):
        self._load_seed = load_seed
        self._clock = clock
        self._engine = create_engine(
            normalize_url(database_url), pool_size=10, max_overflow=20, pool_pre_ping=True, future=True,
            connect_args={"connect_timeout": 5},
        )
        self.seed_version, self.seed = "unseeded", None
        try:
            with self._engine.begin() as conn:
                self._check_schema(conn)
                if conn.execute(text("SELECT count(*) FROM unit")).scalar() == 0:
                    self._seed(conn)
                self._read_meta(conn)
        except OperationalError as exc:
            self._engine.dispose()
            target = make_url(normalize_url(database_url))
            raise RuntimeError(
                f"Cannot reach PostgreSQL at {target.host}:{target.port}/{target.database} as {target.username}: "
                f"{str(exc.orig).strip().splitlines()[0]}"
            ) from None

    @staticmethod
    def _check_schema(conn: Connection) -> None:
        try:
            found = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except ProgrammingError:
            found = None
        if found != SCHEMA_REVISION:
            raise RuntimeError(
                f"The database schema is at {found or 'no migration'}, but this API needs {SCHEMA_REVISION}. "
                "Run `alembic upgrade head` in database/ (see docs/development.md)."
            )

    def dispose(self) -> None:
        self._engine.dispose()

    # -- lifecycle -----------------------------------------------------------
    def ping(self) -> None:
        with self._engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def reset(self) -> dict[str, int]:
        with self._engine.begin() as conn:
            counts = self._seed(conn)
            self._read_meta(conn)
            return counts

    def _seed(self, conn: Connection) -> dict[str, int]:
        s = self._load_seed()
        conn.execute(text(f"TRUNCATE {_RUNTIME_TABLES} RESTART IDENTITY CASCADE"))
        conn.execute(text("ALTER SEQUENCE api_incident_number_seq RESTART"))
        counts = pg_resources.write_seed(conn, s)
        conn.execute(text("DELETE FROM api_meta"))
        conn.execute(
            text("INSERT INTO api_meta (key, value) VALUES ('seed_version', :v), ('seed', :s)"),
            {"v": s.version, "s": "" if s.seed is None else str(s.seed)},
        )
        return counts

    def _read_meta(self, conn: Connection) -> None:
        meta = dict(conn.execute(text("SELECT key, value FROM api_meta")).all())
        # A database seeded some other way (for example database/seed.py) has resources but no meta row.
        self.seed_version = meta.get("seed_version", "external")
        self.seed = int(meta["seed"]) if meta.get("seed") else None

    # -- helpers -------------------------------------------------------------
    def _one(self, model: Type[M], sql: str, **params) -> M | None:
        with self._engine.connect() as conn:
            payload = conn.execute(text(sql), params).scalar()
        return model.model_validate(payload) if payload is not None else None

    def _all(self, model: Type[M], sql: str, **params) -> list[M]:
        with self._engine.connect() as conn:
            return [model.model_validate(p) for p in conn.execute(text(sql), params).scalars()]

    # -- resources -----------------------------------------------------------
    def snapshot(self) -> ResourceSnapshot:
        # One consistent view across the six reads, as the in-memory store's lock gives.
        with self._engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            taken_at = self._clock()
            r = pg_resources.read_resources(conn)
        return ResourceSnapshot(
            taken_at=taken_at, units=r.units, personnel=r.personnel, credentials=r.credentials,
            apparatus=r.apparatus, facilities=r.facilities, capability_definitions=r.capability_definitions,
        )

    def get_unit(self, unit_id: UUID) -> Unit | None:
        with self._engine.connect() as conn:
            units = pg_resources.read_units(conn, unit_id)
        return units[0] if units else None

    def list_units(self) -> list[Unit]:
        with self._engine.connect() as conn:
            return pg_resources.read_units(conn)

    def save_unit(self, unit: Unit, expected_version: int) -> Unit:
        with self._engine.begin() as conn:
            if not pg_resources.save_unit_row(conn, unit, expected_version):
                if conn.execute(text("SELECT 1 FROM unit WHERE id = :id"), {"id": unit.unitId}).first() is None:
                    raise KeyError(unit.unitId)
                raise VersionConflict()
        return unit

    def add_status_event(self, event: UnitStatusEvent) -> UnitStatusEvent:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO api_unit_status_event (id, unit_id, recorded_at, payload)"
                    " VALUES (:id, :unit, :at, CAST(:payload AS jsonb))"
                ),
                {"id": event.statusEventId, "unit": event.unitId, "at": event.recordedAt, "payload": _payload(event)},
            )
        return event

    def list_facilities(self) -> list[Facility]:
        with self._engine.connect() as conn:
            return pg_resources.read_facilities(conn)

    def list_capability_definitions(self) -> list[CapabilityDefinition]:
        with self._engine.connect() as conn:
            return pg_resources.read_capability_definitions(conn)

    # -- incidents -----------------------------------------------------------
    def next_incident_number(self, year: int) -> str:
        with self._engine.begin() as conn:
            n = conn.execute(text("SELECT nextval('api_incident_number_seq')")).scalar()
        return f"INC-{year}-{n:06d}"

    def add_incident(self, incident: Incident, extra: IncidentExtra) -> Incident:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO api_incident (id, tenant_id, number, status, priority, version, reported_category,"
                    " mock_scenario_id, created_at, updated_at, payload)"
                    " VALUES (:id, :tenant, :number, :status, :priority, :version, :category, :scenario, :created,"
                    " :updated, CAST(:payload AS jsonb))"
                ),
                {
                    "id": incident.incidentId, "tenant": incident.tenantId, "number": incident.number,
                    "status": incident.status.value, "priority": incident.priority.value, "version": incident.version,
                    "category": extra.reported_category.value if extra.reported_category else None,
                    "scenario": extra.mock_scenario_id, "created": incident.createdAt, "updated": incident.updatedAt,
                    "payload": _payload(incident),
                },
            )
        return incident

    def get_incident(self, incident_id: UUID) -> Incident | None:
        return self._one(Incident, "SELECT payload FROM api_incident WHERE id = :id", id=incident_id)

    def get_incident_extra(self, incident_id: UUID) -> IncidentExtra:
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT reported_category, mock_scenario_id FROM api_incident WHERE id = :id"), {"id": incident_id}
            ).first()
        if row is None:
            return IncidentExtra()
        return IncidentExtra(
            reported_category=IncidentCategory(row[0]) if row[0] else None, mock_scenario_id=row[1]
        )

    def list_incidents(self) -> list[Incident]:
        return self._all(Incident, "SELECT payload FROM api_incident ORDER BY seq")

    def save_incident(self, incident: Incident, expected_version: int) -> Incident:
        with self._engine.begin() as conn:
            updated = conn.execute(
                text(
                    "UPDATE api_incident SET status = :status, priority = :priority, version = :version,"
                    " updated_at = :updated, payload = CAST(:payload AS jsonb)"
                    " WHERE id = :id AND version = :expected"
                ),
                {
                    "id": incident.incidentId, "status": incident.status.value, "priority": incident.priority.value,
                    "version": incident.version, "updated": incident.updatedAt, "payload": _payload(incident),
                    "expected": expected_version,
                },
            ).rowcount
            if not updated:
                if conn.execute(text("SELECT 1 FROM api_incident WHERE id = :id"), {"id": incident.incidentId}).first() is None:
                    raise KeyError(incident.incidentId)
                raise VersionConflict()
        return incident

    # -- classifications -----------------------------------------------------
    def add_classification(self, c: Classification) -> Classification:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO api_classification (id, incident_id, status, created_at, payload)"
                    " VALUES (:id, :incident, :status, :created, CAST(:payload AS jsonb))"
                    " ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, payload = EXCLUDED.payload"
                ),
                {
                    "id": c.classificationId, "incident": c.incidentId, "status": c.status.value,
                    "created": c.createdAt, "payload": _payload(c),
                },
            )
        return c

    save_classification = add_classification

    def get_classification(self, classification_id: UUID) -> Classification | None:
        return self._one(Classification, "SELECT payload FROM api_classification WHERE id = :id", id=classification_id)

    def list_classifications(self, incident_id: UUID) -> list[Classification]:
        return self._all(
            Classification, "SELECT payload FROM api_classification WHERE incident_id = :id ORDER BY seq", id=incident_id
        )

    # -- recommendations -----------------------------------------------------
    def add_recommendation(self, r: Recommendation) -> Recommendation:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO api_recommendation (id, incident_id, status, created_at, payload)"
                    " VALUES (:id, :incident, :status, :created, CAST(:payload AS jsonb))"
                    " ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, payload = EXCLUDED.payload"
                ),
                {
                    "id": r.recommendationId, "incident": r.incidentId, "status": r.status.value,
                    "created": r.createdAt, "payload": _payload(r),
                },
            )
        return r

    save_recommendation = add_recommendation

    def get_recommendation(self, recommendation_id: UUID) -> Recommendation | None:
        return self._one(Recommendation, "SELECT payload FROM api_recommendation WHERE id = :id", id=recommendation_id)

    def list_recommendations(self, incident_id: UUID) -> list[Recommendation]:
        return self._all(
            Recommendation, "SELECT payload FROM api_recommendation WHERE incident_id = :id ORDER BY seq", id=incident_id
        )

    def list_all_recommendations(self) -> list[Recommendation]:
        return self._all(Recommendation, "SELECT payload FROM api_recommendation ORDER BY seq")

    # -- reviews and audit -----------------------------------------------------
    def add_review(self, review: RecommendationReview) -> RecommendationReview:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO api_review (id, recommendation_id, created_at, payload)"
                    " VALUES (:id, :recommendation, :created, CAST(:payload AS jsonb))"
                ),
                {
                    "id": review.reviewId, "recommendation": review.recommendationId, "created": review.createdAt,
                    "payload": _payload(review),
                },
            )
        return review

    def list_reviews(self, recommendation_id: UUID) -> list[RecommendationReview]:
        return self._all(
            RecommendationReview, "SELECT payload FROM api_review WHERE recommendation_id = :id ORDER BY seq",
            id=recommendation_id,
        )

    def add_audit_event(self, event: AuditEvent) -> AuditEvent:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO api_audit_event (id, tenant_id, occurred_at, action, payload)"
                    " VALUES (:id, :tenant, :at, :action, CAST(:payload AS jsonb))"
                ),
                {
                    "id": event.auditId, "tenant": event.tenantId, "at": event.occurredAt, "action": event.action,
                    "payload": _payload(event),
                },
            )
        return event

    def list_audit_events(self) -> list[AuditEvent]:
        return self._all(AuditEvent, "SELECT payload FROM api_audit_event ORDER BY seq")
