"""SEED_INCIDENTS: the synthetic demo incidents are there on start-up and after every demo reset, never duplicated."""

import math

import pytest

from app import demo_incidents
from app.config import Settings

STATION_1 = (39.100, -95.700)


def km_from_station_1(point: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*STATION_1, point["lat"], point["lon"]))
    a = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def incidents(api) -> list[dict]:
    return api.call("listIncidents", query={"limit": 100}, expect=200).json()["data"]


@pytest.fixture
def settings():
    return Settings(demo_mode=True, seed_incidents=True)


def test_demo_incidents_are_seeded_categorized_and_geocoded(api):
    seeded = incidents(api)
    assert len(seeded) == len(demo_incidents.load()) > 0
    for inc in seeded:
        assert inc["confirmedCategory"] and inc["status"] == "pending_dispatch"
        assert inc["location"]["geocodeConfidence"] == 0.9
        assert km_from_station_1(inc["location"]["point"]) < 25


def test_demo_reset_restores_exactly_the_demo_incidents(api, make_incident):
    expected = len(incidents(api))
    make_incident()
    api.call("resetDemoData", role="admin", expect=200)
    assert len(incidents(api)) == expected


def test_seeding_a_store_that_already_has_incidents_adds_nothing(app):
    assert demo_incidents.seed(app.state.services) == 0


def test_a_seeded_incident_gets_a_recommendation(api):
    cardiac = next(i for i in incidents(api) if i["confirmedCategory"] == "medical_cardiac")
    rec = api.call("createRecommendation", path={"incidentId": cardiac["incidentId"]}, json={}, expect=201).json()
    assert rec["packages"] and rec["packages"][0]["satisfiesAllMandatory"]


def test_nothing_is_seeded_unless_enabled():
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import Api

    assert incidents(Api(TestClient(create_app(settings=Settings(demo_mode=True))))) == []
