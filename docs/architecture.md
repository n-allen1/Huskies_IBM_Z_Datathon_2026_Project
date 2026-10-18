# Architecture

DispatchZ is human-in-the-loop decision support for emergency dispatch. It recommends which units to send, explains why, and
records what a dispatcher decides. It never dispatches anything: an accepted recommendation produces a **simulated** assignment.
All data is synthetic.

This document describes the system as it exists on `main` today. Planned work is labeled as planned.

## Components and where they run

| Component | Technology | Runs on | Status |
|---|---|---|---|
| API (REST + server-sent events) | FastAPI, Pydantic, Uvicorn (single worker) | Docker container on the LinuxONE VM; locally in a venv | Implemented: all 20 `mvp`-tier operations plus `listAuditEvents` |
| Rules engine (protocol, eligibility, ranking) | Pure Python, `backend/app/eligibility.py` | Inside the API | Implemented |
| Multi-incident allocator | Exact mixed-integer solve (SciPy/HiGHS), greedy fallback; `backend/app/multi_incident.py` | Inside the API | Implemented; see [ai-pipeline.md](ai-pipeline.md#multi-incident-allocation) |
| Incident classifier | PyTorch + IBM Granite Embedding 30M, keyword fallback | Inside the API container; model on the Telum NNPA accelerator in fp16, CPU fallback | Implemented; see [ai-pipeline.md](ai-pipeline.md) |
| ETA model | XGBoost, trained at start-up on a synthetic simulator, straight-line fallback | Inside the API | Implemented; synthetic training data |
| Store | In-memory, behind the `Store` interface in `backend/app/store.py` | Inside the API | Implemented; data is lost on restart |
| Event bus | In-process ring buffer | Inside the API | Implemented; single worker only |
| PostgreSQL schema and seed | Alembic, `psql` seed script in `database/` | Not deployed | Exists, **not connected to the API** (see [Persistence](#persistence-status)) |
| Web UI | SvelteKit 2, Svelte 5, Leaflet | Developer machine | On branch `feature/Frontend`, not merged to `main` |
| Geocoding and map tiles | Photon (OpenStreetMap) and Esri tiles, called by the browser | External | Used by the UI for demo data only |

## System architecture

```mermaid
flowchart LR
    subgraph Client["Dispatcher workstation (browser)"]
        UI["Web UI<br/>SvelteKit, branch feature/Frontend"]
    end
    subgraph External["External services, called by the UI (demo data only)"]
        GEO["Photon geocoder<br/>OpenStreetMap"]
        TILES["Esri map tiles"]
    end
    subgraph Z["IBM LinuxONE VM: s390x, z/VM guest, Docker"]
        subgraph API["DispatchZ API container: FastAPI, single worker"]
            ROUTES["REST routes and SSE stream<br/>contract: contracts/openapi.json"]
            SVC["Services: incidents, review,<br/>unit status, audit"]
            RULES["Rules engine:<br/>protocol, hard eligibility, ranking"]
            BUS["Event bus<br/>in-memory ring buffer"]
            STORE[("In-memory store<br/>and synthetic seed")]
            CLS["Incident classifier<br/>PyTorch and Granite Embedding 30M<br/>NNPA fp16, CPU fallback"]
            ETA["ETA model<br/>XGBoost, trained at start-up"]
        end
    end
    PG[("PostgreSQL schema and seed<br/>database/, not connected")]

    UI -->|"REST and SSE, bearer token"| ROUTES
    UI -.-> GEO
    UI -.-> TILES
    ROUTES --> SVC
    SVC --> RULES
    SVC --> STORE
    SVC --> BUS
    BUS --> ROUTES
    SVC --> CLS
    RULES --> ETA
    PG -.->|"planned"| STORE
```

Solid lines exist today; dotted lines are external calls made by the browser, or planned.

## Processing flow

How an incident moves from intake to a recorded decision. The two **human decisions** are the only points where an incident's
category and the unit assignment are determined.

```mermaid
flowchart TD
    A["Call-taker enters the incident<br/>POST /incidents: narrative and coordinates"] --> B["Incident stored<br/>status: intake"]
    B --> C["Classification suggestion<br/>POST /incidents/{id}/classifications, mode infer"]
    C --> D{"Embedding model<br/>ready?"}
    D -->|yes| D1["Granite embedding suggests a category,<br/>or abstains below 0.75 similarity"]
    D -->|"no or failed"| D2["Keyword rules suggest a category,<br/>and say so"]
    D1 --> E
    D2 --> E
    E["Dispatcher confirms or corrects the category<br/>HUMAN DECISION"] --> F["Protocol maps the category<br/>to required capabilities"]
    F --> G["Stage 1: hard eligibility per unit<br/>status, staffing, credentials, equipment"]
    G --> H["Stage 2: rank eligible units by ETA<br/>XGBoost, straight-line fallback"]
    H --> I["Package of units covering the<br/>mandatory requirements; unmet ones listed<br/>(scope multi_incident: one joint allocation across<br/>the contesting incidents, by priority)"]
    I --> J["Recommendation: pending_review"]
    J --> K{"Dispatcher review<br/>POST /recommendations/{id}/reviews<br/>HUMAN DECISION"}
    K -->|"accept or modify"| L["Simulated assignment: units become<br/>dispatched_simulated; competing<br/>recommendations are invalidated"]
    K -->|reject| M["Nothing assigned;<br/>incident back to pending_dispatch"]
    L --> N["Events over SSE and audit trail"]
    M --> N
    O["Unit status event<br/>POST /resources/units/{id}/status-events"] -.->|"a unit leaves available:<br/>dependent recommendations invalidated"| J

    subgraph Planned["Planned, not implemented"]
        P1["Road-network routing"]
        P3["Persistence in PostgreSQL"]
    end
    H -.-> P1
    B -.-> P3
```

Machine learning only produces a *suggestion* (the category) and an *ordering* (ETA). Neither can make an ineligible unit eligible;
tests enforce this (see [ai-pipeline.md](ai-pipeline.md#safety-properties)).

## Decision logic

The protocol table (`backend/app/protocol.py`) is a **synthetic demo protocol**: it is not an adopted agency protocol or a statement
of any standard's minimums. Eligibility (`backend/app/eligibility.py`):

1. **Hard eligibility.** A unit is eligible only if it is `available`, its apparatus is not in maintenance or out of service, it is
   staffed to its minimum crew, it provides at least one mandatory capability, and it has no mandatory capability it nominally
   carries that is impaired (expired or unverified credential, equipment not ready). Equipment that is merely `limited` is not
   accepted. Capabilities come from active crew credentials and ready equipment, not from what a station owns.
2. **Ranking.** Only eligible units get a rank: ETA, then coverage impact, then call sign.
3. **Package.** A greedy cover of the mandatory requirements. Whatever cannot be filled is stated in `limitations`.

4. **Joint allocation (optional, `scope: multi_incident`).** When several incidents contest the same units, steps 1 and 2 run for
   each incident and an exact solver then assigns units across them: the highest-priority incident is staffed first, shortages fall on
   the lowest priority, and nothing ineligible can be chosen. The response is the asked-about incident's part of that one plan. See
   [ai-pipeline.md](ai-pipeline.md#multi-incident-allocation).

A dispatcher can override a recommendation (an ineligible or non-recommended unit) only with an `overrideReason`; it is recorded as
an override. A unit that is not `available` right now can never be assigned.

## Lifecycles

Incident status: `intake` → `pending_dispatch` → `recommendation_ready` → `dispatched_simulated` → `on_scene_simulated` → `resolved`,
or `cancelled`. Invalid transitions return `409 INVALID_STATE_TRANSITION`.

```mermaid
stateDiagram-v2
    [*] --> pending_review: created
    pending_review --> accepted: review accept
    pending_review --> modified: review modify
    pending_review --> rejected: review reject
    pending_review --> invalidated: inputs changed
    pending_review --> expired: past expiresAt
```

A recommendation is invalidated when the incident's category, narrative or location changes, when the incident is cancelled or
resolved, when a newer recommendation supersedes it, when a unit in its recommended package is assigned elsewhere, or when such a
unit leaves `available`. It expires after `RECOMMENDATION_TTL_SECONDS` (default 300).

## Events

`GET /api/v1/events/stream` is Server-Sent Events with replay from `Last-Event-ID`. Emitted: `incident.created`, `incident.updated`,
`unit.status_changed`, `recommendation.created`, `recommendation.invalidated`, plus `stream.heartbeat` (every 15 s) and
`stream.resync_required`. `coverage.gap_detected` is in the contract but is not emitted. Ids are a single in-process sequence, so a
restart makes older `Last-Event-ID` values trigger `stream.resync_required`.

## Persistence status

The API stores everything in memory; a restart loses it. `database/` holds a PostgreSQL schema (Alembic migration `0001`, 15 tables)
and a seed script, but nothing connects them to the API yet. The seed script's scenario set has changed between commits (EMS, then
police, then fire, each replacing the previous one) and currently holds a fire-only set of 6 units and 6 incidents, so check it
before relying on its vocabulary. Known gaps to close before it can be connected:

| Topic | Database schema and seed | API and contract |
|---|---|---|
| Crew and equipment | No tables linking personnel to units; no apparatus or equipment | The rules engine derives capabilities from crew credentials and equipment readiness |
| Unit status | `available`, `assigned` | `dispatched_simulated`, `en_route_simulated`, ... |
| Recommendation status | `current` | `pending_review`, `accepted`, ... |
| Review decision, credential status | Free-text columns with no rows in the current seed; earlier seeds used `accepted`, `modified` and `active` | `accept`, `modify`, `reject`; `valid`, `unverified`, `revoked`, ... |
| Incident categories | Free text, for example `vegetation_fire`, `building_alarm_smoke` | The contract's enumeration (`structure_fire`, `wildland_fire`, ...) |
| Capability codes | `STRUCTURAL_FIRE`, `LADDER_RESCUE`, `HAZMAT_INITIAL`, ... | `CAP-*` identifiers used by the protocol |

The contract wins: a mapping layer must translate database values into the contract's enumerations. The integration point is the
`Store` interface in `backend/app/store.py`.

## Location handling

The API stores a latitude and longitude (and an optional address string) supplied by the client; it does no geocoding. The UI
geocodes in the browser through Photon and draws Esri tiles, which is acceptable for synthetic data only. Contract changes the
frontend work proposes (optional point, server-side resolution, location provenance) are described in
`docs/location-intake.md` on `feature/Frontend` and need explicit approval; see [development.md](development.md#contract-first-workflow).

## Security and privacy posture

Implemented: bearer-token authentication with fixed **demo** tokens (not production authentication), role checks that match the
contract, required `Idempotency-Key` on state-changing calls, an audit trail of state changes, and `isSynthetic: true` required on
every incident. In the LinuxONE deployment the API port is published on the VM's localhost only and reached through an SSH tunnel.

Not implemented: a real identity provider, TLS termination, encryption at rest, rate limiting, and multi-tenant isolation beyond the
`tenantId` filter on the audit trail.

## Planned work

Planning and Insights modules (demand forecasting, gap detection, leadership reports; contract tier `future`),
road-network routing, PostgreSQL persistence, and a calibrated
classifier confidence. See [mvp-plan.md](mvp-plan.md) for the original scope and tiers.
