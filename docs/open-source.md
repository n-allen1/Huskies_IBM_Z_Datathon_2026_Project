# Open-source components and attribution

The IBM Z Datathon rules allow open-source libraries and frameworks provided links to them are included in the submission. This
lists what the project uses. License terms are in each project's repository; the one license verified for this document is noted.
All application code in this repository was written for the Datathon.

## Backend (runtime)

| Component | Purpose | Link |
|---|---|---|
| FastAPI | HTTP API framework | <https://github.com/fastapi/fastapi> |
| Starlette | ASGI toolkit under FastAPI | <https://github.com/encode/starlette> |
| Pydantic | Request and response validation, generated models | <https://github.com/pydantic/pydantic> |
| Uvicorn | ASGI server | <https://github.com/encode/uvicorn> |

## AI and machine learning

| Component | Purpose | Link |
|---|---|---|
| PyTorch | Runs the embedding model (provided by IBM's LinuxONE image) | <https://github.com/pytorch/pytorch> |
| Hugging Face Transformers | Loads the model and tokenizer | <https://github.com/huggingface/transformers> |
| IBM Granite Embedding 30M English | Pretrained model for incident classification (Apache-2.0, verified on the model card) | <https://huggingface.co/ibm-granite/granite-embedding-30m-english> |
| XGBoost | ETA regression model | <https://github.com/dmlc/xgboost> |
| NumPy | Array handling for the ETA model | <https://github.com/numpy/numpy> |

## IBM-provided platform components

| Component | Purpose | Link |
|---|---|---|
| `jupyterlab-image-s390x` container image | Base image with PyTorch, XGBoost and the NNPA plug-in for s390x | <https://github.com/linuxone-community-cloud/jupyter-lab-ml> |
| `torch_nnpa` and zDNN (`libzdnn`) | PyTorch backend for the Telum NNPA accelerator | <https://github.com/IBM/zDNN> |

## Database tooling (schema and seed; not connected to the API yet)

| Component | Purpose | Link |
|---|---|---|
| PostgreSQL | Target database | <https://www.postgresql.org/> |
| Alembic and SQLAlchemy | Schema migrations | <https://github.com/sqlalchemy/alembic>, <https://github.com/sqlalchemy/sqlalchemy> |
| psycopg | PostgreSQL driver used by the migration environment | <https://github.com/psycopg/psycopg> |

## Frontend (branch `feature/Frontend`)

| Component | Purpose | Link |
|---|---|---|
| SvelteKit and Svelte | UI framework | <https://github.com/sveltejs/kit>, <https://github.com/sveltejs/svelte> |
| Vite | Build tool and dev server | <https://github.com/vitejs/vite> |
| Tailwind CSS | Styling | <https://github.com/tailwindlabs/tailwindcss> |
| Leaflet | Maps | <https://github.com/Leaflet/Leaflet> |
| openapi-fetch and openapi-typescript | Typed API client and generated types | <https://github.com/openapi-ts/openapi-typescript> |
| Vitest | Unit tests | <https://github.com/vitest-dev/vitest> |

## Development and testing tools

| Component | Purpose | Link |
|---|---|---|
| pytest, httpx | Test runner and HTTP test client | <https://github.com/pytest-dev/pytest>, <https://github.com/encode/httpx> |
| jsonschema | Validates API responses against the contract | <https://github.com/python-jsonschema/jsonschema> |
| openapi-spec-validator | Validates the OpenAPI contract | <https://github.com/python-openapi/openapi-spec-validator> |
| datamodel-code-generator | Generates Python models from the contract | <https://github.com/koxudaxi/datamodel-code-generator> |
| Prism | Mock server for the contract | <https://github.com/stoplightio/prism> |
| Swagger UI | Contract viewer (`contracts/swagger.html`) | <https://github.com/swagger-api/swagger-ui> |

## External services and data

- **Photon** (<https://github.com/komoot/photon>), built on [OpenStreetMap](https://www.openstreetmap.org/copyright) data, and **Esri**
  map tiles are called by the web UI for geocoding and the basemap. Use is limited to synthetic demo data and is subject to those
  services' terms.
- **No public datasets are ingested.** All incidents, people, units, credentials and addresses are synthetic. The database seed notes
  that its fields are inspired by the public structure of the Seattle Call Data dataset; no real records are imported.
