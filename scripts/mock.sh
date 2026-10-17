#!/usr/bin/env bash
# Prism mock on :4010 (dynamic examples) and Swagger UI page on :8080/swagger.html
cd "$(dirname "$0")/.."
npx --yes @stoplight/prism-cli mock -d contracts/openapi.json -p 4010 &
(cd contracts && python -m http.server 8080)
