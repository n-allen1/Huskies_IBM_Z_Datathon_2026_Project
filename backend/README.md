# DispatchZ backend

FastAPI service implementing the [OpenAPI contract](../contracts/openapi.json): incidents, classification, requirements,
recommendations, human review, unit status, audit trail, and a live event stream. Decision support only; all data is synthetic.

```bash
pip install -r requirements-test.txt
python -m pytest -q
DEMO_MODE=true python -m uvicorn app.main:app --port 8000
```

| Directory | Contents |
|---|---|
| `app/` | The service (module map in [development.md](../docs/development.md#backend-layout)) |
| `tests/` | Test suite, including contract conformance and drift checks |
| `tools/` | Measurement tools run on the LinuxONE VM |
| `Dockerfile` | IBM LinuxONE (s390x) image; see [deployment.md](../docs/deployment.md) |

Setup, configuration and the contract workflow: [development.md](../docs/development.md). Architecture:
[architecture.md](../docs/architecture.md). AI components and measurements: [ai-pipeline.md](../docs/ai-pipeline.md).
