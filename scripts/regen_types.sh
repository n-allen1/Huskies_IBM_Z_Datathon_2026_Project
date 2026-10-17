#!/usr/bin/env bash
# Regenerate contract-derived types. Run after any change to contracts/openapi.json.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m openapi_spec_validator contracts/openapi.json
npx --yes openapi-typescript contracts/openapi.json -o frontend/src/lib/api/schema.d.ts
python -m datamodel_code_generator --input contracts/openapi.json --input-file-type openapi \
  --output-model-type pydantic_v2.BaseModel --output backend/app/models_generated.py
