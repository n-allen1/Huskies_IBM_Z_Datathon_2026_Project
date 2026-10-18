"""Round trip of the stand-in seed through PostgreSQL. Needs a database migrated to head:
set TEST_DATABASE_URL (for example postgresql+psycopg://rm_user:...@127.0.0.1:55432/responsematch_test).
The test truncates every table in that database."""

import os

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from sqlalchemy import create_engine, text  # noqa: E402

from app.pg_resources import read_resources, write_seed
from app.seed_standin import build_seed  # noqa: E402

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def engine():
    engine = create_engine(URL)
    yield engine
    engine.dispose()


def _by(items, key):
    return sorted(items, key=key)


def test_standin_seed_round_trips(engine):
    seed = build_seed()
    with engine.begin() as conn:
        counts = write_seed(conn, seed)
        got = read_resources(conn)

    assert counts["units"] == len(seed.units) == 13
    assert _by(got.units, lambda u: u.callSign) == _by(seed.units, lambda u: u.callSign)
    assert _by(got.personnel, lambda p: p.personnelId.int) == _by(seed.personnel, lambda p: p.personnelId.int)
    assert _by(got.credentials, lambda c: c.credentialId.int) == _by(seed.credentials, lambda c: c.credentialId.int)
    assert _by(got.apparatus, lambda a: a.identifier) == _by(seed.apparatus, lambda a: a.identifier)
    assert _by(got.facilities, lambda f: f.name) == _by(seed.facilities, lambda f: f.name)
    assert _by(got.capability_definitions, lambda d: d.capabilityId) == _by(seed.capability_definitions, lambda d: d.capabilityId)


def test_crew_order_is_preserved(engine):
    seed = build_seed()
    with engine.begin() as conn:
        write_seed(conn, seed)
        got = {u.callSign: u for u in read_resources(conn).units}
    for unit in seed.units:
        assert got[unit.callSign].crewPersonnelIds == unit.crewPersonnelIds


def test_write_seed_replaces_existing_rows(engine):
    seed = build_seed()
    with engine.begin() as conn:
        write_seed(conn, seed)
        write_seed(conn, seed)
        assert conn.execute(text("SELECT count(*) FROM unit")).scalar() == len(seed.units)
