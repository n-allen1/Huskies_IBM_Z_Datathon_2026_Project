from uuid import uuid4

from .conftest import incident_payload


def test_create_incident_persists_and_is_readable(api):
    created = api.call("createIncident", json=incident_payload(mockScenarioId="A"), expect=201).json()
    assert created["status"] == "intake"
    assert created["priority"] == "unassigned" and created["priorityConfirmed"] is False
    assert created["version"] == 1
    assert created["number"] == "INC-2026-000001"
    assert created["provenance"]["dataOrigin"] == "simulated"
    got = api.call("getIncident", path={"incidentId": created["incidentId"]}, expect=200).json()
    assert got == created


def test_manual_incident_is_marked_user_entered(api):
    created = api.call("createIncident", json=incident_payload(), expect=201).json()
    assert created["provenance"]["dataOrigin"] == "user_entered"


def test_incident_must_be_synthetic_and_valid(api):
    bad = incident_payload()
    bad["isSynthetic"] = False
    r = api.call("createIncident", json=bad, expect=400)
    assert r.json()["code"] == "VALIDATION_ERROR" and r.json()["details"]
    api.call("createIncident", json={**incident_payload(), "surprise": 1}, expect=400)
    api.call("createIncident", json={**incident_payload(), "narrative": ""}, expect=400)
    api.call("createIncident", json={**incident_payload(), "location": {"point": {"lat": 91, "lon": 0}}}, expect=400)


def test_create_incident_is_idempotent(api):
    a = api.call("createIncident", json=incident_payload(), key="repeat-key-1", expect=201).json()
    b = api.call("createIncident", json=incident_payload(), key="repeat-key-1", expect=201).json()
    assert a == b
    assert len(api.call("listIncidents", expect=200).json()["data"]) == 1
    api.call("createIncident", json=incident_payload("different"), key="repeat-key-1", expect=409)


def test_idempotency_key_is_scoped_per_user(api):
    api.call("createIncident", role="dispatcher", json=incident_payload(), key="shared-key-1", expect=201)
    api.call("createIncident", role="call_taker", json=incident_payload("other"), key="shared-key-1", expect=201)


def test_idempotency_key_length_is_enforced(api):
    api.call("createIncident", json=incident_payload(), key="short", expect=400)


def test_unknown_incident_is_404(api):
    r = api.call("getIncident", path={"incidentId": uuid4()}, expect=404)
    assert r.json()["code"] == "NOT_FOUND" and r.json()["requestId"]
    api.call("getIncident", path={"incidentId": "not-a-uuid"}, expect=400)


def test_list_filters_sort_and_paginate(api, make_incident):
    a = make_incident(narrative="Kitchen fire at the diner", confirm="structure_fire")
    b = make_incident(narrative="Chest pain at the library")
    c = make_incident(narrative="Crash on route 9")
    api.call("updateIncident", path={"incidentId": c["incidentId"]}, json={"version": c["version"], "priority": "P2"}, expect=200)

    listed = api.call("listIncidents", query={"sort": "number"}, expect=200).json()
    assert [i["number"] for i in listed["data"]] == sorted(i["number"] for i in listed["data"])
    assert listed["page"]["nextCursor"] is None and listed["page"]["totalEstimate"] == 3

    assert [i["incidentId"] for i in api.call("listIncidents", query={"q": "library"}, expect=200).json()["data"]] == [b["incidentId"]]
    assert [i["incidentId"] for i in api.call("listIncidents", query={"category": "structure_fire"}, expect=200).json()["data"]] == [a["incidentId"]]
    assert len(api.call("listIncidents", query={"priority": "P2"}, expect=200).json()["data"]) == 1
    two = api.call("listIncidents", query={"status": ["intake", "pending_dispatch"]}, expect=200).json()
    assert len(two["data"]) == 3

    first = api.call("listIncidents", query={"limit": 2, "sort": "number"}, expect=200).json()
    assert len(first["data"]) == 2 and first["page"]["nextCursor"]
    rest = api.call("listIncidents", query={"limit": 2, "sort": "number", "cursor": first["page"]["nextCursor"]}, expect=200).json()
    assert len(rest["data"]) == 1 and rest["page"]["nextCursor"] is None
    seen = [i["incidentId"] for i in first["data"] + rest["data"]]
    assert len(set(seen)) == 3


def test_bad_list_parameters_are_400(api):
    api.call("listIncidents", query={"limit": 0}, expect=400)
    api.call("listIncidents", query={"limit": 201}, expect=400)
    api.call("listIncidents", query={"cursor": "garbage"}, expect=400)
    api.call("listIncidents", query={"sort": "narrative"}, expect=400)
    api.call("listIncidents", query={"status": "nope"}, expect=400)


def test_update_requires_current_version(api, make_incident):
    inc = make_incident()
    path = {"incidentId": inc["incidentId"]}
    ok = api.call("updateIncident", path=path, json={"version": 1, "callbackNumber": "555-0100"}, expect=200).json()
    assert ok["version"] == 2 and ok["callbackNumber"] == "555-0100"
    stale = api.call("updateIncident", path=path, json={"version": 1, "callbackNumber": "555-0199"}, expect=409)
    assert stale.json()["code"] == "CONFLICT"


def test_invalid_status_transitions_are_rejected(api, make_incident):
    inc = make_incident()
    r = api.call("updateIncident", path={"incidentId": inc["incidentId"]}, json={"version": 1, "status": "resolved"}, expect=409)
    assert r.json()["code"] == "INVALID_STATE_TRANSITION"
    api.call("updateIncident", path={"incidentId": inc["incidentId"]}, json={"version": 1, "status": "cancelled"}, expect=200)
    done = api.call("getIncident", path={"incidentId": inc["incidentId"]}, expect=200).json()
    api.call("updateIncident", path={"incidentId": inc["incidentId"]}, json={"version": done["version"], "status": "intake"}, expect=409)


def test_only_dispatchers_and_supervisors_confirm_priority(api, make_incident):
    inc = make_incident()
    path = {"incidentId": inc["incidentId"]}
    r = api.call("updateIncident", role="call_taker", path=path, json={"version": 1, "priority": "P1", "priorityConfirmed": True}, expect=403)
    assert r.json()["code"] == "FORBIDDEN"
    api.call("updateIncident", role="call_taker", path=path, json={"version": 1, "priority": "P1"}, expect=200)
    confirmed = api.call("updateIncident", role="supervisor", path=path, json={"version": 2, "priorityConfirmed": True}, expect=200).json()
    assert confirmed["priority"] == "P1" and confirmed["priorityConfirmed"] is True
    # changing a confirmed priority withdraws the confirmation
    changed = api.call("updateIncident", role="call_taker", path=path, json={"version": 3, "priority": "P3"}, expect=200).json()
    assert changed["priorityConfirmed"] is False


def test_cannot_confirm_an_unassigned_priority(api, make_incident):
    inc = make_incident()
    api.call("updateIncident", path={"incidentId": inc["incidentId"]}, json={"version": 1, "priorityConfirmed": True}, expect=400)


def test_incident_access_by_role(api, make_incident):
    inc = make_incident()
    for role in ("hr", "fleet", "leadership"):
        api.call("listIncidents", role=role, expect=403)
        api.call("getIncident", role=role, path={"incidentId": inc["incidentId"]}, expect=403)
    api.call("createIncident", role="supervisor", json=incident_payload(), expect=403)
    api.call("createIncident", role="admin", json=incident_payload(), expect=201)
    api.call("getIncident", role="call_taker", path={"incidentId": inc["incidentId"]}, expect=200)


def test_authentication_is_required(api, make_incident):
    inc = make_incident()
    path = {"incidentId": inc["incidentId"]}
    assert api.call("getIncident", role=None, path=path, expect=401).json()["code"] == "UNAUTHENTICATED"
    api.call("getIncident", role="not-a-token", path=path, expect=401)
    api.call("getIncident", role=None, path=path, headers={"Authorization": "Basic abc"}, expect=401)
