"""PostgresStore behavior the shared API tests cannot show: data surviving a restart, optimistic
concurrency at the database, and the seed being loaded only into an empty database.
Needs TEST_DATABASE_URL (a database migrated to head); truncates its tables."""

import os
from datetime import datetime, timezone
from uuid import UUID

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.pg_store import PostgresStore  # noqa: E402
from app.seed_standin import build_seed  # noqa: E402
from app.store import VersionConflict  # noqa: E402
from tests.conftest import Api, incident_payload  # noqa: E402

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    s = PostgresStore(URL, build_seed, lambda: NOW)
    s.reset()
    yield s
    s.dispose()


def _api(store) -> Api:
    app = create_app(settings=Settings(demo_mode=True), store=store, clock=lambda: NOW)
    return Api(TestClient(app, raise_server_exceptions=False))


def test_data_survives_a_restart(store):
    inc = _api(store).call("createIncident", json=incident_payload(), expect=201).json()

    restarted = PostgresStore(URL, build_seed, lambda: NOW)
    try:
        got = _api(restarted).call("getIncident", path={"incidentId": inc["incidentId"]}, expect=200).json()
        assert got == inc
        assert len(restarted.list_units()) == 13
        # The incident counter continues instead of starting over.
        assert restarted.next_incident_number(2026) == "INC-2026-000002"
    finally:
        restarted.dispose()


def test_start_up_does_not_reseed_a_populated_database(store):
    unit = store.list_units()[0]
    store.save_unit(unit.model_copy(update={"minimumCrew": 7}), unit.version or 1)

    restarted = PostgresStore(URL, build_seed, lambda: NOW)
    try:
        assert restarted.get_unit(unit.unitId).minimumCrew == 7
        assert restarted.seed_version == build_seed().version
    finally:
        restarted.dispose()


def test_stale_unit_version_is_rejected(store):
    unit = store.list_units()[0]
    store.save_unit(unit.model_copy(update={"version": 2}), 1)
    with pytest.raises(VersionConflict):
        store.save_unit(unit.model_copy(update={"version": 2}), 1)


def test_stale_incident_version_is_rejected(store):
    api = _api(store)
    inc = api.call("createIncident", json=incident_payload(), expect=201).json()
    stored = store.get_incident(UUID(inc["incidentId"]))
    store.save_incident(stored.model_copy(update={"version": 2}), 1)
    with pytest.raises(VersionConflict):
        store.save_incident(stored.model_copy(update={"version": 2}), 1)


def test_reset_clears_run_time_data_and_restarts_numbering(store):
    api = _api(store)
    api.call("createIncident", json=incident_payload(), expect=201)
    store.reset()
    assert store.list_incidents() == []
    assert api.call("createIncident", json=incident_payload(), expect=201).json()["number"] == "INC-2026-000001"


def test_start_up_refuses_a_schema_that_is_not_at_the_expected_revision(store):
    from sqlalchemy import create_engine, text

    engine = create_engine(URL.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = '0002_api_resource_model'"))
    try:
        with pytest.raises(RuntimeError, match="alembic upgrade head"):
            PostgresStore(URL, build_seed, lambda: NOW)
    finally:
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = '0003_api_transactional_store'"))
        engine.dispose()


def test_unreachable_database_is_reported_by_address():
    with pytest.raises(RuntimeError, match="Cannot reach PostgreSQL at 127.0.0.1:1/"):
        PostgresStore("postgresql://nobody:x@127.0.0.1:1/none", build_seed, lambda: NOW)
