# Deployment on IBM LinuxONE

The API runs as a Docker container on a LinuxONE Community Cloud VM. This document covers building, running, configuring and
verifying it. For local development without Docker see [development.md](development.md).

## Target environment

Observed on the project's VM (from `lscpu`, `/etc/os-release` and the platform check):

| Item | Value |
|---|---|
| Architecture | `s390x`, big-endian |
| Hypervisor | z/VM 7.3.0 (full virtualization) |
| CPU and memory | 3 CPUs at 5508 MHz (machine type 9175), about 7 GB RAM, 50 GB disk |
| Operating system | Ubuntu 24.04.5 LTS |
| Docker | 29.8.0 (podman is not installed) |
| Accelerator facility | `nnpa` flag present; `torch_nnpa` reports `nnpa available: True` and `telum2 available: True` |

The VM is requested through the [LinuxONE Community Cloud](https://linuxone.cloud.marist.edu/) (event code `SSF2026` gives an instance
sized for ML work).

## What is deployed

`backend/Dockerfile` builds on IBM's `registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x` (pinned by digest), which already provides
PyTorch, IBM's `torch_nnpa` plug-in, `libzdnn`, XGBoost and scikit-learn for s390x. These are not installable from PyPI on s390x
(no wheels), which is why we build on IBM's image. Our layer adds only the API's own dependencies, at the exact versions in `backend/constraints.txt`, and pip is
prevented from changing IBM's libraries; a conflict fails the build. See [Pinned versions](ci-cd.md#pinned-versions).

| Dockerfile target | Purpose |
|---|---|
| `test` | Adds test dependencies and runs the full test suite inside the image |
| `runtime` (default) | The API: FastAPI on port 8000, one worker, health check. The build tries to pre-download the model; if that fails it is downloaded at first start. |

The runtime image sets `CLASSIFIER=embedding`, `EMBEDDING_DEVICE=nnpa`, `EMBEDDING_DTYPE=float16` and `ETA_MODEL=xgboost`. It runs as the
base image's non-root user. The image is s390x-only, so build it on the VM, not on an x86 machine.

## Deploy

Pushes to `main` deploy automatically once the checks pass; see [ci-cd.md](ci-cd.md). To deploy by hand:

**With the script** (copies the working tree, rebuilds, runs the tests, restarts the API, waits for health). Run it from a bash shell
on your machine:

```bash
export VM_KEY="/path/to/private-key.pem" VM_HOST="linux1@<vm-ip>"
bash scripts/deploy_vm.sh --dev        # your own test container; add --no-tests to skip the test image
bash scripts/deploy_vm.sh --yes        # replaces the SHARED demo API; normally CI does this
```

The key path and host come from your environment and are never stored in the repository.

### Testing a branch on the VM without touching the demo

`--dev` builds the working tree you run it from as a separate container, `dispatchz-dev`, published on VM port 8001, with its own
images and source directory. It never touches `dispatchz-api`, its images or its `DEPLOYED_REF`, and it has the same rollback. This is
the way to try something on the real hardware (the NNPA accelerator, XGBoost on s390x) from a feature branch.

```bash
bash scripts/deploy_vm.sh --dev
ssh -i /path/to/private-key.pem -L 8001:localhost:8001 linux1@<vm-ip>     # then use http://localhost:8001
```

Several people can each have a container: give each their own `DEV_NAME` and `DEV_PORT`, for example
`DEV_NAME=alex DEV_PORT=8002 bash scripts/deploy_vm.sh --dev` gives `dispatchz-dev-alex` on port 8002. Remove a container you are done with
(`docker rm -f dispatchz-dev`); the VM has about 7 GB of memory and each API loads its own model. Without `--dev` the script asks for
confirmation (or `--yes`) before replacing the shared API, and refuses to run unattended without `--yes`. CI sets `CI=true` and is not asked.

**Manually**, on the VM, from the repository root:

```bash
docker build -f backend/Dockerfile --target test -t dispatchz-test . && docker run --rm dispatchz-test
docker build -f backend/Dockerfile -t dispatchz-api .
docker rm -f dispatchz-api 2>/dev/null
docker run -d --name dispatchz-api -p 127.0.0.1:8000:8000 -e DEMO_MODE=true --restart unless-stopped dispatchz-api
```

Configuration is by environment variable; see [`.env.example`](../.env.example). To use a file: `docker run --env-file .env ...`.

## Verify

```bash
curl -s localhost:8000/api/v1/health
docker ps --format 'table {{.Names}}\t{{.Status}}'      # the API shows (healthy)
```

Expect `"architecture":"s390x"` and `"syntheticDataOnly":true`. The status reads `degraded` for the first seconds while the models
load, then `ok`; the `inference` component names the classifier (including its backend, such as `+nnpa-float16`) and the ETA model.
A suggestion's `modelVersion` also states the backend that produced it.

## Access

The API port is published on the VM's localhost only. From your machine, tunnel to it:

```bash
ssh -i /path/to/private-key.pem -L 8000:localhost:8000 linux1@<vm-ip>
# then browse or curl http://localhost:8000
```

## Evidence for the Datathon submission

The guidebook asks for screenshots of platform use. This captures the essentials in one screen:

```bash
{ hostname; uname -srm; . /etc/os-release; echo "$PRETTY_NAME"
  lscpu | grep -E "Architecture|Byte Order|^CPU\(s\)|Machine type|CPU dynamic MHz|Hypervisor:|nnpa"
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'; }
```

The measurement tools (classifier evaluation, NNPA check, ETA evaluation) are listed in [ai-pipeline.md](ai-pipeline.md#evaluation).

`scripts/check_platform.sh` is an optional diagnostic for a new VM. It tests only PyPI binary wheels, so it reports `FAIL` for numpy,
torch and xgboost on s390x even though they are available through apt or IBM's image.

## Verification status

| Check | Status |
|---|---|
| Image builds on the VM; tests pass in the image | Confirmed 2026-10-18 by the CI deploy: all 167 tests pass on s390x |
| Container healthy, health reports `architecture: s390x` | Confirmed 2026-10-17, before the AI models became the default |
| Classifier on NNPA fp16: parity and latency | Confirmed with `tools.classifier_eval` and `tools.nnpa_check` ([ai-pipeline.md](ai-pipeline.md#evaluation)) |
| XGBoost ETA trains and evaluates on s390x | Confirmed with `tools.eta_eval` |
| Review, unit-status, audit and SSE endpoints on the VM | Not yet confirmed (verified locally only) |
| Running API reporting `+nnpa-float16` in health | Confirmed 2026-10-18: `granite-embedding-30m@9b5b096411+nnpa-float16`, `eta: xgboost-3.4.1-eta-synthetic-v1`, status `ok` |
| `scripts/deploy_vm.sh` end to end | Confirmed 2026-10-18 (run by CI). Rollback confirmed on the VM the same day with a deliberately crashing build (restored the previous image, exit code 1, `DEPLOYED_REF` unchanged) |
| Automatic deploy from GitHub Actions | Confirmed 2026-10-18: a push to `main` ran all checks, deployed to the VM in under a minute, and the container reported healthy |

## Security notes

- Never commit keys or tokens. `.gitignore` excludes `*.pem` and `.env`.
- Do not publish port 8000 beyond localhost. The bearer tokens are fixed demo tokens, not authentication.
- IBM's Jupyter lab instructions open port 38888 to the internet with a default token, which allows remote code execution on the VM.
  Use an SSH tunnel instead, or change the token and restrict access.

## Troubleshooting

- **CRLF line endings.** A Windows checkout can leave carriage returns in shell scripts, the Dockerfile and env files, which breaks them
  on Linux. `.gitattributes` forces LF for these, and the deploy script strips them on the VM.
- **`degraded` health.** Models are still loading (first load takes 10 to 20 seconds) or a fallback is in use. Check `docker logs dispatchz-api`.
- **Port 8000 already in use after the rename.** Deployments made before the project was renamed used the container name `responsematch-api`.
  `scripts/deploy_vm.sh` removes it; to do it by hand, run `docker rm -f responsematch-api`. The old images (`responsematch-api`,
  `responsematch-test`) and the `~/responsematch` directory are harmless and can be deleted.
- **Build fails with a dependency conflict.** The pin step is working as designed; the API's requirements conflict with a package in IBM's image.
