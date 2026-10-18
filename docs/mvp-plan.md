# DispatchZ 24-Hour MVP Plan

Contract: `contracts/openapi.json` (v0.1.2). Every operation carries `x-mvp-tier`: `mvp` (20), `stretch` (8), `future` (13). Nothing was removed.

## MVP vertical slice
create incident -> units + capabilities -> classify (PyTorch, human confirms) -> requirements (rules) -> hard eligibility -> ETA rank (XGBoost) -> dispatcher review (simulated) -> SSE updates to dashboard. Scenario B: mark unit out of service -> recommendation invalidated -> re-run.

## Tier 1 (mvp) build order
1. getHealth, getCurrentUser, resetDemoData
2. listUnits, getUnit, getUnitCapabilities, listCapabilityDefinitions, listFacilities
3. createIncident, listIncidents, getIncident, updateIncident
4. createClassification (PyTorch), getIncidentRequirements
5. createRecommendation (XGBoost ETA), getRecommendation, listIncidentRecommendations
6. createRecommendationReview
7. streamEvents, createUnitStatusEvent

## Tier 2 (stretch, only if MVP stable by ~hour 16)
listIncidentEvents, listAuditEvents, listPersonnel/getPersonnel/listCredentials, listCoverageGaps, listForecasts, listLeadershipRecommendations.

## Tier 3 (future)
Credential writes, apparatus, scenarios, readiness/training/equipment gaps, shifts, coverage, policies, multi-incident optimization (OR-Tools), multi-tenant isolation.

## AI (smallest viable)
- PyTorch: pretrained embedding model (Granite-embedding-30M or MiniLM) + cosine similarity to category prototypes. No training.
- XGBoost: ETA regressor trained at seed time on seeded synthetic data; haversine baseline and fallback.
- Report measured latency and baseline comparisons only. No Telum/NNPA claims without `lscpu` evidence and a benchmark.

## Simplifications
No OR-Tools, no PostGIS/routing (haversine), in-process SSE with ring buffer (single worker), fixed-token demo auth, Postgres with SQLite fallback, frontend pages `/intake` and `/dispatch` only.

## Phases and acceptance
| Phase | Hours | Tracks | Acceptance |
|---|---|---|---|
| 0 | 0-2 | contract, types, mock, platform check | spec valid; types compile; Prism serves Tier 1; `docs/platform-check.md` filled from the VM |
| 1 | 2-10 | DB+seed, rules-based backend, frontend on mock then real API, AI modules standalone | POST /incidents persists; recommendation lists ineligible units with reasons; dashboard shows queue/map/candidates from real API |
| 2 | 10-16 | AI integration, SSE, review, invalidation, conformance | classification returns confidence+model version; etaMethod=xgboost; model failure falls back; unit out_of_service emits events and UI shows outdated banner; responses pass contract validation |
| 3 | 16-21 | LinuxONE deploy, benchmarks, Tier 2 | `docs/benchmarks.md` with s390x numbers |
| 4 | 21-24 | demo hardening | fresh clone reproduces Scenarios A and B from README |

Overall MVP: ineligible units are never ranked eligible (tested); reviews are recorded with `simulatedAssignment: true` and appear in audit; PyTorch and XGBoost both run in live request paths on LinuxONE; SSE recovers from dropped connections; all data labelled synthetic.

## Workstream dependencies
Contract (frozen) -> types -> {frontend on Prism, backend routes, DB seed, AI modules}. Backend recommendation endpoint depends on seed data + AI module interfaces (`classify(text)`, `eta(features)`). Frontend switches from Prism to backend at end of Phase 1.

## Seed spec (fictional county, deterministic seed)
4 stations, ~12 units (engines, ladder, ALS/BLS ambulances, one hazmat/tech-rescue), ~20 personnel with credentials (one expired, one unverified), apparatus equipment with one in maintenance, 3 scripted scenarios (A immediate, B unit loss, optional C gap).
