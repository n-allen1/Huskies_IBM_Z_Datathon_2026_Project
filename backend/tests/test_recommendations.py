"""Hard eligibility, ranking and the recommendation endpoints against the stand-in seed.

Seed facts these tests rely on (see seed_standin.py): M2 is busy; M4's only EMT credentials are
one expired and one unverified; M5's cardiac monitor is in maintenance; E4's technical-rescue
credential is expired; HR4 is the only unit with a valid hazmat technician and tech-rescue crew."""

from uuid import UUID

import pytest

from app.inference import InferenceUnavailable
from app.models import IncidentCategory, UnitStatus
from app.main import create_app
from fastapi.testclient import TestClient

from .conftest import NOW, Api, incident_payload


def recommend(api, incident, **body):
    return api.call("createRecommendation", path={"incidentId": incident["incidentId"]}, json=body, expect=201).json()


def by_sign(rec):
    return {c["callSign"]: c for c in rec["candidates"]}


def set_status(api, unit_id, status):
    """Test-only: flip a unit's status directly in the store (status events are a Phase 2 endpoint)."""
    store = api.client.app.state.services.store
    unit_id = UUID(unit_id)
    unit = store.get_unit(unit_id)
    store.save_unit(unit.model_copy(update={"status": UnitStatus(status)}), unit.version or 1)


# --- the safety invariant ----------------------------------------------------

@pytest.mark.parametrize("category", [c.value for c in IncidentCategory if c.value != "other"])
def test_ineligible_units_are_never_ranked(api, make_incident, category):
    rec = recommend(api, make_incident(confirm=category), maxCandidates=50)
    for c in rec["candidates"]:
        if not c["eligible"]:
            assert "rank" not in c
            assert c["ineligibleReasons"], f"{c['callSign']} is ineligible without a stated reason"
        else:
            assert c["rank"] >= 1 and "ineligibleReasons" not in c
    ranks = sorted(c["rank"] for c in rec["candidates"] if c["eligible"])
    assert ranks == list(range(1, len(ranks) + 1))
    assert rec["humanConfirmationRequired"] is True


def test_every_candidate_lists_a_check_for_every_requirement(api, make_incident):
    inc = make_incident(confirm="medical_cardiac")
    reqs = api.call("getIncidentRequirements", path={"incidentId": inc["incidentId"]}, expect=200).json()
    rec = recommend(api, inc, maxCandidates=50)
    wanted = {r["capabilityId"] for r in reqs["requirements"]}
    assert wanted
    for c in rec["candidates"]:
        assert {k["capabilityId"] for k in c["checks"]} == wanted


# --- scenario A: immediate response -----------------------------------------

def test_cardiac_call_ranks_available_als_ambulances_by_eta(api, make_incident):
    rec = recommend(api, make_incident(confirm="medical_cardiac"), maxCandidates=50)
    candidates = by_sign(rec)
    assert candidates["M1"]["eligible"] and candidates["M1"]["rank"] is not None
    assert candidates["M1"]["etaMethod"] == "straight_line_fallback"
    eligible = [c for c in rec["candidates"] if c["eligible"]]
    etas = [c["etaSeconds"] for c in sorted(eligible, key=lambda c: c["rank"])]
    assert etas == sorted(etas)
    assert rec["packages"][0]["satisfiesAllMandatory"] is True
    assert rec["status"] == "pending_review"


def test_reasons_are_specific_for_each_way_a_unit_can_be_blocked(api, make_incident):
    cardiac = by_sign(recommend(api, make_incident(confirm="medical_cardiac"), maxCandidates=50))
    assert any("dispatched_simulated" in r for r in cardiac["M2"]["ineligibleReasons"])  # busy
    assert any("CAP-CARDIAC-MONITOR" in r and "maintenance" in r for r in cardiac["M5"]["ineligibleReasons"])

    other = by_sign(recommend(api, make_incident(confirm="medical_other"), maxCandidates=50))
    assert not other["M4"]["eligible"]
    assert any("unverified" in r or "expired" in r for r in other["M4"]["ineligibleReasons"])


def test_ambulance_with_no_valid_crew_credentials_is_never_admitted_as_transport_only(api, make_incident):
    # M4 owns a working ambulance but its only EMT credentials are expired/unverified.
    for category in ("medical_cardiac", "medical_trauma", "medical_other", "traffic_collision"):
        m4 = by_sign(recommend(api, make_incident(confirm=category), maxCandidates=50))["M4"]
        assert not m4["eligible"] and "rank" not in m4, category
        assert any("CAP-BLS-EMT" in r for r in m4["ineligibleReasons"]), category


def test_unverified_credentials_are_not_accepted(api, make_incident):
    checks = {c["capabilityId"]: c for c in by_sign(recommend(api, make_incident(confirm="medical_other"), maxCandidates=50))["M4"]["checks"]}
    # M4 holds one expired and one unverified EMT credential; unverified is the closest-to-qualifying state.
    assert checks["CAP-BLS-EMT"]["state"] == "unverified"


def test_expired_credential_blocks_that_capability_only(api, make_incident):
    rec = recommend(api, make_incident(confirm="technical_rescue"), maxCandidates=50)
    e4 = {c["capabilityId"]: c for c in by_sign(rec)["E4"]["checks"]}
    assert e4["CAP-TECH-RESCUE"]["state"] == "expired"
    assert by_sign(rec)["HR4"]["eligible"]
    assert rec["packages"][0]["satisfiesAllMandatory"] is True


# --- scenario B: loss of the only specialized unit ---------------------------

def test_losing_the_only_hazmat_unit_is_reported_not_hidden(api, make_incident, units):
    inc = make_incident(confirm="hazmat")
    before = recommend(api, inc, maxCandidates=50)
    assert by_sign(before)["HR4"]["eligible"]
    assert before["packages"][0]["satisfiesAllMandatory"] is True

    set_status(api, units["HR4"]["unitId"], "out_of_service")
    after = recommend(api, inc, maxCandidates=50)
    hr4 = by_sign(after)["HR4"]
    assert not hr4["eligible"] and "rank" not in hr4
    assert any("out_of_service" in r for r in hr4["ineligibleReasons"])
    assert not any(c["eligible"] for c in after["candidates"])  # no other unit carries a mandatory hazmat capability
    assert "packages" not in after  # nothing eligible to package; the gap is stated in limitations
    assert any("CAP-HAZMAT-TECH" in line for line in after["limitations"])
    # the earlier recommendation is no longer actionable
    old = api.call("getRecommendation", path={"recommendationId": before["recommendationId"]}, expect=200).json()
    assert old["status"] == "invalidated"


def test_unit_with_too_few_active_crew_is_ineligible(api, make_incident, units):
    store = api.client.app.state.services.store
    m1 = store.get_unit(UUID(units["M1"]["unitId"]))
    store.save_unit(m1.model_copy(update={"minimumCrew": 5}), m1.version or 1)
    cands = by_sign(recommend(api, make_incident(confirm="medical_cardiac"), maxCandidates=50))
    assert not cands["M1"]["eligible"]
    assert any("Staffed 2 of 5" in r for r in cands["M1"]["ineligibleReasons"])


def test_excluded_units_stay_visible_but_ineligible(api, make_incident, units):
    rec = recommend(api, make_incident(confirm="medical_cardiac"), excludeUnitIds=[units["M1"]["unitId"]], maxCandidates=50)
    m1 = by_sign(rec)["M1"]
    assert not m1["eligible"] and any("Excluded" in r for r in m1["ineligibleReasons"])


# --- lifecycle and errors ----------------------------------------------------

def test_recommendation_without_a_category_is_rejected(api, make_incident):
    inc = make_incident()
    r = api.call("createRecommendation", path={"incidentId": inc["incidentId"]}, json={}, expect=409)
    assert r.json()["code"] == "CONFLICT"


def test_reported_but_unconfirmed_category_is_used_with_a_limitation(api, make_incident):
    rec = recommend(api, make_incident(category="structure_fire"))
    assert any("not been confirmed" in line for line in rec["limitations"])


def test_ml_suggestion_alone_never_drives_requirements(api, make_incident):
    inc = make_incident()
    api.call("createClassification", path={"incidentId": inc["incidentId"]}, json={"mode": "infer"}, expect=201)
    reqs = api.call("getIncidentRequirements", path={"incidentId": inc["incidentId"]}, expect=200).json()
    assert reqs["requirements"] == []
    api.call("createRecommendation", path={"incidentId": inc["incidentId"]}, json={}, expect=409)


def test_new_recommendation_supersedes_pending_and_moves_incident_forward(api, make_incident):
    inc = make_incident(confirm="structure_fire")
    first = recommend(api, inc)
    second = recommend(api, inc)
    listed = api.call("listIncidentRecommendations", path={"incidentId": inc["incidentId"]}, expect=200).json()
    statuses = {r["recommendationId"]: r["status"] for r in listed["data"]}
    assert statuses[first["recommendationId"]] == "invalidated"
    assert statuses[second["recommendationId"]] == "pending_review"
    assert api.call("getIncident", path={"incidentId": inc["incidentId"]}, expect=200).json()["status"] == "recommendation_ready"


def test_changing_the_category_invalidates_pending_recommendations(api, make_incident):
    inc = make_incident(confirm="structure_fire")
    rec = recommend(api, inc)
    api.call("createClassification", path={"incidentId": inc["incidentId"]}, json={"mode": "confirm", "category": "hazmat"}, expect=201)
    got = api.call("getRecommendation", path={"recommendationId": rec["recommendationId"]}, expect=200).json()
    assert got["status"] == "invalidated" and "category" in got["invalidatedReason"]


def test_recommendation_expires(settings):
    clock = {"now": NOW}
    api = Api(TestClient(create_app(settings=settings, clock=lambda: clock["now"])))
    inc = api.call("createIncident", json=incident_payload(), expect=201).json()
    api.call("createClassification", path={"incidentId": inc["incidentId"]}, json={"mode": "confirm", "category": "vehicle_fire"}, expect=201)
    rec = api.call("createRecommendation", path={"incidentId": inc["incidentId"]}, json={}, expect=201).json()
    from datetime import timedelta
    clock["now"] = NOW + timedelta(seconds=settings.recommendation_ttl_seconds + 1)
    assert api.call("getRecommendation", path={"recommendationId": rec["recommendationId"]}, expect=200).json()["status"] == "expired"


def test_recommendation_is_idempotent_and_detects_key_reuse(api, make_incident):
    inc = make_incident(confirm="vehicle_fire")
    path = {"incidentId": inc["incidentId"]}
    a = api.call("createRecommendation", path=path, json={}, key="same-key-123", expect=201).json()
    b = api.call("createRecommendation", path=path, json={}, key="same-key-123", expect=201).json()
    assert a["recommendationId"] == b["recommendationId"]
    clash = api.call("createRecommendation", path=path, json={"maxCandidates": 3}, key="same-key-123", expect=409)
    assert clash.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_recommendation_requires_idempotency_key(api, make_incident):
    inc = make_incident(confirm="vehicle_fire")
    api.call("createRecommendation", path={"incidentId": inc["incidentId"]}, json={}, key=None, expect=400)


def test_closed_incident_gets_no_recommendation(api, make_incident):
    inc = make_incident(confirm="vehicle_fire")
    api.call("updateIncident", path={"incidentId": inc["incidentId"]}, json={"version": inc["version"], "status": "cancelled"}, expect=200)
    r = api.call("createRecommendation", path={"incidentId": inc["incidentId"]}, json={}, expect=409)
    assert r.json()["code"] == "INVALID_STATE_TRANSITION"


def test_only_dispatchers_and_supervisors_generate_recommendations(api, make_incident):
    inc = make_incident(confirm="vehicle_fire")
    for role in ("call_taker", "admin", "hr", "leadership"):
        api.call("createRecommendation", role=role, path={"incidentId": inc["incidentId"]}, json={}, expect=403)
    api.call("createRecommendation", role="supervisor", path={"incidentId": inc["incidentId"]}, json={}, expect=201)


def test_a_failing_classifier_does_not_affect_recommendations(settings):
    class Broken:
        def classify(self, text):
            raise InferenceUnavailable("model not loaded")

    api = Api(TestClient(create_app(settings=settings, classifier=Broken(), clock=lambda: NOW)))
    inc = api.call("createIncident", json=incident_payload(), expect=201).json()
    path = {"incidentId": inc["incidentId"]}
    err = api.call("createClassification", path=path, json={"mode": "infer"}, expect=503)
    assert err.json()["code"] == "INFERENCE_UNAVAILABLE"
    api.call("createClassification", path=path, json={"mode": "confirm", "category": "structure_fire"}, expect=201)
    api.call("createRecommendation", path=path, json={}, expect=201)
