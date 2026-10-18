import json, sys

R = lambda n: {"$ref": f"#/components/schemas/{n}"}
S = {"type": "string"}
def s(**k): return {"type": "string", **k}
def i(**k): return {"type": "integer", **k}
def n(**k): return {"type": "number", **k}
B = {"type": "boolean"}
def arr(x, **k): return {"type": "array", "items": x, **k}
DT = s(format="date-time", description="ISO 8601 UTC timestamp")
UUID = s(format="uuid")
def obj(props, req=None, desc=None, extra=True):
    o = {"type": "object", "properties": props}
    if req: o["required"] = req
    if desc: o["description"] = desc
    if not extra: o["additionalProperties"] = False
    return o
def enum(*v, **k): return {"type": "string", "enum": list(v), **k}

schemas = {}

# ---------- shared ----------
schemas["Provenance"] = obj({
    "dataOrigin": enum("synthetic", "public_historical", "simulated", "user_entered", "model_generated"),
    "sourceSystem": s(examples=["dispatchz-seed-v1"]),
    "sourceTimestamp": DT,
    "retrievedAt": DT,
    "verificationStatus": enum("verified", "unverified", "unknown"),
    "stale": B,
    "staleAfterSeconds": i(minimum=0),
}, ["dataOrigin", "sourceTimestamp"], "Source provenance and freshness. All hackathon data is synthetic or simulated.")
schemas["Error"] = obj({
    "code": enum("VALIDATION_ERROR", "NOT_FOUND", "UNAUTHENTICATED", "FORBIDDEN", "STALE_DATA",
                 "CONFLICT", "IDEMPOTENCY_KEY_REUSED", "UNIT_UNAVAILABLE", "INVALID_STATE_TRANSITION",
                 "RECOMMENDATION_OUTDATED", "INFERENCE_UNAVAILABLE", "RATE_LIMITED", "INTERNAL_ERROR"),
    "message": s(),
    "requestId": s(),
    "details": arr(obj({"field": s(), "issue": s()}, ["issue"])),
    "timestamp": DT,
}, ["code", "message", "requestId"], "Structured error body used by every non-2xx response.")
schemas["PageInfo"] = obj({
    "nextCursor": {"type": ["string", "null"]},
    "limit": i(),
    "totalEstimate": i(minimum=0),
}, ["nextCursor", "limit"])

def list_of(name):
    schemas[name + "List"] = obj({"data": arr(R(name)), "page": R("PageInfo")}, ["data", "page"])

# ---------- auth / health ----------
ROLES = ["call_taker", "dispatcher", "supervisor", "hr_training_admin", "fleet_equipment_admin",
         "department_leadership", "system_admin"]
schemas["Role"] = enum(*ROLES)
schemas["User"] = obj({
    "userId": UUID, "displayName": s(), "tenantId": UUID, "agencyIds": arr(UUID),
    "roles": arr(R("Role"), minItems=1), "permissions": arr(s()),
    "authMode": enum("demo_adapter", "oidc"),
}, ["userId", "displayName", "tenantId", "roles", "authMode"],
    "Current principal. authMode=demo_adapter is NOT production authentication.")
schemas["Health"] = obj({
    "status": enum("ok", "degraded", "down"), "version": s(), "time": DT,
    "components": arr(obj({"name": s(examples=["database", "inference", "event_stream"]),
                           "status": enum("ok", "degraded", "down"), "detail": s()}, ["name", "status"])),
    "architecture": s(examples=["s390x"]), "syntheticDataOnly": B,
}, ["status", "version", "time"])

# ---------- incidents ----------
INC_STATUS = ["intake", "pending_dispatch", "recommendation_ready", "dispatched_simulated", "on_scene_simulated", "resolved", "cancelled"]
DISC = ["fire", "police", "ems", "rescue", "hazmat", "other"]
schemas["GeoPoint"] = obj({"lat": n(minimum=-90, maximum=90), "lon": n(minimum=-180, maximum=180)}, ["lat", "lon"])
schemas["Location"] = obj({"address": s(maxLength=300), "point": R("GeoPoint"),
                           "jurisdictionId": UUID, "geocodeConfidence": n(minimum=0, maximum=1)}, ["point"])
schemas["Incident"] = obj({
    "incidentId": UUID, "tenantId": UUID, "number": s(examples=["INC-2026-000142"]),
    "status": enum(*INC_STATUS), "priority": enum("P1", "P2", "P3", "P4", "unassigned"),
    "priorityConfirmed": B,
    "confirmedCategory": {"anyOf": [R("IncidentCategory"), {"type": "null"}]},
    "narrative": s(maxLength=8000), "location": R("Location"),
    "callbackNumber": {"type": ["string", "null"], "description": "Synthetic or masked in the demo."},
    "version": i(minimum=1, description="Optimistic-concurrency version."),
    "createdAt": DT, "updatedAt": DT, "createdBy": UUID,
    "provenance": R("Provenance"),
}, ["incidentId", "number", "status", "priority", "narrative", "location", "version", "createdAt", "updatedAt", "provenance"])
schemas["IncidentCategory"] = enum("structure_fire", "vehicle_fire", "wildland_fire", "medical_cardiac", "medical_trauma",
                                   "medical_other", "traffic_collision", "technical_rescue", "hazmat", "assault",
                                   "burglary", "domestic_disturbance", "welfare_check", "other")
schemas["IncidentCreate"] = obj({
    "narrative": s(minLength=1, maxLength=8000), "location": R("Location"),
    "callbackNumber": s(maxLength=32),
    "reportedCategory": R("IncidentCategory"), "priority": enum("P1", "P2", "P3", "P4"),
    "isSynthetic": {"type": "boolean", "const": True, "description": "Must be true during the hackathon."},
}, ["narrative", "location", "isSynthetic"], extra=False)
schemas["IncidentUpdate"] = obj({
    "version": i(minimum=1, description="Expected current version; mismatch yields 409 CONFLICT."),
    "status": enum(*INC_STATUS), "priority": enum("P1", "P2", "P3", "P4"),
    "priorityConfirmed": B, "confirmedCategory": R("IncidentCategory"),
    "narrative": s(maxLength=8000), "location": R("Location"), "callbackNumber": s(maxLength=32),
}, ["version"], extra=False)
list_of("Incident")
schemas["IncidentEvent"] = obj({
    "eventId": UUID, "incidentId": UUID, "sequence": i(minimum=1),
    "type": enum("created", "updated", "status_changed", "classification_added", "recommendation_created",
                 "recommendation_invalidated", "review_recorded", "unit_assigned_simulated", "note"),
    "actorId": {"type": ["string", "null"], "format": "uuid"}, "occurredAt": DT,
    "summary": s(), "data": {"type": "object", "additionalProperties": True},
}, ["eventId", "incidentId", "sequence", "type", "occurredAt"])
list_of("IncidentEvent")

schemas["Classification"] = obj({
    "classificationId": UUID, "incidentId": UUID,
    "source": enum("ml_inferred", "rule_based", "dispatcher_confirmed"),
    "category": R("IncidentCategory"), "suggestedPriority": enum("P1", "P2", "P3", "P4"),
    "confidence": n(minimum=0, maximum=1, description="Null-equivalent (omitted) for dispatcher_confirmed."),
    "modelVersion": s(), "policyVersionId": UUID,
    "status": enum("suggested", "confirmed", "rejected", "superseded"),
    "confirmedBy": UUID, "createdAt": DT, "limitations": arr(s()),
}, ["classificationId", "incidentId", "source", "category", "status", "createdAt"],
    "Inferred and dispatcher-confirmed classifications are separate records.")
schemas["ClassificationCreate"] = obj({
    "mode": enum("infer", "confirm", "reject"),
    "category": R("IncidentCategory"), "priority": enum("P1", "P2", "P3", "P4"),
    "inferredClassificationId": UUID, "note": s(maxLength=1000),
}, ["mode"], "mode=infer asks the backend to run inference; confirm/reject records a human decision.")

CAP_STATE = enum("available", "unavailable", "expired", "missing", "unverified")
schemas["CapabilityRequirement"] = obj({
    "capabilityId": s(examples=["CAP-HAZMAT-TECH"]), "label": s(),
    "kind": enum("personnel_qualification", "apparatus", "equipment", "crew_size", "authority"),
    "quantity": i(minimum=1), "mandatory": B,
    "origin": enum("protocol_rule", "dispatcher_added", "ml_suggested"),
    "standardRef": s(examples=["FEMA NIMS RTL: Engine, Type 1"]),
}, ["capabilityId", "kind", "mandatory", "origin"])
schemas["RequirementSet"] = obj({
    "incidentId": UUID, "policyVersionId": UUID, "generatedAt": DT,
    "requirements": arr(R("CapabilityRequirement")), "basedOnClassificationId": UUID,
}, ["incidentId", "policyVersionId", "requirements", "generatedAt"])

schemas["CapabilityCheck"] = obj({
    "capabilityId": s(), "state": CAP_STATE, "evidence": s(), "expiresAt": DT, "mandatory": B,
}, ["capabilityId", "state", "mandatory"])
schemas["Candidate"] = obj({
    "unitId": UUID, "callSign": s(), "eligible": B,
    "rank": {"type": ["integer", "null"], "minimum": 1},
    "etaSeconds": {"type": ["integer", "null"], "minimum": 0},
    "etaMethod": enum("xgboost", "routing_estimate", "straight_line_fallback"),
    "checks": arr(R("CapabilityCheck")),
    "ineligibleReasons": arr(s()), "rankingFactors": arr(obj({"factor": s(), "value": s(), "note": s()}, ["factor"])),
    "coverageImpact": s(), "dataFreshness": R("Provenance"),
}, ["unitId", "callSign", "eligible", "checks"])
REC_STATUS = ["pending_review", "accepted", "modified", "rejected", "invalidated", "expired"]
schemas["Recommendation"] = obj({
    "recommendationId": UUID, "incidentId": UUID, "status": enum(*REC_STATUS),
    "stage": enum("hard_eligibility", "prioritization", "multi_incident_optimization"),
    "candidates": arr(R("Candidate")),
    "packages": arr(obj({"packageId": UUID, "unitIds": arr(UUID), "satisfiesAllMandatory": B,
                         "worstEtaSeconds": i(), "explanation": s()}, ["packageId", "unitIds", "satisfiesAllMandatory"])),
    "inputs": obj({"requirementSetGeneratedAt": DT, "unitSnapshotAt": DT, "classificationId": UUID,
                   "modelVersions": arr(s()), "optimizer": s(examples=["ortools-cp-sat"])}),
    "policyVersionId": UUID, "confidence": n(minimum=0, maximum=1),
    "limitations": arr(s()), "invalidatedReason": s(),
    "createdAt": DT, "expiresAt": DT, "provenance": R("Provenance"),
    "humanConfirmationRequired": {"type": "boolean", "const": True},
}, ["recommendationId", "incidentId", "status", "stage", "candidates", "policyVersionId", "inputs", "createdAt", "humanConfirmationRequired"],
    "Decision support only. Never an autonomous dispatch order.")
list_of("Recommendation")
schemas["RecommendationCreate"] = obj({
    "scope": enum("single_incident", "multi_incident"),
    "additionalIncidentIds": arr(UUID), "maxCandidates": i(minimum=1, maximum=50, default=10),
    "excludeUnitIds": arr(UUID), "policyVersionId": UUID,
})
schemas["ReviewCreate"] = obj({
    "decision": enum("accept", "modify", "reject"),
    "selectedUnitIds": arr(UUID), "explanation": s(maxLength=2000),
    "overrideReason": s(maxLength=2000, description="Required when selecting an ineligible or lower-ranked unit."),
    "humanConfirmed": {"type": "boolean", "const": True},
    "recommendationVersionSeenAt": DT,
}, ["decision", "humanConfirmed"], extra=False)
schemas["RecommendationReview"] = obj({
    "reviewId": UUID, "recommendationId": UUID, "reviewerId": UUID,
    "decision": enum("accept", "modify", "reject"), "selectedUnitIds": arr(UUID),
    "explanation": s(), "overrideReason": s(), "isOverride": B,
    "simulatedAssignment": B, "createdAt": DT,
}, ["reviewId", "recommendationId", "reviewerId", "decision", "isOverride", "simulatedAssignment", "createdAt"],
    "simulatedAssignment is always true: no CAD integration.")

# ---------- resources ----------
UNIT_STATUS = ["available", "dispatched_simulated", "en_route_simulated", "on_scene_simulated", "returning",
               "out_of_service", "off_duty"]
schemas["Unit"] = obj({
    "unitId": UUID, "agencyId": UUID, "callSign": s(examples=["E12"]),
    "discipline": enum(*DISC[:3]), "unitType": s(examples=["Engine Type 1", "ALS Ambulance"]),
    "status": enum(*UNIT_STATUS), "stationId": UUID, "apparatusId": UUID,
    "location": R("GeoPoint"), "crewPersonnelIds": arr(UUID), "minimumCrew": i(minimum=1),
    "version": i(), "provenance": R("Provenance"),
}, ["unitId", "agencyId", "callSign", "discipline", "status", "provenance"])
list_of("Unit")
schemas["Facility"] = obj({
    "facilityId": UUID, "agencyId": UUID, "name": s(), "type": enum("fire_station", "police_station", "ems_station", "hospital", "eoc"),
    "location": R("GeoPoint"), "serviceAreaId": UUID, "provenance": R("Provenance"),
}, ["facilityId", "agencyId", "name", "type", "location", "provenance"])
list_of("Facility")
schemas["Apparatus"] = obj({
    "apparatusId": UUID, "agencyId": UUID, "identifier": s(), "type": s(examples=["Engine Type 1"]),
    "nimsTypeRef": s(), "readiness": enum("ready", "limited", "maintenance", "out_of_service"),
    "equipment": arr(obj({"equipmentId": UUID, "name": s(), "capabilityIds": arr(s()),
                          "readiness": enum("ready", "limited", "maintenance", "missing"), "lastInspectedAt": DT}, ["equipmentId", "name", "readiness"])),
    "provenance": R("Provenance"),
}, ["apparatusId", "agencyId", "identifier", "type", "readiness", "provenance"])
list_of("Apparatus")
schemas["CapabilityDefinition"] = obj({
    "capabilityId": s(), "label": s(), "description": s(),
    "kind": enum("personnel_qualification", "apparatus", "equipment", "crew_size", "authority"),
    "standardRefs": arr(s(), description="e.g. FEMA NIMS RTL, NFPA 1001, NEMSIS scope of practice."),
    "requiresCredentialDefinitionIds": arr(s()),
}, ["capabilityId", "label", "kind"])
list_of("CapabilityDefinition")
schemas["UnitCapabilities"] = obj({
    "unitId": UUID, "evaluatedAt": DT, "capabilities": arr(obj({
        "capabilityId": s(), "state": CAP_STATE, "evidence": s(), "expiresAt": DT, "provenance": R("Provenance")},
        ["capabilityId", "state"])),
}, ["unitId", "evaluatedAt", "capabilities"])
schemas["UnitStatusEventCreate"] = obj({
    "status": enum(*UNIT_STATUS), "reason": s(maxLength=500), "location": R("GeoPoint"),
    "effectiveAt": DT, "expectedUnitVersion": i(),
}, ["status"], extra=False)
schemas["UnitStatusEvent"] = obj({
    "statusEventId": UUID, "unitId": UUID, "status": enum(*UNIT_STATUS), "previousStatus": enum(*UNIT_STATUS),
    "reason": s(), "recordedBy": UUID, "effectiveAt": DT, "recordedAt": DT,
    "invalidatedRecommendationIds": arr(UUID),
}, ["statusEventId", "unitId", "status", "recordedAt"])

# ---------- personnel ----------
schemas["Personnel"] = obj({
    "personnelId": UUID, "agencyId": UUID, "displayName": s(description="Synthetic name."),
    "role": s(examples=["Firefighter/Paramedic"]), "stationId": UUID, "employmentStatus": enum("active", "leave", "inactive"),
    "provenance": R("Provenance"),
}, ["personnelId", "agencyId", "displayName", "role", "employmentStatus", "provenance"])
list_of("Personnel")
schemas["Credential"] = obj({
    "credentialId": UUID, "personnelId": UUID, "credentialDefinitionId": s(examples=["CRED-NREMT-PARAMEDIC"]),
    "label": s(), "state": enum("valid", "expired", "unverified", "revoked"),
    "issuedAt": DT, "expiresAt": DT, "verifiedBy": UUID, "verifiedAt": DT,
    "version": i(), "provenance": R("Provenance"),
}, ["credentialId", "personnelId", "credentialDefinitionId", "state", "provenance"])
list_of("Credential")
schemas["CredentialCreate"] = obj({
    "credentialDefinitionId": s(), "issuedAt": DT, "expiresAt": DT,
    "evidenceRef": s(), "verified": B,
}, ["credentialDefinitionId"], extra=False)
schemas["CredentialUpdate"] = obj({
    "version": i(), "state": enum("valid", "expired", "unverified", "revoked"),
    "expiresAt": DT, "verified": B, "evidenceRef": s(),
}, ["version"], extra=False)

# ---------- planning ----------
schemas["ForecastPoint"] = obj({"start": DT, "end": DT, "predicted": n(minimum=0), "lower": n(), "upper": n(),
                                "observed": {"type": ["number", "null"]}}, ["start", "end", "predicted"])
schemas["Forecast"] = obj({
    "forecastId": UUID, "serviceAreaId": UUID, "discipline": enum(*DISC),
    "granularity": enum("hour", "day"), "generatedAt": DT,
    "model": obj({"name": s(examples=["granite-timeseries-ttm-r2"]), "framework": enum("pytorch", "xgboost", "baseline"), "version": s()}, ["name", "framework"]),
    "points": arr(R("ForecastPoint")), "baseline": arr(R("ForecastPoint")),
    "evaluation": obj({"mae": n(), "baselineMae": n(), "note": s()}),
    "disclaimer": s(examples=["Forecasts emergency service demand, not individual behaviour. Trained/evaluated on synthetic data; not operational accuracy."]),
    "provenance": R("Provenance"),
}, ["forecastId", "serviceAreaId", "granularity", "model", "points", "generatedAt", "provenance"])
list_of("Forecast")
schemas["CoverageCell"] = obj({"serviceAreaId": UUID, "windowStart": DT, "windowEnd": DT, "capabilityId": s(),
                               "required": i(), "scheduled": i(), "available": i(), "dataKind": enum("observed", "predicted", "scenario")},
                              ["serviceAreaId", "windowStart", "windowEnd", "capabilityId", "required", "scheduled", "dataKind"])
schemas["CoverageReport"] = obj({"generatedAt": DT, "cells": arr(R("CoverageCell")), "provenance": R("Provenance")}, ["generatedAt", "cells"])
schemas["CoverageGap"] = obj({
    "gapId": UUID, "capabilityId": s(), "serviceAreaId": UUID, "shiftId": UUID,
    "cause": enum("missing_qualification", "equipment_unavailable", "insufficient_staff", "mutual_aid_dependency", "unknown"),
    "occurrences": i(minimum=1), "firstObservedAt": DT, "lastObservedAt": DT,
    "dataKind": enum("observed", "predicted"), "evidence": arr(s()), "provenance": R("Provenance"),
}, ["gapId", "capabilityId", "cause", "occurrences", "dataKind"])
list_of("CoverageGap")
schemas["Shift"] = obj({"shiftId": UUID, "stationId": UUID, "label": s(examples=["A-Shift"]), "start": DT, "end": DT,
                        "assignedPersonnelIds": arr(UUID), "minimumStaffing": i()}, ["shiftId", "stationId", "start", "end"])
list_of("Shift")
schemas["ScenarioAssumption"] = obj({"key": s(), "value": {}, "unit": s(), "rationale": s()}, ["key", "value"])
schemas["ScenarioCreate"] = obj({
    "name": s(maxLength=200), "type": enum("shift_adjustment", "training_intervention", "equipment_purchase", "mutual_aid_change", "staffing_change"),
    "assumptions": arr(R("ScenarioAssumption")), "baselineForecastId": UUID,
    "constraints": obj({"minimumStaffing": i(), "maxConsecutiveHours": i(), "budgetLimit": n()}),
}, ["name", "type", "assumptions"])
schemas["Scenario"] = obj({
    "scenarioId": UUID, "name": s(), "type": s(), "assumptions": arr(R("ScenarioAssumption")),
    "createdBy": UUID, "createdAt": DT, "latestEvaluationId": UUID,
    "provenance": R("Provenance"),
}, ["scenarioId", "name", "type", "assumptions", "createdAt"])
schemas["ScenarioEvaluation"] = obj({
    "evaluationId": UUID, "scenarioId": UUID, "evaluatedAt": DT,
    "baseline": R("CoverageReport"), "projected": R("CoverageReport"),
    "gapsResolved": i(), "gapsRemaining": i(), "estimatedCost": n(), "currency": s(examples=["USD"]),
    "risks": arr(s()), "dataKind": {"type": "string", "const": "scenario"},
    "disclaimer": s(examples=["Simulated outcome under stated assumptions; not a guarantee of improved safety."]),
}, ["evaluationId", "scenarioId", "evaluatedAt", "projected", "dataKind", "disclaimer"])

# ---------- insights ----------
schemas["ReadinessSummary"] = obj({
    "generatedAt": DT, "agencyId": UUID, "overallReadiness": n(minimum=0, maximum=1),
    "byCapability": arr(obj({"capabilityId": s(), "readiness": n(minimum=0, maximum=1), "trend": enum("improving", "stable", "declining")}, ["capabilityId", "readiness"])),
    "mutualAidDependencyRate": n(minimum=0, maximum=1), "provenance": R("Provenance"),
}, ["generatedAt", "overallReadiness", "byCapability"])
schemas["TrainingGap"] = obj({"capabilityId": s(), "credentialDefinitionId": s(), "qualifiedCount": i(), "requiredCount": i(),
                              "expiringWithin90Days": i(), "affectedShiftIds": arr(UUID), "provenance": R("Provenance")},
                             ["capabilityId", "qualifiedCount", "requiredCount"])
schemas["EquipmentGap"] = obj({"capabilityId": s(), "equipmentType": s(), "readyCount": i(), "requiredCount": i(),
                               "downtimeHours30d": n(), "provenance": R("Provenance")}, ["capabilityId", "readyCount", "requiredCount"])
list_of("TrainingGap"); list_of("EquipmentGap")
schemas["LeadershipRecommendation"] = obj({
    "recommendationId": UUID, "title": s(), "summary": s(),
    "category": enum("training", "equipment", "staffing", "scheduling", "mutual_aid"),
    "supportingObservations": arr(obj({"description": s(), "gapId": UUID, "metric": s(), "value": n()}, ["description"]), minItems=1),
    "assumptions": arr(R("ScenarioAssumption")), "alternatives": arr(s()),
    "relatedScenarioId": UUID, "advisoryOnly": {"type": "boolean", "const": True},
    "generatedAt": DT, "provenance": R("Provenance"),
}, ["recommendationId", "title", "summary", "category", "supportingObservations", "assumptions", "advisoryOnly", "generatedAt"],
    "Advisory only. Does not authorize spending, assignments, or protocol changes.")
list_of("LeadershipRecommendation")

# ---------- audit / admin ----------
schemas["AuditEvent"] = obj({
    "auditId": UUID, "occurredAt": DT, "actorId": UUID, "actorRoles": arr(R("Role")),
    "action": s(examples=["recommendation.review.accept"]), "entityType": s(), "entityId": s(),
    "outcome": enum("success", "denied", "error"), "requestId": s(), "tenantId": UUID,
    "detail": {"type": "object", "additionalProperties": True},
}, ["auditId", "occurredAt", "action", "entityType", "outcome"])
list_of("AuditEvent")
schemas["Policy"] = obj({
    "policyId": UUID, "name": s(), "kind": enum("dispatch_protocol", "eligibility_rules", "prioritization", "optimization", "model_config"),
    "version": s(examples=["1.0.0"]), "status": enum("draft", "active", "retired"),
    "effectiveFrom": DT, "supersedesPolicyId": UUID, "definition": {"type": "object", "additionalProperties": True},
    "standardRefs": arr(s()),
}, ["policyId", "name", "kind", "version", "status"])
list_of("Policy")

# ---------- SSE ----------
EVT = ["incident.created", "incident.updated", "unit.status_changed", "recommendation.created",
       "recommendation.invalidated", "coverage.gap_detected", "stream.resync_required", "stream.heartbeat"]
schemas["EventEnvelope"] = obj({
    "id": s(description="Monotonically increasing per-tenant sequence, also sent as SSE `id:` for Last-Event-ID replay.", examples=["000000000123"]),
    "type": enum(*EVT), "timestamp": DT,
    "entity": obj({"type": enum("incident", "unit", "recommendation", "coverage_gap", "none"), "id": s()}, ["type"]),
    "version": i(minimum=1, description="Entity version after this change."),
    "schemaVersion": s(examples=["1"]), "payload": {"type": "object", "additionalProperties": True},
}, ["id", "type", "timestamp", "entity", "version", "payload"],
    "Common envelope for every SSE `data:` frame. Payloads per type are in docs/event-catalog.")

# ---------- parameters ----------
P = {}
def pp(name, where, schema, desc=None, req=False):
    d = {"name": name, "in": where, "required": req or where == "path", "schema": schema}
    if desc: d["description"] = desc
    return d
P["Limit"] = pp("limit", "query", i(minimum=1, maximum=200, default=50))
P["Cursor"] = pp("cursor", "query", s(), "Opaque cursor from page.nextCursor.")
P["Sort"] = pp("sort", "query", s(examples=["-createdAt"]), "Comma-separated fields; prefix '-' for descending.")
P["IdemKey"] = pp("Idempotency-Key", "header", s(minLength=8, maxLength=128), "Client-generated key; replays return the original response.", True)
P["IfMatch"] = pp("If-Match", "header", s(), "Entity version for optimistic concurrency.")
for nm in ["incidentId", "recommendationId", "unitId", "personnelId", "credentialId", "scenarioId", "policyId"]:
    P[nm[0].upper() + nm[1:]] = pp(nm, "path", UUID)
P["LastEventId"] = pp("Last-Event-ID", "header", s(), "Resume after this event id; server replays missed events or sends stream.resync_required.")
for k, v in P.items(): pass

def pref(n): return {"$ref": f"#/components/parameters/{n}"}
common_list = [pref("Limit"), pref("Cursor"), pref("Sort")]

# ---------- responses ----------
def err(desc): return {"description": desc, "content": {"application/json": {"schema": R("Error")}}}
responses = {
    "BadRequest": err("VALIDATION_ERROR"), "Unauthorized": err("UNAUTHENTICATED"),
    "Forbidden": err("FORBIDDEN"), "NotFound": err("NOT_FOUND"),
    "Conflict": err("CONFLICT, UNIT_UNAVAILABLE, INVALID_STATE_TRANSITION, RECOMMENDATION_OUTDATED, IDEMPOTENCY_KEY_REUSED or STALE_DATA"),
    "Unavailable": err("INFERENCE_UNAVAILABLE or service degraded"),
    "InternalError": err("INTERNAL_ERROR"),
}

def jc(name, **kw): return {"required": True, "content": {"application/json": {"schema": R(name), **kw}}}
def ok(code, desc, name):
    return {str(code): {"description": desc, "content": {"application/json": {"schema": R(name)}}}}
def e(*codes):
    m = {"400": "BadRequest", "401": "Unauthorized", "403": "Forbidden", "404": "NotFound", "409": "Conflict", "503": "Unavailable"}
    return {c: {"$ref": f"#/components/responses/{m[c]}"} for c in codes}

paths = {}
def op(path, method, opid, summary, tag, resp, params=None, body=None, roles=None, desc=None, errs=("401", "403")):
    o = {"operationId": opid, "summary": summary, "tags": [tag]}
    d = desc or ""
    if roles: d = (d + " " if d else "") + "Allowed roles: " + ", ".join(roles) + "."
    if d: o["description"] = d
    if params: o["parameters"] = params
    if body: o["requestBody"] = body
    o["responses"] = {**resp, **e(*errs)}
    if roles: o["x-allowed-roles"] = roles
    paths.setdefault(path, {})[method] = o

A = "/api/v1"
ALL = ROLES
OPS = ["call_taker", "dispatcher", "supervisor", "system_admin"]

op(f"{A}/auth/me", "get", "getCurrentUser", "Current user", "Auth", ok(200, "Principal", "User"), roles=ALL)
op(f"{A}/health", "get", "getHealth", "Service health", "Auth", ok(200, "Healthy or degraded", "Health"),
   desc="Unauthenticated liveness/readiness for deployment health checks.", errs=("503",))
paths[f"{A}/health"]["get"]["security"] = []
# A failing readiness probe returns the Health body so the failing component is visible to the caller.
paths[f"{A}/health"]["get"]["responses"]["503"] = {
    "description": "Not ready; `components` reports which dependency is down.",
    "content": {"application/json": {"schema": R("Health")}},
}

# incidents
op(f"{A}/incidents", "get", "listIncidents", "List incidents", "Incidents", ok(200, "Page of incidents", "IncidentList"),
   params=common_list + [pp("status", "query", arr(enum(*INC_STATUS))),
                         pp("priority", "query", enum("P1", "P2", "P3", "P4", "unassigned")),
                         pp("category", "query", R("IncidentCategory")),
                         pp("createdAfter", "query", DT), pp("createdBefore", "query", DT),
                         pp("q", "query", s(maxLength=200), "Full-text search over narrative and address.")],
   roles=OPS + ["hr_training_admin"][:0], errs=("400", "401", "403"))
op(f"{A}/incidents", "post", "createIncident", "Create incident (synthetic)", "Incidents", ok(201, "Created", "Incident"),
   params=[pref("IdemKey")], body=jc("IncidentCreate"), roles=["call_taker", "dispatcher", "system_admin"], errs=("400", "401", "403", "409"))
op(f"{A}/incidents/{{incidentId}}", "get", "getIncident", "Get incident", "Incidents", ok(200, "Incident", "Incident"),
   params=[pref("IncidentId")], roles=OPS, errs=("401", "403", "404"))
op(f"{A}/incidents/{{incidentId}}", "patch", "updateIncident", "Update incident", "Incidents", ok(200, "Updated", "Incident"),
   params=[pref("IncidentId")], body=jc("IncidentUpdate"), roles=["call_taker", "dispatcher", "supervisor", "system_admin"],
   desc="Status transitions are validated; invalid transitions return INVALID_STATE_TRANSITION. Priority confirmation is restricted to dispatchers and supervisors.",
   errs=("400", "401", "403", "404", "409"))
op(f"{A}/incidents/{{incidentId}}/events", "get", "listIncidentEvents", "Incident timeline", "Incidents", ok(200, "Events", "IncidentEventList"),
   params=[pref("IncidentId")] + common_list, roles=OPS, errs=("401", "403", "404"))

op(f"{A}/incidents/{{incidentId}}/classifications", "post", "createClassification", "Infer or confirm classification", "Intelligence",
   {**ok(201, "Classification recorded", "Classification")}, params=[pref("IncidentId")], body=jc("ClassificationCreate"),
   roles=["call_taker", "dispatcher", "supervisor"], desc="Inferred suggestions stay `suggested` until a human confirms them. May return 503 INFERENCE_UNAVAILABLE; clients then fall back to manual entry.",
   errs=("400", "401", "403", "404", "409", "503"))
op(f"{A}/incidents/{{incidentId}}/requirements", "get", "getIncidentRequirements", "Required capabilities", "Intelligence",
   ok(200, "Requirements", "RequirementSet"), params=[pref("IncidentId")], roles=OPS, errs=("401", "403", "404"))
op(f"{A}/incidents/{{incidentId}}/recommendations", "get", "listIncidentRecommendations", "List recommendations", "Intelligence",
   ok(200, "Recommendations", "RecommendationList"), params=[pref("IncidentId")] + common_list, roles=OPS, errs=("401", "403", "404"))
op(f"{A}/incidents/{{incidentId}}/recommendations", "post", "createRecommendation", "Generate recommendation", "Intelligence",
   ok(201, "Recommendation generated", "Recommendation"), params=[pref("IncidentId"), pref("IdemKey")], body=jc("RecommendationCreate"),
   roles=["dispatcher", "supervisor"], desc="Runs hard eligibility, prioritization, and optional optimization. Mandatory eligibility is never overridden by ranking.",
   errs=("400", "401", "403", "404", "409", "503"))

op(f"{A}/recommendations/{{recommendationId}}", "get", "getRecommendation", "Get recommendation", "Recommendations",
   ok(200, "Recommendation", "Recommendation"), params=[pref("RecommendationId")], roles=OPS, errs=("401", "403", "404"))
op(f"{A}/recommendations/{{recommendationId}}/reviews", "post", "createRecommendationReview", "Record human review", "Recommendations",
   ok(201, "Review recorded (simulated assignment only)", "RecommendationReview"),
   params=[pref("RecommendationId"), pref("IdemKey")], body=jc("ReviewCreate"), roles=["dispatcher", "supervisor"],
   desc="Duplicate submissions are harmful, so Idempotency-Key is required. Returns 409 UNIT_UNAVAILABLE or RECOMMENDATION_OUTDATED when inputs went stale.",
   errs=("400", "401", "403", "404", "409"))

# resources
rl = ["dispatcher", "supervisor", "fleet_equipment_admin", "hr_training_admin", "department_leadership", "system_admin"]
op(f"{A}/resources/units", "get", "listUnits", "List units", "Resources", ok(200, "Units", "UnitList"),
   params=common_list + [pp("status", "query", arr(enum(*UNIT_STATUS))), pp("discipline", "query", enum(*DISC[:3])),
                         pp("agencyId", "query", UUID), pp("stationId", "query", UUID), pp("capabilityId", "query", s())],
   roles=rl + ["call_taker"][:0], errs=("400", "401", "403"))
op(f"{A}/resources/units/{{unitId}}", "get", "getUnit", "Get unit", "Resources", ok(200, "Unit", "Unit"),
   params=[pref("UnitId")], roles=rl, errs=("401", "403", "404"))
op(f"{A}/resources/facilities", "get", "listFacilities", "List facilities", "Resources", ok(200, "Facilities", "FacilityList"),
   params=common_list + [pp("type", "query", enum("fire_station", "police_station", "ems_station", "hospital", "eoc"))], roles=ALL, errs=("400", "401", "403"))
op(f"{A}/resources/apparatus", "get", "listApparatus", "List apparatus and equipment", "Resources", ok(200, "Apparatus", "ApparatusList"),
   params=common_list + [pp("readiness", "query", enum("ready", "limited", "maintenance", "out_of_service"))], roles=rl, errs=("400", "401", "403"))
op(f"{A}/resources/capabilities", "get", "listCapabilityDefinitions", "Capability registry", "Resources", ok(200, "Definitions", "CapabilityDefinitionList"),
   params=common_list, roles=ALL, errs=("401", "403"))
op(f"{A}/resources/units/{{unitId}}/capabilities", "get", "getUnitCapabilities", "Unit capability status", "Resources",
   ok(200, "Per-capability state", "UnitCapabilities"), params=[pref("UnitId")], roles=rl, errs=("401", "403", "404"))
op(f"{A}/resources/units/{{unitId}}/status-events", "post", "createUnitStatusEvent", "Record unit status (simulated)", "Resources",
   ok(201, "Status recorded", "UnitStatusEvent"), params=[pref("UnitId"), pref("IdemKey")], body=jc("UnitStatusEventCreate"),
   roles=["dispatcher", "supervisor", "fleet_equipment_admin", "system_admin"],
   desc="May invalidate pending recommendations that include this unit (emits recommendation.invalidated).", errs=("400", "401", "403", "404", "409"))

# personnel
pl = ["supervisor", "hr_training_admin", "department_leadership", "system_admin"]
op(f"{A}/personnel", "get", "listPersonnel", "List personnel", "Personnel", ok(200, "Personnel", "PersonnelList"),
   params=common_list + [pp("agencyId", "query", UUID), pp("stationId", "query", UUID), pp("credentialDefinitionId", "query", s())],
   roles=pl, errs=("400", "401", "403"))
op(f"{A}/personnel/{{personnelId}}", "get", "getPersonnel", "Get personnel", "Personnel", ok(200, "Person", "Personnel"),
   params=[pref("PersonnelId")], roles=pl, errs=("401", "403", "404"))
op(f"{A}/personnel/{{personnelId}}/credentials", "get", "listCredentials", "List credentials", "Personnel", ok(200, "Credentials", "CredentialList"),
   params=[pref("PersonnelId")] + common_list, roles=pl, errs=("401", "403", "404"))
op(f"{A}/personnel/{{personnelId}}/credentials", "post", "createCredential", "Add credential", "Personnel", ok(201, "Created", "Credential"),
   params=[pref("PersonnelId"), pref("IdemKey")], body=jc("CredentialCreate"), roles=["hr_training_admin", "system_admin"], errs=("400", "401", "403", "404", "409"))
op(f"{A}/personnel/{{personnelId}}/credentials/{{credentialId}}", "patch", "updateCredential", "Update credential", "Personnel", ok(200, "Updated", "Credential"),
   params=[pref("PersonnelId"), pref("CredentialId")], body=jc("CredentialUpdate"), roles=["hr_training_admin", "system_admin"], errs=("400", "401", "403", "404", "409"))

# planning
pr = ["supervisor", "department_leadership", "hr_training_admin", "fleet_equipment_admin", "system_admin"]
op(f"{A}/planning/forecasts", "get", "listForecasts", "Demand forecasts", "Planning", ok(200, "Forecasts", "ForecastList"),
   params=common_list + [pp("serviceAreaId", "query", UUID), pp("discipline", "query", enum(*DISC)),
                         pp("from", "query", DT), pp("to", "query", DT)], roles=pr, errs=("400", "401", "403"))
op(f"{A}/planning/coverage", "get", "getCoverage", "Coverage matrix", "Planning", ok(200, "Coverage", "CoverageReport"),
   params=[pp("serviceAreaId", "query", UUID), pp("from", "query", DT), pp("to", "query", DT),
           pp("dataKind", "query", enum("observed", "predicted", "scenario"))], roles=pr, errs=("400", "401", "403"))
op(f"{A}/planning/gaps", "get", "listCoverageGaps", "Recurring coverage gaps", "Planning", ok(200, "Gaps", "CoverageGapList"),
   params=common_list + [pp("cause", "query", s()), pp("capabilityId", "query", s())], roles=pr, errs=("400", "401", "403"))
op(f"{A}/planning/shifts", "get", "listShifts", "Shifts", "Planning", ok(200, "Shifts", "ShiftList"),
   params=common_list + [pp("stationId", "query", UUID), pp("from", "query", DT), pp("to", "query", DT)], roles=pr, errs=("400", "401", "403"))
op(f"{A}/planning/scenarios", "post", "createScenario", "Create scenario", "Planning", ok(201, "Created", "Scenario"),
   params=[pref("IdemKey")], body=jc("ScenarioCreate"), roles=["supervisor", "department_leadership", "system_admin"], errs=("400", "401", "403"))
op(f"{A}/planning/scenarios/{{scenarioId}}", "get", "getScenario", "Get scenario", "Planning", ok(200, "Scenario", "Scenario"),
   params=[pref("ScenarioId")], roles=pr, errs=("401", "403", "404"))
op(f"{A}/planning/scenarios/{{scenarioId}}/evaluations", "post", "createScenarioEvaluation", "Evaluate scenario", "Planning",
   ok(201, "Evaluation", "ScenarioEvaluation"), params=[pref("ScenarioId"), pref("IdemKey")],
   roles=["supervisor", "department_leadership", "system_admin"], errs=("401", "403", "404", "409", "503"))

# insights
il = ["department_leadership", "supervisor", "system_admin"]
op(f"{A}/insights/readiness", "get", "getReadiness", "Readiness summary", "Insights", ok(200, "Readiness", "ReadinessSummary"),
   params=[pp("agencyId", "query", UUID)], roles=il, errs=("401", "403"))
op(f"{A}/insights/training-gaps", "get", "listTrainingGaps", "Training gaps", "Insights", ok(200, "Gaps", "TrainingGapList"),
   params=common_list, roles=il + ["hr_training_admin"], errs=("401", "403"))
op(f"{A}/insights/equipment-gaps", "get", "listEquipmentGaps", "Equipment gaps", "Insights", ok(200, "Gaps", "EquipmentGapList"),
   params=common_list, roles=il + ["fleet_equipment_admin"], errs=("401", "403"))
op(f"{A}/insights/recommendations", "get", "listLeadershipRecommendations", "Advisory recommendations", "Insights",
   ok(200, "Recommendations", "LeadershipRecommendationList"), params=common_list, roles=il, errs=("401", "403"))

# audit/admin
op(f"{A}/audit/events", "get", "listAuditEvents", "Audit log", "Admin", ok(200, "Audit events", "AuditEventList"),
   params=common_list + [pp("actorId", "query", UUID), pp("entityType", "query", s()), pp("entityId", "query", s()),
                         pp("action", "query", s()), pp("from", "query", DT), pp("to", "query", DT)],
   roles=["supervisor", "system_admin"], errs=("400", "401", "403"))
op(f"{A}/admin/policies", "get", "listPolicies", "List policy versions", "Admin", ok(200, "Policies", "PolicyList"),
   params=common_list + [pp("kind", "query", s()), pp("status", "query", enum("draft", "active", "retired"))],
   roles=["supervisor", "system_admin"], errs=("401", "403"))
op(f"{A}/admin/policies/{{policyId}}", "get", "getPolicy", "Get policy", "Admin", ok(200, "Policy", "Policy"),
   params=[pref("PolicyId")], roles=["supervisor", "system_admin"], errs=("401", "403", "404"))

# SSE
op(f"{A}/events/stream", "get", "streamEvents", "Server-Sent Events stream", "Events", {
    "200": {"description": "text/event-stream. Each frame: `id: <EventEnvelope.id>`, `event: <type>`, `data: <EventEnvelope JSON>`. "
            "A `stream.heartbeat` is sent every 15 s. On reconnect with Last-Event-ID the server replays missed events (retention window configurable); "
            "if the id is too old it emits `stream.resync_required` and the client must refetch affected lists.",
            "headers": {"Cache-Control": {"schema": s(const="no-cache")}},
            "content": {"text/event-stream": {"schema": s(description="SSE frames whose data field is an EventEnvelope."),
                                              "x-frame-data-schema": R("EventEnvelope"),
                                              "example": "id: 000000000123\nevent: incident.created\ndata: {\"id\":\"000000000123\",\"type\":\"incident.created\",\"timestamp\":\"2026-03-01T14:02:11Z\",\"entity\":{\"type\":\"incident\",\"id\":\"3f0c1a52-8d4e-4e0b-9a55-1d2f3c4b5a60\"},\"version\":1,\"schemaVersion\":\"1\",\"payload\":{\"number\":\"INC-2026-000142\",\"priority\":\"unassigned\"}}\n\n"}}}},
   params=[pref("LastEventId"), pp("types", "query", arr(enum(*EVT)), "Subscribe only to these event types."),
           pp("lastEventId", "query", s(), "Fallback for clients that cannot set headers (e.g. native EventSource polyfills).")],
   roles=ALL, errs=("401", "403"))

# ---------- examples ----------
U1, U2, I1 = "9b1d6a30-5c7e-4a8a-b0f1-2a3c4d5e6f70", "c2e8f4a1-7b3d-4c59-8e12-6f7a8b9c0d11", "3f0c1a52-8d4e-4e0b-9a55-1d2f3c4b5a60"
PROV = {"dataOrigin": "synthetic", "sourceSystem": "dispatchz-seed-v1", "sourceTimestamp": "2026-03-01T14:00:00Z", "verificationStatus": "verified", "stale": False}
schemas["Incident"]["examples"] = [{
    "incidentId": I1, "number": "INC-2026-000142", "status": "pending_dispatch", "priority": "P1", "priorityConfirmed": True,
    "confirmedCategory": "structure_fire", "narrative": "Synthetic: smoke visible from second-floor window, caller reports a person trapped.",
    "location": {"address": "100 Example St, Fictional County", "point": {"lat": 47.6101, "lon": -122.3321}},
    "version": 3, "createdAt": "2026-03-01T14:02:11Z", "updatedAt": "2026-03-01T14:03:40Z", "provenance": PROV}]
schemas["Unit"]["examples"] = [{
    "unitId": U1, "agencyId": U2, "callSign": "E12", "discipline": "fire", "unitType": "Engine Type 1", "status": "available",
    "location": {"lat": 47.6062, "lon": -122.3321}, "minimumCrew": 4, "version": 7,
    "provenance": {**PROV, "dataOrigin": "simulated"}}]
schemas["UnitCapabilities"]["examples"] = [{
    "unitId": U1, "evaluatedAt": "2026-03-01T14:03:00Z",
    "capabilities": [{"capabilityId": "CAP-FIRE-SUPPRESSION", "state": "available"},
                     {"capabilityId": "CAP-TECH-RESCUE", "state": "expired", "evidence": "Credential CRED-TRT-1 expired 2026-02-10"},
                     {"capabilityId": "CAP-ALS", "state": "missing"}, {"capabilityId": "CAP-THERMAL-CAMERA", "state": "unverified"},
                     {"capabilityId": "CAP-HAZMAT-OPS", "state": "unavailable", "evidence": "Equipment in maintenance"}]}]
schemas["Recommendation"]["examples"] = [{
    "recommendationId": "5d9f7e10-2b64-4c1d-8a37-0e1f2a3b4c5d", "incidentId": I1, "status": "pending_review", "stage": "prioritization",
    "candidates": [{"unitId": U1, "callSign": "E12", "eligible": True, "rank": 1, "etaSeconds": 214, "etaMethod": "xgboost",
                    "checks": [{"capabilityId": "CAP-FIRE-SUPPRESSION", "state": "available", "mandatory": True}],
                    "ineligibleReasons": [], "rankingFactors": [{"factor": "eta", "value": "214s"}]},
                   {"unitId": U2, "callSign": "L4", "eligible": False, "rank": None, "etaSeconds": 190, "etaMethod": "routing_estimate",
                    "checks": [{"capabilityId": "CAP-TECH-RESCUE", "state": "expired", "mandatory": True}],
                    "ineligibleReasons": ["Mandatory credential expired"]}],
    "inputs": {"requirementSetGeneratedAt": "2026-03-01T14:03:00Z", "unitSnapshotAt": "2026-03-01T14:03:30Z", "modelVersions": ["xgb-eta-0.1"]},
    "policyVersionId": "a1b2c3d4-e5f6-4789-9abc-def012345678", "confidence": 0.82,
    "limitations": ["ETA model trained on synthetic data"], "createdAt": "2026-03-01T14:03:31Z", "humanConfirmationRequired": True}]
schemas["Error"]["examples"] = [{"code": "UNIT_UNAVAILABLE", "message": "Unit E12 is no longer available.", "requestId": "req_01HX", "details": [{"field": "selectedUnitIds[0]", "issue": "status=out_of_service"}]}]

# ---------- 0.1.1 additions (additive) ----------
schemas["IncidentCreate"]["properties"]["mockScenarioId"] = s(
    maxLength=64, description="Demo only: tags the incident with a scripted scenario (e.g. 'A', 'B') for reproducible demonstrations.")
schemas["DemoResetResult"] = obj({"resetAt": DT, "seedVersion": s(), "seed": i(), "counts": {"type": "object", "additionalProperties": i()}},
                                 ["resetAt", "seedVersion"])
op(f"{A}/admin/demo/reset", "post", "resetDemoData", "Reset and re-seed demo data", "Admin", ok(200, "Data re-seeded", "DemoResetResult"),
   roles=["system_admin"], desc="Demo/hackathon only. Disabled unless DEMO_MODE=true. Restores the deterministic synthetic seed.", errs=("401", "403"))

TIERS = {
 "mvp": ["getHealth", "getCurrentUser", "listUnits", "getUnit", "getUnitCapabilities", "listCapabilityDefinitions", "listFacilities",
         "createIncident", "listIncidents", "getIncident", "updateIncident", "createClassification", "getIncidentRequirements",
         "createRecommendation", "getRecommendation", "listIncidentRecommendations", "createRecommendationReview",
         "streamEvents", "createUnitStatusEvent", "resetDemoData"],
 "stretch": ["listIncidentEvents", "listAuditEvents", "listPersonnel", "getPersonnel", "listCredentials", "listCoverageGaps",
             "listForecasts", "listLeadershipRecommendations"],
}
t_of = {o: t for t, l in TIERS.items() for o in l}
for pth in paths.values():
    for o in pth.values():
        o["x-mvp-tier"] = t_of.get(o["operationId"], "future")
        # A malformed path parameter (e.g. a non-UUID id) is a client validation error.
        if any(P[p["$ref"].rsplit("/", 1)[-1]]["in"] == "path" for p in o.get("parameters", []) if "$ref" in p):
            o["responses"].setdefault("400", {"$ref": "#/components/responses/BadRequest"})
        # Unexpected server failure uses the same Error envelope on every operation.
        o["responses"].setdefault("500", {"$ref": "#/components/responses/InternalError"})

spec = {
    "openapi": "3.1.0",
    "info": {
        "title": "DispatchZ API", "version": "0.1.2",
        "summary": "Human-in-the-loop emergency dispatch decision support (IBM Z Datathon 2026).",
        "description": "Authoritative contract for DispatchZ Live, Planning and Insights.\n\n"
        "**Decision support only.** The API never issues real dispatch orders; any resulting unit assignment is a simulated action. "
        "All operational data is synthetic or simulated. Not CJIS-certified, HIPAA-compliant, or production-ready.\n\n"
        "Conventions: timestamps are ISO 8601 UTC; list endpoints use cursor pagination (`limit`, `cursor`) and `sort`; "
        "mutating endpoints that would be harmful if repeated require `Idempotency-Key`; entities carry `version` for optimistic concurrency; "
        "ML predictions, deterministic rules, optimizer output and human decisions are kept in separate fields/records.",
        "license": {"name": "Hackathon prototype", "identifier": "UNLICENSED"},
    },
    "servers": [{"url": "http://localhost:4010", "description": "Prism mock server"}, {"url": "http://localhost:8000", "description": "Local development"},
                {"url": "https://dispatchz.example.invalid", "description": "LinuxONE deployment (placeholder)"}],
    "security": [{"bearerAuth": []}],
    "tags": [{"name": t} for t in ["Auth", "Incidents", "Intelligence", "Recommendations", "Resources", "Personnel", "Planning", "Insights", "Admin", "Events"]],
    "paths": paths,
    "components": {
        "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT",
                                           "description": "Replaceable auth adapter. The hackathon demo adapter issues tokens for fictional users and is not production authentication."}},
        "schemas": schemas,
        "parameters": P,
        "responses": responses,
    },
}
# fix accidental junk in listIncidents param
for p in paths[f"{A}/incidents"]["get"]["parameters"]:
    p.pop("style", None)
out = sys.argv[1]
with open(out, "w", encoding="utf-8") as f:
    json.dump(spec, f, indent=2, ensure_ascii=False)
print(len(paths), "paths", len(schemas), "schemas")
