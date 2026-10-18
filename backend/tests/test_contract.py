"""Drift detection: what the backend serves versus contracts/openapi.json.

These tests never edit the contract. A failure means the code and the contract disagree and a
human must decide which is right (see CLAUDE.md, API Contract Governance)."""

import re

from fastapi.routing import APIRoute

from app.main import ROUTERS

from .conftest import CONTRACT, OPERATIONS

# Operations implemented so far (docs/mvp-plan.md build order items 1-7). Add an entry here only when the
# operation exists in the contract AND is implemented; the drift tests below fail on any other difference.
IMPLEMENTED = {
    "getHealth", "getCurrentUser", "resetDemoData",
    "listUnits", "getUnit", "getUnitCapabilities", "listCapabilityDefinitions", "listFacilities",
    "createIncident", "listIncidents", "getIncident", "updateIncident",
    "createClassification", "getIncidentRequirements",
    "createRecommendation", "getRecommendation", "listIncidentRecommendations",
    "createRecommendationReview", "createUnitStatusEvent", "streamEvents",
    "listAuditEvents",  # contract tier "stretch", pulled in because reviews must appear in the audit trail
}


def _routes(app):
    """Routes from the included routers plus any attached straight to the app (an undocumented
    route added either way must be caught). Avoids FastAPI's private include wrappers."""
    direct = [r for r in app.routes if isinstance(r, APIRoute)]
    routes = direct + [r for router in ROUTERS for r in router.routes if isinstance(r, APIRoute)]
    assert routes, "route enumeration found nothing; the tests below would pass vacuously"
    return routes


def _operation_id(route: APIRoute) -> str | None:
    for method in route.methods:
        for op_id, (m, path, _) in OPERATIONS.items():
            if m.upper() == method and path == route.path:
                return op_id
    return None


def test_every_route_exists_in_contract(app):
    undocumented = [f"{sorted(r.methods)} {r.path}" for r in _routes(app) if _operation_id(r) is None]
    assert not undocumented, f"routes not in the contract: {undocumented}"


def test_implemented_operations_are_exactly_the_expected_set(app):
    implemented = {_operation_id(r) for r in _routes(app)}
    assert implemented == IMPLEMENTED
    assert {o for o in implemented if OPERATIONS[o][2]["x-mvp-tier"] != "mvp"} == {"listAuditEvents"}


def test_no_second_openapi_spec_is_served(api):
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert api.client.get(path).status_code == 404


def test_allowed_roles_match_contract(app):
    checked = 0
    for route in _routes(app):
        op_id = _operation_id(route)
        match = re.search(r"Allowed roles: ([a-z_, ]+?)\.", OPERATIONS[op_id][2].get("description", ""))
        guards = [d.call.allowed_roles for d in route.dependant.dependencies if hasattr(d.call, "allowed_roles")]
        if match is None:
            assert not guards, f"{op_id} has a role guard but the contract lists no roles"
            continue
        assert guards, f"{op_id} has no role guard"
        assert {r.value for r in guards[0]} == set(match.group(1).replace(" ", "").split(",")), op_id
        checked += 1
    assert checked >= 15


def test_health_is_the_only_unauthenticated_operation(app):
    open_ops = {o for o, (_, _, op) in OPERATIONS.items() if op.get("security") == []}
    assert open_ops == {"getHealth"}
    assert CONTRACT["security"] == [{"bearerAuth": []}]
