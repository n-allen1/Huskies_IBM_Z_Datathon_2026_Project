from uuid import uuid4

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

from .conftest import NOW, Api, incident_payload


def test_health_is_public_and_reports_architecture(api):
    h = api.call("getHealth", role=None, expect=200).json()
    assert h["status"] == "ok" and h["syntheticDataOnly"] is True and h["architecture"]
    assert {c["name"] for c in h["components"]} == {"database", "inference"}


def test_health_reports_down_with_503_when_the_store_is_unreachable(app):
    store = app.state.services.store
    store.ping = lambda: (_ for _ in ()).throw(RuntimeError("connection refused"))
    r = Api(TestClient(app)).call("getHealth", role=None, expect=503)
    body = r.json()
    assert body["status"] == "down"
    assert {c["name"]: c["status"] for c in body["components"]} == {"database": "down", "inference": "ok"}
    assert "connection refused" in next(c for c in body["components"] if c["name"] == "database")["detail"]


def test_current_user(api):
    me = api.call("getCurrentUser", role="supervisor", expect=200).json()
    assert me["roles"] == ["supervisor"] and me["authMode"] == "demo_adapter"
    for role in ("call_taker", "dispatcher", "hr", "fleet", "leadership", "admin"):
        api.call("getCurrentUser", role=role, expect=200)
    api.call("getCurrentUser", role=None, expect=401)


def test_units_list_filter_and_sort(api):
    everything = api.call("listUnits", query={"limit": 200}, expect=200).json()["data"]
    assert len(everything) == 13 and all(u["provenance"]["dataOrigin"] == "synthetic" for u in everything)
    ems = api.call("listUnits", query={"discipline": "ems"}, expect=200).json()["data"]
    assert {u["discipline"] for u in ems} == {"ems"}
    busy = api.call("listUnits", query={"status": ["dispatched_simulated"]}, expect=200).json()["data"]
    assert [u["callSign"] for u in busy] == ["M2"]
    desc = api.call("listUnits", query={"sort": "-callSign"}, expect=200).json()["data"]
    assert [u["callSign"] for u in desc] == sorted((u["callSign"] for u in everything), reverse=True)


def test_units_filter_by_capability_uses_current_availability(api):
    haz = api.call("listUnits", query={"capabilityId": "CAP-HAZMAT-TECH"}, expect=200).json()["data"]
    assert [u["callSign"] for u in haz] == ["HR4"]
    als = {u["callSign"] for u in api.call("listUnits", query={"capabilityId": "CAP-ALS-PARAMEDIC", "limit": 200}, expect=200).json()["data"]}
    assert "M2" not in als  # has the crew, but is busy: owning the capability is not being deployable
    assert {"M1", "M5", "E2"} <= als


def test_unit_capabilities_explain_state(api, units):
    caps = api.call("getUnitCapabilities", path={"unitId": units["E4"]["unitId"]}, expect=200).json()["capabilities"]
    by_id = {c["capabilityId"]: c for c in caps}
    assert by_id["CAP-TECH-RESCUE"]["state"] == "expired" and "expired" in by_id["CAP-TECH-RESCUE"]["evidence"]
    assert by_id["CAP-FIRE-SUPPRESSION"]["state"] == "available"
    assert by_id["CAP-HAZMAT-TECH"]["state"] == "missing"
    m5 = {c["capabilityId"]: c for c in api.call("getUnitCapabilities", path={"unitId": units["M5"]["unitId"]}, expect=200).json()["capabilities"]}
    assert m5["CAP-CARDIAC-MONITOR"]["state"] == "unavailable"
    m2 = {c["capabilityId"]: c for c in api.call("getUnitCapabilities", path={"unitId": units["M2"]["unitId"]}, expect=200).json()["capabilities"]}
    assert m2["CAP-ALS-PARAMEDIC"]["state"] == "unavailable" and "dispatched_simulated" in m2["CAP-ALS-PARAMEDIC"]["evidence"]


def test_capability_evidence_does_not_name_individuals(api, units):
    caps = api.call("getUnitCapabilities", path={"unitId": units["M1"]["unitId"]}, expect=200).json()["capabilities"]
    assert not any("Crew Member" in (c.get("evidence") or "") for c in caps)


def test_unknown_unit_is_404(api):
    api.call("getUnit", path={"unitId": uuid4()}, expect=404)
    api.call("getUnitCapabilities", path={"unitId": uuid4()}, expect=404)


def test_unit_roles(api, units):
    path = {"unitId": units["E1"]["unitId"]}
    api.call("getUnit", role="dispatcher", path=path, expect=200)
    api.call("getUnit", role="fleet", path=path, expect=200)
    api.call("getUnit", role="call_taker", path=path, expect=403)
    api.call("listUnits", role="call_taker", expect=403)


def test_facilities_and_capability_registry(api):
    stations = api.call("listFacilities", query={"type": "fire_station"}, expect=200).json()["data"]
    assert len(stations) == 4
    api.call("listFacilities", role="call_taker", expect=200)
    api.call("listFacilities", query={"type": "moon_base"}, expect=400)
    caps = api.call("listCapabilityDefinitions", expect=200).json()["data"]
    assert "CAP-HAZMAT-TECH" in {c["capabilityId"] for c in caps}
    assert all(c["requiresCredentialDefinitionIds"] for c in caps if c["kind"] == "personnel_qualification")


def test_every_capability_the_protocol_needs_is_in_the_registry():
    from app.protocol import PROTOCOL
    from app.seed_standin import build_seed
    known = {d.capabilityId for d in build_seed().capability_definitions}
    needed = {rule.capability_id for rules in PROTOCOL.values() for rule in rules}
    assert needed <= known


def test_demo_reset_restores_the_seed(api, make_incident, units):
    make_incident()
    r = api.call("resetDemoData", role="admin", expect=200).json()
    assert r["seedVersion"] and r["counts"]["units"] == 13
    assert api.call("listIncidents", expect=200).json()["data"] == []
    assert api.call("createIncident", json=incident_payload(), expect=201).json()["number"] == "INC-2026-000001"


def test_demo_reset_is_disabled_outside_demo_mode_and_for_other_roles():
    api = Api(TestClient(create_app(settings=Settings(demo_mode=False), clock=lambda: NOW)))
    assert api.call("resetDemoData", role="admin", expect=403).json()["code"] == "FORBIDDEN"
    api = Api(TestClient(create_app(settings=Settings(demo_mode=True), clock=lambda: NOW)))
    api.call("resetDemoData", role="dispatcher", expect=403)
    api.call("resetDemoData", role=None, expect=401)


def test_unhandled_errors_use_the_error_envelope(app):
    app.state.services.store.list_facilities = lambda: 1 / 0
    r = Api(TestClient(app, raise_server_exceptions=False)).call("listFacilities", expect=500)
    assert r.json()["code"] == "INTERNAL_ERROR" and "division" not in r.text
