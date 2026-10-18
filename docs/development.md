# Development guide

Local setup, testing, and the contract-first workflow. For the LinuxONE deployment see [deployment.md](deployment.md).

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.12 (tested with 3.12.6 locally and 3.12.3 on the VM; the code uses 3.10+ syntax) | Backend, tests, contract tooling |
| Node.js and npm | 22 (tested with 22.11) | Frontend, Prism mock, TypeScript type generation |
| Git | any recent | |
| Docker | 29.x on the VM | LinuxONE image only (s390x) |

The backend was tested on Windows 11 (Git Bash) and Ubuntu 24.04 on s390x.

## Backend

```bash
python -m venv .venv
source .venv/bin/activate                  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r backend/requirements-test.txt
cd backend
python -m pytest -q
```

Expect `162 passed, 5 skipped` (the 5 need the optional `xgboost` package; with it installed, 167 pass). Run the API:

```bash
DEMO_MODE=true python -m uvicorn app.main:app --port 8000        # PowerShell: $env:DEMO_MODE="true"; python -m uvicorn ...
```

Requirements files: [`requirements.txt`](../backend/requirements.txt) (runtime), [`requirements-test.txt`](../backend/requirements-test.txt)
(tests), [`requirements-dev.txt`](../backend/requirements-dev.txt) (adds contract tooling and `xgboost`; local only, because the LinuxONE
image ships it). Configuration variables are in [`.env.example`](../.env.example); the API reads the process environment and does not
load that file itself.

### Running with the ML components locally

By default a local run uses the rule-based fallbacks: keyword classifier and straight-line ETA. To use the XGBoost ETA model, install
`xgboost` (it is in `requirements-dev.txt`) and start with `ETA_MODEL=xgboost`; `/api/v1/health` then reports the model once trained.
The embedding classifier (`CLASSIFIER=embedding`) needs PyTorch and `transformers`, which are not in the requirements files; they come
with the LinuxONE image, and that path has been exercised only there.

### Demo authentication

Authentication uses fixed **demo** bearer tokens for fictional users; this is not production authentication. Send
`Authorization: Bearer <token>`.

| Token | Role |
|---|---|
| `demo-call-taker` | call_taker |
| `demo-dispatcher` | dispatcher |
| `demo-supervisor` | supervisor |
| `demo-hr` | hr_training_admin |
| `demo-fleet` | fleet_equipment_admin |
| `demo-leadership` | department_leadership |
| `demo-admin` | system_admin |

Which roles may call which operation comes from the contract, and a test fails if the code and the contract disagree.

### Seed data

The API starts with a synthetic fictional county from `backend/app/seed_standin.py`: 13 units (4 engines, a ladder, a hazmat and
technical-rescue unit, 5 ambulances, 2 patrol cars), 30 crew members, 11 capabilities and 6 facilities. `POST /api/v1/admin/demo/reset`
(needs `DEMO_MODE=true` and `demo-admin`) restores it. Scripted situations for demonstrations:

| Situation | Effect |
|---|---|
| `HR4` is the only unit with hazmat and technical-rescue capability | Losing it leaves the hazmat requirement unfilled |
| `E4`'s technical-rescue credential expired | Ineligible for technical rescue, with the expiry date as the reason |
| `M4`'s EMT credentials are expired and unverified | Never admitted as a transport unit |
| `M5`'s cardiac monitor is in maintenance | Ineligible for cardiac calls |
| `M2` is already dispatched | Ineligible, status shown |

## Frontend

The UI lives on branch `feature/Frontend` and is not merged to `main` yet. It is SvelteKit 2 with Svelte 5, Vite, Tailwind and Leaflet.

```bash
git fetch origin && git switch feature/Frontend
cd frontend
npm install
cp .env.example .env            # then set VITE_API_TARGET (below)
npm run dev                     # http://localhost:5173
```

`VITE_API_TARGET` is the proxy target for `/api`: `http://localhost:4010` (Prism mock, the default) or `http://localhost:8000` (the
backend). Other scripts: `npm run check` (type check), `npm test` (unit tests), `npm run build` (static build), and
`npm run mock:fixtures` (a stateful fixture server on port 4011 with a working event stream; not tested for this document).

Verified on that branch: `npm install`, `npm run check` (0 errors), `npm test` (28 passed), `npm run build`, and the dev server proxying
`/api` to a running backend. Full UI flows against this backend have not been tested end to end.

## Contract-first workflow

`contracts/openapi.json` (OpenAPI 3.1) is the single source of truth for the API. Implementation conforms to it, never the reverse.
**Any change to an endpoint, schema, status code, header, role or other public interface needs explicit human approval first**,
including backward-compatible additions. The rules are in [`CLAUDE.md`](../CLAUDE.md). `scripts/gen_openapi.py` generates the contract
and is treated as a contract source: change the generator, then regenerate, after approval.

| Task | Command |
|---|---|
| Validate the contract | `python -m openapi_spec_validator contracts/openapi.json` |
| Regenerate the contract (needs approval) | `python scripts/gen_openapi.py contracts/openapi.json` |
| Regenerate Python models and TypeScript types | `bash scripts/regen_types.sh` (needs `pip install -r backend/requirements-dev.txt` and Node) |
| Mock server and Swagger viewer | `bash scripts/mock.sh`: Prism on `:4010`, viewer at `http://localhost:8080/swagger.html` (loads Swagger UI from a CDN) |

`backend/app/models_generated.py` and `frontend/src/lib/api/schema.d.ts` are generated; do not edit them by hand. The backend imports
generated types only through `backend/app/models.py`, so a regeneration that renumbers a type needs a fix in one place.

`backend/tests/test_contract.py` guards drift: every route must exist in the contract, the implemented operations must match an
explicit list, roles must match the contract's "Allowed roles", and every response in the test suite is validated against the schema the
contract declares for that status.

## Backend layout

| Path | Responsibility |
|---|---|
| `backend/app/main.py` | App factory; everything with a side effect (store, models, clock) is injectable |
| `backend/app/routes/` | Thin HTTP layer, one module per area |
| `backend/app/services.py` | Use cases: incident lifecycle, classification, recommendation, review, unit status, audit |
| `backend/app/eligibility.py`, `protocol.py` | Rules engine and the synthetic demo protocol |
| `backend/app/embedding_classifier.py`, `eta_model.py`, `inference.py` | AI components and their interfaces and fallbacks |
| `backend/app/store.py` | `Store` interface and the in-memory implementation; **the integration point for a database** |
| `backend/app/events.py` | SSE event bus and stream |
| `backend/tools/` | Measurement tools run on the VM (see [ai-pipeline.md](ai-pipeline.md#evaluation)) |
| `backend/tests/` | Test suite |

## Conventions

- **Commits:** follow `.claude/skills/commit-messages/SKILL.md` (concise, American English, no AI author or co-author lines).
- **Line endings:** `.gitattributes` forces LF for shell scripts, the Dockerfile, `.dockerignore`, `requirements*.txt` and `.env.example`.
- **Synthetic data only.** Every incident must be created with `isSynthetic: true`; do not add real personal or operational data.
