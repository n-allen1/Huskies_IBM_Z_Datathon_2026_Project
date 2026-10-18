import pytest

from app.inference import KeywordClassifier
from app.models import IncidentCategory


@pytest.mark.parametrize("text,expected", [
    ("Caller reports chest pain and trouble breathing", "medical_cardiac"),
    ("Smoke showing from a two story house fire", "structure_fire"),
    ("Car fire on the highway shoulder", "vehicle_fire"),
    ("Chemical spill with strong fumes at the plant", "hazmat"),
    ("Worker trapped in a trench", "technical_rescue"),
    ("Two vehicle collision, one rollover", "traffic_collision"),
    ("Neighbor says someone broke into the garage", "burglary"),
    ("Fell at the waterfall", "other"),  # word boundaries: 'fall' alone must not match
    ("Lorem ipsum", "other"),
])
def test_keyword_classifier(text, expected):
    result = KeywordClassifier().classify(text)
    assert result.category == IncidentCategory(expected)
    assert result.confidence is None and result.suggested_priority is None  # no invented probabilities or priority


def test_infer_creates_a_suggestion_a_human_must_confirm(api, make_incident):
    inc = make_incident(narrative="Chest pain, patient is not breathing")
    path = {"incidentId": inc["incidentId"]}
    s = api.call("createClassification", path=path, json={"mode": "infer"}, expect=201).json()
    assert s["status"] == "suggested" and s["source"] == "rule_based" and s["category"] == "medical_cardiac"
    assert s["modelVersion"] and "confidence" not in s
    after = api.call("getIncident", path=path, expect=200).json()
    assert "confirmedCategory" not in after and after["status"] == "intake"  # suggestion changed nothing


def test_confirming_a_suggestion_sets_the_category_and_advances_the_incident(api, make_incident):
    inc = make_incident(narrative="House fire with flames")
    path = {"incidentId": inc["incidentId"]}
    s = api.call("createClassification", path=path, json={"mode": "infer"}, expect=201).json()
    c = api.call(
        "createClassification", path=path,
        json={"mode": "confirm", "inferredClassificationId": s["classificationId"]}, expect=201,
    ).json()
    assert c["source"] == "dispatcher_confirmed" and c["status"] == "confirmed" and c["category"] == "structure_fire"
    got = api.call("getIncident", path=path, expect=200).json()
    assert got["confirmedCategory"] == "structure_fire" and got["status"] == "pending_dispatch" and got["version"] == 2
    reqs = api.call("getIncidentRequirements", path=path, expect=200).json()
    assert reqs["basedOnClassificationId"] == c["classificationId"]
    assert {r["capabilityId"] for r in reqs["requirements"]} >= {"CAP-FIRE-SUPPRESSION", "CAP-AERIAL-LADDER"}
    assert all(r["origin"] == "protocol_rule" for r in reqs["requirements"])


def test_human_correction_overrides_the_suggestion(api, make_incident):
    inc = make_incident(narrative="House fire with flames")
    path = {"incidentId": inc["incidentId"]}
    s = api.call("createClassification", path=path, json={"mode": "infer"}, expect=201).json()
    c = api.call(
        "createClassification", path=path,
        json={"mode": "confirm", "inferredClassificationId": s["classificationId"], "category": "hazmat"}, expect=201,
    ).json()
    assert c["category"] == "hazmat"
    assert api.call("getIncident", path=path, expect=200).json()["confirmedCategory"] == "hazmat"


def test_rejecting_a_suggestion(api, make_incident):
    inc = make_incident(narrative="House fire with flames")
    path = {"incidentId": inc["incidentId"]}
    s = api.call("createClassification", path=path, json={"mode": "infer"}, expect=201).json()
    r = api.call("createClassification", path=path, json={"mode": "reject", "inferredClassificationId": s["classificationId"]}, expect=201).json()
    assert r["status"] == "rejected"
    again = api.call("createClassification", path=path, json={"mode": "confirm", "inferredClassificationId": s["classificationId"]}, expect=409)
    assert again.json()["code"] == "CONFLICT"
    assert "confirmedCategory" not in api.call("getIncident", path=path, expect=200).json()


def test_confirm_and_reject_need_something_to_act_on(api, make_incident):
    path = {"incidentId": make_incident()["incidentId"]}
    api.call("createClassification", path=path, json={"mode": "confirm"}, expect=400)
    api.call("createClassification", path=path, json={"mode": "reject"}, expect=400)


def test_priority_on_confirm_is_restricted(api, make_incident):
    path = {"incidentId": make_incident()["incidentId"]}
    api.call("createClassification", role="call_taker", path=path, json={"mode": "confirm", "category": "hazmat", "priority": "P1"}, expect=403)
    api.call("createClassification", role="dispatcher", path=path, json={"mode": "confirm", "category": "hazmat", "priority": "P1"}, expect=201)
    got = api.call("getIncident", path=path, expect=200).json()
    assert got["priority"] == "P1" and got["priorityConfirmed"] is True


def test_classification_is_for_call_takers_dispatchers_supervisors(api, make_incident):
    path = {"incidentId": make_incident()["incidentId"]}
    for role in ("admin", "hr", "fleet", "leadership"):
        api.call("createClassification", role=role, path=path, json={"mode": "infer"}, expect=403)
