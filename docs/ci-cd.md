# CI/CD

GitHub Actions validates every pull request and every push to `main`. A push to `main` that passes every check is then deployed to
the LinuxONE VM. The whole pipeline is one workflow, [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), so the deploy job
cannot start unless the checks succeeded.

## Pipeline

```mermaid
flowchart LR
    PR["Pull request"] --> C
    PUSH["Push to main"] --> C
    subgraph C["Checks, run in parallel"]
        K["OpenAPI contract<br/>validate, generator drift,<br/>Redocly, approval label"]
        B["Backend tests<br/>minimal and full dependencies,<br/>lint"]
        F["Frontend build<br/>types, check, test, build"]
        H["Repository hygiene<br/>secrets, line endings,<br/>script syntax"]
    end
    C -->|"all passed, and the ref is main"| D["Deploy to LinuxONE<br/>over SSH"]
    D --> T["On the VM: tests in the s390x image"]
    T --> R["Build the runtime image,<br/>replace the container"]
    R --> W{"Health reports ok?"}
    W -->|yes| OK["Done"]
    W -->|no| RB["Roll back to the previous image,<br/>fail the run"]
```

Pull requests run the checks only. They never deploy and never see the deploy secrets.

## Jobs

| Job (the name GitHub shows) | What it checks | Fails when |
|---|---|---|
| **OpenAPI contract** | `scripts/check_contract.py` validates `contracts/openapi.json` and compares it with the output of `scripts/gen_openapi.py`; Redocly lints it; on pull requests, a contract change needs a label | The file is invalid, was edited by hand or without regenerating, has lint errors (the 15 "unused component" warnings are expected: SSE payload schemas are not referenced by a response), or a PR changes the contract without the `contract-approved` label |
| **Backend tests (minimal)** | `pytest` on Python 3.12 without xgboost or torch, plus a lint for syntax errors and undefined names | A test fails, or the code does not import on a clean checkout |
| **Backend tests (full)** | The same with xgboost, so the ETA model tests run too | As above |
| **Frontend build** | `npm ci`, regenerated API types match the committed ones, `svelte-check`, `vitest`, production build | Any of those fails. **Skipped with a notice** when there is no `frontend/package.json` on the branch (the UI is still on `feature/Frontend`) |
| **Repository hygiene** | Shell scripts parse; no key files, `.env` or private keys are tracked; files that must be LF are LF | A secret or CRLF is committed |
| **Deploy to LinuxONE** | See below | The VM tests fail, the image does not build, or the API does not become healthy |

What CI deliberately does not cover: the Docker image, the Granite embedding model on the NNPA accelerator and the XGBoost model on
big-endian s390x. They cannot run on a GitHub-hosted x86 runner, so they are tested on the VM as the first step of the deploy.

### The contract approval label

[`CLAUDE.md`](../CLAUDE.md) requires explicit human approval before the API contract changes. The check cannot judge whether an
amendment is sensible; it makes sure nobody changes the contract without a person having looked. A pull request that touches
`contracts/openapi.json` or `scripts/gen_openapi.py` fails until a reviewer adds the label `contract-approved`. The label must be
added by a human reviewer after the amendment is approved, not by the author to get the build green.

## Deploy

[`scripts/deploy_vm.sh`](../scripts/deploy_vm.sh) is the same script a developer runs by hand. In CI it:

1. Copies the checked-out commit to the VM over SSH.
2. Builds the `test` image on the VM and runs the whole suite there, on s390x with torch, xgboost and the Granite model.
3. Builds the runtime image and keeps the previous one as `dispatchz-api:previous`.
4. Replaces the container and waits up to two minutes for `GET /api/v1/health` to report `"status":"ok"`.
5. If the build or the health check fails: exits non-zero, shows the container logs, points the `dispatchz-api` tag back at the
   previous image and starts it. If the tests fail, the running container is not touched at all.

The commit is recorded as the image label `org.opencontainers.image.revision` and in `~/dispatchz/DEPLOYED_REF` on the VM, so
`docker inspect dispatchz-api` and `cat ~/dispatchz/DEPLOYED_REF` tell you which commit is running. `DEPLOYED_REF` changes only after the new API
is healthy, so after a rollback it still names the commit that is really running. Deploys never overlap: they
share one concurrency group and queue instead of canceling each other.

Where the deployed API can be reached is a separate decision; see [Seeing the deployment](#seeing-the-deployment).

## One-time setup (a repository admin does this)

### 1. A dedicated deploy key

Use a key made only for CI, not the key IBM issued for the VM, so it can be revoked on its own. On your machine:

```bash
ssh-keygen -t ed25519 -N "" -C "dispatchz-ci" -f dispatchz_deploy
# Authorize it on the VM (use your existing key to connect):
ssh -i /path/to/ibm-key.pem linux1@<vm-ip> 'cat >> ~/.ssh/authorized_keys' < dispatchz_deploy.pub
# Optional but recommended: the VM's host key, so CI does not trust whatever answers on that IP.
ssh-keyscan -t ed25519 <vm-ip>
```

### 2. Repository secrets

Settings → Secrets and variables → Actions → New repository secret (or the same under Environments → `linuxone`):

| Secret | Value |
|---|---|
| `VM_SSH_KEY` | The contents of the private file `dispatchz_deploy` |
| `VM_HOST` | `linux1@<vm-ip>` |
| `VM_KNOWN_HOSTS` | Optional. The `ssh-keyscan` output from step 1 |

Then delete the local `dispatchz_deploy` file or keep it somewhere safe. Until `VM_SSH_KEY` and `VM_HOST` exist, the deploy job
finishes with a warning and a summary saying it was skipped; the checks still run.

### 3. Branch protection, so a failed commit cannot reach `main`

The workflow reports results; it cannot block a push by itself. Settings → Branches → Add rule for `main`:

- Require a pull request before merging.
- Require status checks to pass: select **OpenAPI contract**, **Backend tests (minimal)**, **Backend tests (full)**, **Frontend build**
  and **Repository hygiene**. Do not select the deploy job.
- Optionally require one approving review. For `contracts/openapi.json`, a `CODEOWNERS` entry naming the contract owner makes that
  review mandatory (the repository does not have one yet because it needs the owner's GitHub handle).

Without this rule, anyone can still push straight to `main`; CI then runs and the deploy is skipped on failure, but the bad commit
is already on `main`.

## Pinned versions

Nothing the pipeline or the VM build depends on is allowed to change unless a pull request changes it on purpose.

| What | Pinned by | Where |
|---|---|---|
| CI runner image | `ubuntu-24.04` | `runs-on` in the workflow |
| GitHub Actions | Full commit SHA, with the version in a comment | `uses:` lines in the workflow |
| Python and Node in CI | Exact patch versions (3.12.15, 22.23.3) | `env:` in the workflow |
| Python packages in CI | Exact versions for the whole dependency tree, resolved against the IBM image's versions | `.github/pins/minimal.lock`, `full.lock` |
| Frontend packages | Lockfile, installed with `npm ci` | `frontend/package-lock.json` |
| API packages on the VM (FastAPI, Pydantic, Starlette, Uvicorn, pytest, ...) | Exact versions of what passed on s390x | `backend/constraints.txt`, applied by the Dockerfile |
| IBM's base image (PyTorch, `torch_nnpa`, XGBoost, ...) | Image digest, not the moving `:latest` tag | `ARG BASE_IMAGE` in `backend/Dockerfile` |
| Embedding model weights | Hugging Face revision | `MODEL_REVISION` in `backend/app/embedding_classifier.py` |
| Contract linter | Exact version | the Redocly line in the workflow |

CI installs the lock with `pip install --no-deps` and then runs `pip check`, so a dependency missing from the lock fails the job
instead of being fetched at whatever version is newest.

What is still not pinned: the packages that come preinstalled on the GitHub runner image (git, curl, ssh, Docker, Node for `npx`),
which GitHub updates weekly inside the `ubuntu-24.04` label, and the npm dependencies of the Redocly CLI, which `npx` resolves from
ranges. Both have been stable; if one ever breaks the contract job, pin it then.

## Dependabot

`.github/dependabot.yml` makes Dependabot open a pull request each Monday for a newer GitHub Action (the SHA and its version comment)
or a newer frontend package, with at most three open at a time. Each one runs the full CI like any other PR and waits for a person to
merge it; nothing updates by itself. Patch and minor frontend updates come as one PR, and major updates as their own. Dependabot cannot
read `.github/pins/*.lock`, `backend/constraints.txt` or the Docker base image digest, so those follow the procedure below.

## Updating pinned versions

Do these in a pull request. CI then runs the whole suite against the new versions before anything reaches `main`.

- **Runner, Python or Node in CI:** edit `runs-on` or `env:` in the workflow.
- **An action:** pick a tag, then use its commit SHA and keep the version comment.
  `git ls-remote --tags https://github.com/actions/checkout 'refs/tags/v6*'` (for an annotated tag use the SHA on the `^{}` line).
- **A CI Python package:** edit `.github/pins/minimal.in` or `full.in`, run `pip install uv==0.13.1` and `python scripts/lock_ci.py`,
  and commit the changed `.lock` files. The script resolves for Linux and Python 3.12 whatever machine you run it on.
- **A package on the VM (for example FastAPI):** edit `backend/constraints.txt`, then run `bash scripts/deploy_vm.sh` from a branch
  or read the PR's CI result after merge; the Docker build and the 167 tests on s390x are the real check. Run `scripts/lock_ci.py`
  too, because the CI locks are resolved against that file.
- **IBM's base image:** on the VM, `docker pull` the new image, read its digest with
  `docker image inspect --format '{{index .RepoDigests 0}}' <image>`, put it in the Dockerfile, refresh
  `.github/pins/ibm-base-image.txt` from `pip list --format=freeze` inside the image, update `backend/constraints.txt` if needed,
  and rerun `scripts/lock_ci.py`.
- **Frontend packages:** `npm install <pkg>@<version>` in `frontend/` and commit `package-lock.json`.

## Running the checks locally

```bash
pip install --no-deps -r .github/pins/full.lock                    # the exact CI environment (use a virtual environment)
python scripts/check_contract.py
npx @redocly/cli@2.64.1 lint contracts/openapi.json
(cd backend && python -m pytest -q)
ruff check --select E9,F63,F7,F82,F811 backend scripts
(cd frontend && npm ci && npm run gen:types && npm run check && npm test && npm run build)
```

## Seeing the deployment

The API container listens on the VM's `localhost:8000` only, on purpose: the bearer tokens are fixed demo tokens, so the API must
not face the internet. Teammates reach it through an SSH tunnel ([deployment.md](deployment.md#access)). The GitHub **Deployments**
page (repository sidebar) and the run summary show which commit was deployed and when.

If the team wants a URL that anyone can open, that is a security decision to take explicitly (a reverse proxy with TLS and real
authentication, or a read-only demo mode), not something the pipeline should do quietly.

## Limitations and known risks

- **Rollback was tested once.** On 2026-10-18 a deliberately crashing build was deployed to the VM by hand: the script detected it
  after two minutes, restored the previous image, exited non-zero, and `DEPLOYED_REF` kept naming the good commit. Not repeated from CI.
- **Pins go stale.** Pinned versions do not receive security fixes by themselves. Dependabot (below) covers the actions and the frontend
  packages; the Python locks, `constraints.txt` and the base image digest are updated by hand, so review them before the final submission.
- **The runner must reach the VM.** GitHub-hosted runners connect to the VM's public SSH port. If the LinuxONE Community Cloud
  restricts inbound addresses, the deploy step fails at the `scp`; the fix is a self-hosted runner on a machine that can reach it.
- **The VM can change.** Community Cloud instances are time-limited and a new one has a new address and host key. Update `VM_HOST`,
  `VM_KNOWN_HOSTS` and the authorized key.
- **Only the API is deployed.** The web UI is not on `main`. When it is, deploying it (for example serving the static build) is a
  separate addition.
- **A short outage per deploy.** The old container stops before the new one starts. That is acceptable for a demo; a rolling
  swap would need a second port and a proxy.
- **Rollback is one level deep.** `dispatchz-api:previous` is the image that was running before the latest deploy.
