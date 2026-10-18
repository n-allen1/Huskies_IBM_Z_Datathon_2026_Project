"""Human review, unit status events, invalidation of stale recommendations, the audit trail and event emission.

Seed facts (seed_standin.py): for a cardiac call the recommended package is M1 alone; M5's cardiac monitor is in
maintenance (ineligible); M2 is already dispatched; HR4 is the only unit with hazmat/tech-rescue capability."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.main import create_app

from .conftest import CONTRACT, NOW, Api, incident_payload


def recommend(api, incident, **body):
    return api.call("createRecommendation", path={"incidentId": incident["incidentId"]}, json={"maxCandidates": 50, **body}, expect=201).json()


def review(api, rec, *, expect=201, role="dispatcher", key="auto", **body):
    body.setdefault("humanConfirmed", True)
    return api.call("createRecommendationReview", role=role, path={"recommendationId": rec["recommendationId"]}, json=body, key=key, expect=expect)


def set_status(api, unit, status, *, expect=201, role="dispatcher", **body):
    return api.call("createUnitStatusEvent", role=role, path={"unitId": unit["unitId"]}, json={"status": status, **body}, expect=expect)


def get_unit(api, unit):
    return api.call("getUnit", path={"unitId": unit["unitId"]}, expect=200).json()


def get_rec(api, rec):
    return api.call("getRecommendation", path={"recommendationId": rec["recommendationId"]}, expect=200).json()


def audit(api, **query):
    return api.call("listAuditEvents", role="supervisor", query={"limit": 200, **query}, expect=200).json()["data"]


def ids(*units):
    return [u["unitId"] for u in units]


# --- accept / modify / reject -------------------------------------------------------------------------

def test_accept_assigns_the_recommended_package_in_simulation(api, make_incident, units):
    inc = make_incident(confirm="medical_cardiac")
    rec = recommend(api, inc)
    assert rec["packages"][0]["unitIds"] == ids(units["M1"])

    r = review(api, rec, decision="accept", explanation="Closest ALS unit").json()
    assert r["decision"] == "accept" and r["isOverride"] is False and r["simulatedAssignment"] is True
    assert r["selectedUnitIds"] == ids(units["M1"])
    m1 = get_unit(api, units["M1"])
    assert m1["status"] == "dispatched_simulated" and m1["version"] == 2
    assert api.call("getIncident", path={"incidentId": inc["incidentId"]}, expect=200).json()["status"] == "dispatched_simulated"
    assert get_rec(api, rec)["status"] == "accepted"

    entry = audit(api, action="recommendation.review.accept")[0]
    assert entry["entityId"] == rec["recommendationId"] and entry["actorRoles"] == ["dispatcher"]
    assert entry["detail"]["selectedUnits"] == ["M1"] and entry["detail"]["isOverride"] is False


def test_choosing_a_different_eligible_unit_is_a_modification_that_needs_a_reason(api, make_incident, units):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    assert next(c for c in rec["candidates"] if c["callSign"] == "M3")["eligible"]
    review(api, rec, decision="modify", selectedUnitIds=ids(units["M3"]), expect=400)
    r = review(api, rec, decision="modify", selectedUnitIds=ids(units["M3"]), overrideReason="M1 is committed to a standby").json()
    assert r["isOverride"] is True and r["overrideReason"] == "M1 is committed to a standby"
    assert get_rec(api, rec)["status"] == "modified"
    assert get_unit(api, units["M3"])["status"] == "dispatched_simulated" and get_unit(api, units["M1"])["status"] == "available"


def test_modify_equal_to_the_recommendation_is_not_an_override(api, make_incident, units):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    r = review(api, rec, decision="modify", selectedUnitIds=ids(units["M1"])).json()
    assert r["isOverride"] is False


def test_a_human_may_choose_an_ineligible_unit_only_as_a_recorded_override(api, make_incident, units):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    m5 = next(c for c in rec["candidates"] if c["callSign"] == "M5")
    assert not m5["eligible"]  # monitor in maintenance
    review(api, rec, decision="modify", selectedUnitIds=ids(units["M5"]), expect=400)
    r = review(api, rec, decision="modify", selectedUnitIds=ids(units["M5"]), overrideReason="Monitor swap done by crew").json()
    assert r["isOverride"] is True
    detail = audit(api, action="recommendation.review.modify")[0]["detail"]
    assert detail["ineligibleSelected"][0]["callSign"] == "M5"
    assert any("CAP-CARDIAC-MONITOR" in reason for reason in detail["ineligibleSelected"][0]["reasons"])


def test_a_unit_that_is_not_available_cannot_be_assigned_even_with_an_override(api, make_incident, units):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    err = review(api, rec, decision="modify", selectedUnitIds=ids(units["M2"]), overrideReason="Need it", expect=409).json()
    assert err["code"] == "UNIT_UNAVAILABLE" and err["details"][0]["field"] == "selectedUnitIds[0]"
    assert "M2" in err["details"][0]["issue"] and "dispatched_simulated" in err["details"][0]["issue"]
    assert get_rec(api, rec)["status"] == "pending_review"  # nothing changed


def test_selected_units_must_come_from_the_recommendation(api, make_incident, units):
    rec = recommend(api, make_incident(confirm="medical_cardiac"), maxCandidates=2)
    outside = next(u for u in units.values() if u["unitId"] not in {c["unitId"] for c in rec["candidates"]})
    review(api, rec, decision="modify", selectedUnitIds=ids(outside), overrideReason="x", expect=400)


def test_accept_takes_the_recommendation_and_modify_needs_units(api, make_incident, units):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    review(api, rec, decision="accept", selectedUnitIds=ids(units["M3"]), expect=400)  # differing choice = modify
    review(api, rec, decision="modify", expect=400)  # modify without units


def test_nothing_to_accept_when_no_package_exists(api, make_incident, units):
    set_status(api, units["HR4"], "out_of_service")
    rec = recommend(api, make_incident(confirm="hazmat"))
    assert "packages" not in rec
    err = review(api, rec, decision="accept", expect=400).json()
    assert "nothing to accept" in err["message"]
    review(api, rec, decision="reject", explanation="No hazmat unit; calling mutual aid")  # rejecting is always possible


def test_reject_is_recorded_without_any_assignment(api, make_incident, units):
    inc = make_incident(confirm="medical_cardiac")
    rec = recommend(api, inc)
    review(api, rec, decision="reject", selectedUnitIds=ids(units["M1"]), expect=400)
    r = review(api, rec, decision="reject", explanation="Caller cancelled the ambulance").json()
    assert r["simulatedAssignment"] is False and r["isOverride"] is False and "selectedUnitIds" not in r
    assert get_unit(api, units["M1"])["status"] == "available"
    assert get_rec(api, rec)["status"] == "rejected"
    assert api.call("getIncident", path={"incidentId": inc["incidentId"]}, expect=200).json()["status"] == "pending_dispatch"


def test_a_human_must_confirm(api, make_incident):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    review(api, rec, decision="accept", humanConfirmed=False, expect=400)
    review(api, rec, decision="accept", humanConfirmed=None, expect=400)


# --- staleness --------------------------------------------------------------------------------------------

def test_a_review_is_final(api, make_incident):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    review(api, rec, decision="accept")
    assert review(api, rec, decision="reject", expect=409).json()["code"] == "CONFLICT"


def test_an_invalidated_or_expired_recommendation_cannot_be_reviewed(api, make_incident, settings):
    inc = make_incident(confirm="medical_cardiac")
    first = recommend(api, inc)
    recommend(api, inc)  # supersedes the first
    err = review(api, first, decision="accept", expect=409).json()
    assert err["code"] == "RECOMMENDATION_OUTDATED" and "Superseded" in err["message"]

    clock = {"now": NOW}
    timed = Api(TestClient(create_app(settings=settings, clock=lambda: clock["now"])))
    inc2 = timed.call("createIncident", json=incident_payload(), expect=201).json()
    timed.call("createClassification", path={"incidentId": inc2["incidentId"]}, json={"mode": "confirm", "category": "vehicle_fire"}, expect=201)
    rec2 = recommend(timed, inc2)
    clock["now"] = NOW + timedelta(seconds=settings.recommendation_ttl_seconds + 1)
    assert review(timed, rec2, decision="accept", expect=409).json()["code"] == "RECOMMENDATION_OUTDATED"


def test_a_review_based_on_a_view_older_than_the_recommendation_is_refused(api, make_incident):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    created = datetime.fromisoformat(rec["createdAt"].replace("Z", "+00:00"))
    stale = (created - timedelta(minutes=1)).astimezone(timezone.utc).isoformat()
    assert review(api, rec, decision="accept", recommendationVersionSeenAt=stale, expect=409).json()["code"] == "RECOMMENDATION_OUTDATED"
    review(api, rec, decision="accept", recommendationVersionSeenAt=created.isoformat())


def test_competing_incidents_one_assignment_invalidates_the_other_recommendation(api, make_incident, units):
    first, second = make_incident(confirm="hazmat"), make_incident(confirm="hazmat")
    rec1, rec2 = recommend(api, first), recommend(api, second)
    assert rec1["packages"][0]["unitIds"] == rec2["packages"][0]["unitIds"] == ids(units["HR4"])

    review(api, rec1, decision="accept")
    stale = get_rec(api, rec2)
    assert stale["status"] == "invalidated" and "HR4" in stale["invalidatedReason"] and first["number"] in stale["invalidatedReason"]
    assert review(api, rec2, decision="accept", expect=409).json()["code"] == "RECOMMENDATION_OUTDATED"

    retry = recommend(api, second)  # re-running shows the consequence honestly
    hr4 = next(c for c in retry["candidates"] if c["callSign"] == "HR4")
    assert not hr4["eligible"] and any("dispatched_simulated" in r for r in hr4["ineligibleReasons"])
    assert "packages" not in retry and any("CAP-HAZMAT-TECH" in line for line in retry["limitations"])


def test_review_is_idempotent_and_detects_key_reuse(api, make_incident, units):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    a = review(api, rec, decision="accept", key="review-key-0001").json()
    b = review(api, rec, decision="accept", key="review-key-0001").json()
    assert a["reviewId"] == b["reviewId"] and get_unit(api, units["M1"])["version"] == 2  # assigned once
    assert review(api, rec, decision="reject", key="review-key-0001", expect=409).json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_only_dispatchers_and_supervisors_review(api, make_incident):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    for role in ("call_taker", "fleet", "hr", "leadership", "admin"):
        review(api, rec, decision="accept", role=role, expect=403)
    review(api, rec, decision="accept", role="supervisor")


# --- unit status events -------------------------------------------------------------------------------------

def test_losing_a_unit_invalidates_recommendations_that_depend_on_it(api, make_incident, units):
    rec = recommend(api, make_incident(confirm="hazmat"))
    event = set_status(api, units["HR4"], "out_of_service", reason="Hydraulic leak").json()
    assert event["previousStatus"] == "available" and event["status"] == "out_of_service"
    assert event["invalidatedRecommendationIds"] == [rec["recommendationId"]]
    stale = get_rec(api, rec)
    assert stale["status"] == "invalidated" and "HR4 became out_of_service: Hydraulic leak" in stale["invalidatedReason"]
    hr4 = get_unit(api, units["HR4"])
    assert hr4["status"] == "out_of_service" and hr4["version"] == 2

    back = set_status(api, units["HR4"], "available").json()
    assert "invalidatedRecommendationIds" not in back  # becoming available again does not make anything stale


def test_losing_a_unit_the_recommendation_does_not_use_changes_nothing(api, make_incident, units):
    rec = recommend(api, make_incident(confirm="medical_cardiac"))
    event = set_status(api, units["E3"], "out_of_service").json()
    assert "invalidatedRecommendationIds" not in event and get_rec(api, rec)["status"] == "pending_review"
    # ...but the review itself re-checks live availability of whatever is chosen
    set_status(api, units["M3"], "off_duty")
    err = review(api, rec, decision="modify", selectedUnitIds=ids(units["M3"]), overrideReason="x", expect=409).json()
    assert err["code"] == "UNIT_UNAVAILABLE"


def test_status_events_check_versions_and_reject_no_ops(api, units):
    set_status(api, units["E1"], "off_duty", expectedUnitVersion=99, expect=409)
    assert set_status(api, units["E1"], "available", expect=409).json()["code"] == "INVALID_STATE_TRANSITION"
    set_status(api, units["E1"], "returning", expectedUnitVersion=1, location={"lat": 39.12, "lon": -95.71})
    unit = get_unit(api, units["E1"])
    assert unit["status"] == "returning" and unit["location"] == {"lat": 39.12, "lon": -95.71} and unit["version"] == 2
    api.call("createUnitStatusEvent", path={"unitId": "00000000-0000-0000-0000-000000000000"}, json={"status": "off_duty"}, expect=404)


def test_status_event_roles(api, units):
    set_status(api, units["E1"], "off_duty", role="fleet")
    for role in ("call_taker", "hr", "leadership"):
        set_status(api, units["E2"], "off_duty", role=role, expect=403)


# --- audit trail ----------------------------------------------------------------------------------------------

def test_the_audit_trail_records_who_did_what(api, make_incident, units):
    inc = make_incident(confirm="medical_cardiac")
    rec = recommend(api, inc)
    review(api, rec, decision="accept")
    set_status(api, units["E1"], "off_duty", role="fleet")
    actions = [e["action"] for e in audit(api)]
    for expected in ("incident.create", "classification.confirm", "recommendation.create", "recommendation.review.accept", "unit.status.record"):
        assert expected in actions
    assert actions[0] == "unit.status.record"  # newest first
    fleet_entry = audit(api, action="unit.status.record")[0]
    assert fleet_entry["actorRoles"] == ["fleet_equipment_admin"] and fleet_entry["outcome"] == "success"
    assert [e["action"] for e in audit(api, entityId=rec["recommendationId"])] == ["recommendation.review.accept", "recommendation.create"]
    assert len(audit(api, entityType="unit")) == 1 and audit(api, action="nope") == []


def test_the_audit_trail_is_restricted_and_paged(api, make_incident):
    for _ in range(3):
        make_incident()
    for role in ("dispatcher", "call_taker", "hr", "leadership", "fleet"):
        api.call("listAuditEvents", role=role, expect=403)
    api.call("listAuditEvents", role="admin", expect=200)
    first = api.call("listAuditEvents", role="supervisor", query={"limit": 2}, expect=200).json()
    assert len(first["data"]) == 2 and first["page"]["nextCursor"]
    api.call("listAuditEvents", role="supervisor", query={"sort": "bogus"}, expect=400)


def test_demo_reset_clears_history_and_audits_itself(api, make_incident):
    make_incident(confirm="medical_cardiac")
    api.call("resetDemoData", role="admin", expect=200)
    entries = audit(api)
    assert [e["action"] for e in entries] == ["demo.reset"]
    assert api.client.app.state.services.events.since("000000000000") in ([], None)


# --- events ---------------------------------------------------------------------------------------------------

def test_events_are_emitted_in_order_and_match_the_contract_envelope(api, app, make_incident, units):
    inc = make_incident(confirm="hazmat")
    rec = recommend(api, inc)
    set_status(api, units["HR4"], "out_of_service")
    events = app.state.services.events.since("000000000000")
    validator = Draft202012Validator({"$ref": "#/components/schemas/EventEnvelope", "components": CONTRACT["components"]})
    for e in events:
        validator.validate(e)
    types = [e["type"] for e in events]
    assert types[0] == "incident.created"
    assert types.index("recommendation.created") < types.index("unit.status_changed") < types.index("recommendation.invalidated")
    assert [e["id"] for e in events] == sorted(e["id"] for e in events) and len({e["id"] for e in events}) == len(events)
    invalidated = next(e for e in events if e["type"] == "recommendation.invalidated")
    assert invalidated["entity"] == {"type": "recommendation", "id": rec["recommendationId"]}
