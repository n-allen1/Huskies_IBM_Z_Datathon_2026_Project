"""POST /incidents/{id}/recommendations with scope multi_incident, through the HTTP API against the stand-in seed.

Seed facts these tests rely on (see seed_standin.py): HR4 is the only unit with a valid hazmat technician and technical-rescue
crew, so a hazmat incident and a technical-rescue incident contest it. Every response is validated against the contract's schemas
by the `api` fixture, so these also prove the joint recommendation conforms to the approved contract."""

import importlib.util

import pytest

from app import multi_incident
from .conftest import Api

# With SciPy installed (the IBM image, and the "full" CI job) the exact solver must be the one that ran; without it, the fallback.
EXPECTED_OPTIMIZER = multi_incident.OPTIMIZER_MILP if importlib.util.find_spec("scipy") else multi_incident.OPTIMIZER_FALLBACK


def priority(api: Api, incident: dict, level: str) -> dict:
    """Set and confirm an incident's priority the way a dispatcher does."""
    path = {"incidentId": incident["incidentId"]}
    updated = api.call("updateIncident", path=path, json={"version": incident["version"], "priority": level}, expect=200).json()
    return api.call("updateIncident", path=path, json={"version": updated["version"], "priorityConfirmed": True}, expect=200).json()


def joint(api: Api, primary: dict, *others: dict, expect=201, **body):
    return api.call(
        "createRecommendation", path={"incidentId": primary["incidentId"]}, expect=expect,
        json={"scope": "multi_incident", "additionalIncidentIds": [o["incidentId"] for o in others], **body},
    ).json()


def units_of(rec: dict) -> set[str]:
    return {u for p in rec.get("packages") or [] for u in p["unitIds"]}


@pytest.fixture
def contested(api, make_incident):
    """A hazmat incident and a technical-rescue incident: HR4 is the only unit that can serve either."""
    hazmat = priority(api, make_incident(confirm="hazmat", lat=39.11, lon=-95.71), "P1")
    rescue = priority(api, make_incident(confirm="technical_rescue", lat=39.08, lon=-95.66), "P2")
    return hazmat, rescue


# --- validation ----------------------------------------------------------------

def test_multi_incident_needs_another_incident(api, make_incident):
    inc = make_incident(confirm="structure_fire")
    r = api.call("createRecommendation", path={"incidentId": inc["incidentId"]}, json={"scope": "multi_incident"}, expect=400)
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_additional_incidents_need_the_multi_incident_scope(api, make_incident):
    a, b = make_incident(confirm="structure_fire"), make_incident(confirm="vehicle_fire")
    for body in ({"additionalIncidentIds": [b["incidentId"]]}, {"scope": "single_incident", "additionalIncidentIds": [b["incidentId"]]}):
        r = api.call("createRecommendation", path={"incidentId": a["incidentId"]}, json=body, expect=400)
        assert r.json()["details"][0]["field"] == "additionalIncidentIds"


def test_an_incident_may_appear_only_once(api, make_incident):
    a, b = make_incident(confirm="structure_fire"), make_incident(confirm="vehicle_fire")
    joint(api, a, a, expect=400)
    joint(api, a, b, b, expect=400)


def test_at_most_five_incidents_can_be_planned_together(api, make_incident):
    incidents = [make_incident(confirm="vehicle_fire") for _ in range(6)]
    joint(api, incidents[0], *incidents[1:], expect=400)
    joint(api, incidents[0], *incidents[1:5], expect=201)


def test_an_unknown_additional_incident_is_404(api, make_incident):
    a = make_incident(confirm="structure_fire")
    api.call(
        "createRecommendation", path={"incidentId": a["incidentId"]}, expect=404,
        json={"scope": "multi_incident", "additionalIncidentIds": ["00000000-0000-4000-8000-000000000000"]},
    )


def test_a_closed_additional_incident_is_a_state_conflict(api, make_incident):
    a, b = make_incident(confirm="structure_fire"), make_incident(confirm="vehicle_fire")
    api.call("updateIncident", path={"incidentId": b["incidentId"]}, json={"version": b["version"], "status": "cancelled"}, expect=200)
    assert joint(api, a, b, expect=409)["code"] == "INVALID_STATE_TRANSITION"


def test_an_additional_incident_without_a_category_is_a_conflict(api, make_incident):
    a, b = make_incident(confirm="structure_fire"), make_incident()
    r = joint(api, a, b, expect=409)
    assert r["code"] == "CONFLICT" and b["number"] in r["message"]


# --- the joint plan ----------------------------------------------------------------

def test_the_response_is_a_multi_incident_recommendation(api, contested):
    hazmat, rescue = contested
    rec = joint(api, hazmat, rescue)
    assert rec["stage"] == "multi_incident_optimization"
    assert rec["inputs"]["optimizer"] == EXPECTED_OPTIMIZER
    assert any(v.startswith("multi-incident-allocation") for v in rec["inputs"]["modelVersions"])
    assert rec["humanConfirmationRequired"] is True and rec["status"] == "pending_review"
    assert rec["incidentId"] == hazmat["incidentId"]
    assert any("Joint allocation across 2 incidents" in line for line in rec["limitations"])


def test_the_higher_priority_incident_gets_the_contested_unit(api, contested, units):
    hazmat, rescue = contested
    high, low = joint(api, hazmat, rescue), joint(api, rescue, hazmat)
    hr4 = units["HR4"]["unitId"]
    assert hr4 in units_of(high)
    assert hr4 not in units_of(low)
    assert any(line.startswith("HR4 is eligible for this incident but is allocated to") for line in low["limitations"])
    assert any("cannot be fully staffed" in line and rescue["number"] in line for line in high["limitations"])
    assert any(line.startswith("Unmet mandatory requirement") for line in low["limitations"])


def test_the_two_incidents_get_disjoint_parts_of_one_plan(api, contested):
    hazmat, rescue = contested
    assert not units_of(joint(api, hazmat, rescue)) & units_of(joint(api, rescue, hazmat))


def test_priority_decides_not_who_asked_first(api, make_incident, units):
    hazmat = priority(api, make_incident(confirm="hazmat"), "P3")
    rescue = priority(api, make_incident(confirm="technical_rescue"), "P1")
    hr4 = units["HR4"]["unitId"]
    assert hr4 in units_of(joint(api, rescue, hazmat))
    assert hr4 not in units_of(joint(api, hazmat, rescue))


def test_equal_priority_goes_to_the_earlier_incident(api, make_incident, units):
    first = priority(api, make_incident(confirm="hazmat"), "P2")
    second = priority(api, make_incident(confirm="technical_rescue"), "P2")
    hr4 = units["HR4"]["unitId"]
    assert hr4 in units_of(joint(api, first, second))
    assert hr4 not in units_of(joint(api, second, first))


def test_unconfirmed_and_unset_priorities_are_called_out(api, make_incident):
    a = priority(api, make_incident(confirm="structure_fire"), "P1")
    b = make_incident(confirm="vehicle_fire")  # priority unassigned
    rec = joint(api, a, b)
    assert any(b["number"] in line and "no priority" in line for line in rec["limitations"])


def test_only_eligible_units_are_ever_allocated(api, make_incident):
    a = priority(api, make_incident(confirm="structure_fire"), "P1")
    b = priority(api, make_incident(confirm="medical_cardiac"), "P2")
    c = priority(api, make_incident(confirm="technical_rescue"), "P3")
    for primary, others in ((a, (b, c)), (b, (a, c)), (c, (a, b))):
        rec = joint(api, primary, *others, maxCandidates=50)
        eligible = {x["unitId"] for x in rec["candidates"] if x["eligible"]}
        assert units_of(rec) <= eligible, "the joint plan sent a unit that failed hard eligibility"
        assert all(p["packageId"] for p in rec.get("packages") or [])


def test_excluded_units_are_excluded_from_every_incident(api, contested, units):
    hazmat, rescue = contested
    hr4 = units["HR4"]["unitId"]
    rec = joint(api, hazmat, rescue, excludeUnitIds=[hr4])
    assert hr4 not in units_of(rec) and hr4 not in units_of(joint(api, rescue, hazmat, excludeUnitIds=[hr4]))


def test_two_uncontested_incidents_are_both_fully_staffed_and_both_can_be_accepted(api, make_incident):
    fire = priority(api, make_incident(confirm="structure_fire", lat=39.12, lon=-95.72), "P1")
    medical = priority(api, make_incident(confirm="medical_cardiac", lat=39.05, lon=-95.65), "P2")
    for_fire, for_medical = joint(api, fire, medical), joint(api, medical, fire)
    assert all(p["satisfiesAllMandatory"] for p in for_fire["packages"] + for_medical["packages"])
    assert not units_of(for_fire) & units_of(for_medical)
    for rec in (for_fire, for_medical):
        api.call("createRecommendationReview", path={"recommendationId": rec["recommendationId"]}, json={"decision": "accept", "humanConfirmed": True}, expect=201)
    # Accepting one did not invalidate the other: the plan gave them different units.
    assert api.call("getRecommendation", path={"recommendationId": for_medical["recommendationId"]}, expect=200).json()["status"] == "accepted"


def test_a_single_incident_request_is_unchanged(api, make_incident):
    rec = api.call("createRecommendation", path={"incidentId": make_incident(confirm="structure_fire")["incidentId"]}, json={}, expect=201).json()
    assert rec["stage"] == "prioritization" and "optimizer" not in rec["inputs"]
    assert not any("Joint allocation" in line for line in rec["limitations"])


def test_a_solver_failure_degrades_to_the_labeled_fallback(api, contested, monkeypatch):
    def boom(_problems):
        raise RuntimeError("solver status 4")

    monkeypatch.setattr(multi_incident, "solve_milp", boom)
    hazmat, rescue = contested
    rec = joint(api, hazmat, rescue)
    assert rec["inputs"]["optimizer"] == multi_incident.OPTIMIZER_FALLBACK
    assert any("greedy" in line for line in rec["limitations"])
    assert rec["humanConfirmationRequired"] is True


def test_the_audit_trail_records_the_joint_request(api, contested):
    hazmat, rescue = contested
    rec = joint(api, hazmat, rescue)
    events = api.call("listAuditEvents", role="supervisor", query={"entityId": rec["recommendationId"]}, expect=200).json()["data"]
    created = [e for e in events if e["action"] == "recommendation.create"]
    assert created and created[0]["detail"]["scope"] == "multi_incident"
    assert created[0]["detail"]["jointIncidentIds"] == [rescue["incidentId"]]
