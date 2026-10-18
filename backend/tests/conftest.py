"""Test harness. `Api.call` resolves paths from the contract by operationId, so a route that
drifts from the contract fails here, and every response is validated against the schema the
contract declares for that status (an undocumented status is a failure)."""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from app.config import Settings
from app.main import create_app

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)

TOKENS = {
    "call_taker": "demo-call-taker", "dispatcher": "demo-dispatcher", "supervisor": "demo-supervisor",
    "hr": "demo-hr", "fleet": "demo-fleet", "leadership": "demo-leadership", "admin": "demo-admin",
}

OPERATIONS: dict[str, tuple[str, str, dict]] = {
    op["operationId"]: (method, path, op)
    for path, item in CONTRACT["paths"].items()
    for method, op in item.items()
    if isinstance(op, dict) and "operationId" in op
}


def _resolve(node: dict) -> dict:
    return CONTRACT["components"]["responses"][node["$ref"].split("/")[-1]] if "$ref" in node else node


def assert_conforms(op_id: str, status: int, body) -> None:
    _, _, op = OPERATIONS[op_id]
    declared = op["responses"].get(str(status))
    assert declared is not None, f"{op_id} returned {status}, which the contract does not declare"
    content = _resolve(declared).get("content", {}).get("application/json")
    if content is None:
        return
    schema = {**content["schema"], "components": CONTRACT["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(body)


class Api:
    def __init__(self, client: TestClient):
        self.client = client

    def call(self, op_id: str, *, role: str | None = "dispatcher", path: dict | None = None, query=None,
             json=None, key: str | None = "auto", headers: dict | None = None, expect: int | None = None):
        method, template, op = OPERATIONS[op_id]
        url = re.sub(r"\{(\w+)\}", lambda m: str((path or {})[m.group(1)]), template)
        h = dict(headers or {})
        if role:
            h["Authorization"] = f"Bearer {TOKENS.get(role, role)}"
        needs_key = any(p.get("$ref", "").endswith("/IdemKey") for p in op.get("parameters", []))
        if needs_key and key:
            h["Idempotency-Key"] = uuid.uuid4().hex if key == "auto" else key
        resp = self.client.request(method.upper(), url, params=query, json=json, headers=h)
        if "application/json" in resp.headers.get("content-type", ""):
            assert_conforms(op_id, resp.status_code, resp.json())
        else:
            assert str(resp.status_code) in op["responses"], f"undeclared status {resp.status_code}"
        if expect is not None:
            assert resp.status_code == expect, resp.text
        return resp


@pytest.fixture
def settings():
    return Settings(demo_mode=True)


@pytest.fixture
def app(settings):
    return create_app(settings=settings, clock=lambda: NOW)


@pytest.fixture
def api(app):
    return Api(TestClient(app, raise_server_exceptions=False))


def incident_payload(narrative="House fire with smoke showing", category=None, lat=39.10, lon=-95.70, **extra):
    body = {
        "narrative": narrative,
        "location": {"address": "100 Main St (fictional)", "point": {"lat": lat, "lon": lon}},
        "isSynthetic": True,
        **extra,
    }
    if category:
        body["reportedCategory"] = category
    return body


@pytest.fixture
def make_incident(api):
    def make(category=None, confirm=None, **kw) -> dict:
        inc = api.call("createIncident", json=incident_payload(category=category, **kw), expect=201).json()
        if confirm:
            api.call(
                "createClassification", path={"incidentId": inc["incidentId"]},
                json={"mode": "confirm", "category": confirm}, expect=201,
            )
            inc = api.call("getIncident", path={"incidentId": inc["incidentId"]}, expect=200).json()
        return inc
    return make


@pytest.fixture
def units(api):
    """call sign -> unit JSON."""
    data = api.call("listUnits", query={"limit": 200}, expect=200).json()["data"]
    return {u["callSign"]: u for u in data}
