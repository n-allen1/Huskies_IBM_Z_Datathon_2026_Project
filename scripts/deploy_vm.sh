#!/usr/bin/env bash
# Deploy the DispatchZ stack (web UI + API, see compose.yml) to the LinuxONE VM.
# Run from anywhere in a bash shell on your machine (it needs Docker with buildx locally, for the web image):
#   export VM_KEY="E:\path\to\key.pem" VM_HOST=linux1@<vm-ip>
#   bash scripts/deploy_vm.sh --dev              # YOUR OWN test stack (web on VM port 8001, localhost only); the demo is untouched
#   bash scripts/deploy_vm.sh --dev --no-tests   # same, skipping the test image
#   bash scripts/deploy_vm.sh --yes              # replace the SHARED demo stack (web on port 80, public)
# The key path and host are never stored in the repo. The GitHub Actions workflow (.github/workflows/ci.yml) runs the shared
# deploy on every push to main (it sets CI=true, so it needs no --yes).
#
# What happens:
#   here  1. build the web image for linux/s390x (the Node stage runs on this machine; no emulation, no Node on the VM)
#         2. pack ONLY what the API image needs (backend/, the contract, compose.yml) from the tracked files
#   VM    3. run the test suite in the s390x test image (a failure stops here; the running stack is not touched)
#         4. build the API image, load the web image, both tagged with the commit
#         5. `docker compose up` with the new tag, wait for healthy, then for the API to report status ok THROUGH the web proxy
#         6. check that both running containers carry this commit
#         7. on success record the release and delete older images; on failure put the previous release back and exit non-zero
# The VM keeps only ~/dispatchz/{compose.yml,DEPLOYED_TAG,DEPLOYED_REF,PREVIOUS_TAG,.env}. The source bundle is deleted afterwards.
# Optional environment:
#   DEPLOY_REF  the commit being deployed; becomes the image tag (first 12 characters) and the image label (CI passes the SHA)
#   DEV_NAME    with --dev: a suffix, so several people can each have a stack (dispatchz-dev-<DEV_NAME>)
#   DEV_PORT    with --dev: the VM port for the web UI (default 8001; give each DEV_NAME its own port). The API uses DEV_PORT+1000.
# Reach a dev stack with an SSH tunnel:  ssh -i <key> -L 8001:localhost:8001 <host>
set -euo pipefail
: "${VM_KEY:?set VM_KEY to the path of your private key}"
: "${VM_HOST:?set VM_HOST, e.g. linux1@<vm-ip>}"

mode=tests
target=shared
confirmed=no
for arg in "$@"; do
  case "$arg" in
    --no-tests) mode=skip ;;
    --dev) target=dev ;;
    --yes) confirmed=yes ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$target" = dev ]; then
  project="dispatchz-dev${DEV_NAME:+-$DEV_NAME}"
  port="${DEV_PORT:-8001}"
  case "$port" in ''|*[!0-9]*) echo "DEV_PORT must be a number" >&2; exit 2 ;; esac
  web_bind=127.0.0.1; web_port="$port"; api_port=$((port + 1000))
else
  project=dispatchz
  # Empty: the VM's ~/dispatchz/.env decides, defaulting to the public port 80.
  web_bind=""; web_port=""; api_port=8000
  # The shared stack is the demo everybody looks at; make replacing it from a laptop a deliberate act.
  if [ -z "${CI:-}" ] && [ "$confirmed" != yes ]; then
    if [ -t 0 ]; then
      read -r -p "This replaces the SHARED demo stack (dispatchz). Continue? [y/N] " answer
      case "$answer" in y|Y|yes) ;; *) echo "Aborted. Use --dev to test without touching the demo."; exit 1 ;; esac
    else
      echo "Refusing to replace the shared demo stack without --yes. Use --dev to test without touching it." >&2
      exit 1
    fi
  fi
fi
case "$project" in *[!a-z0-9-]*) echo "DEV_NAME may contain only lowercase letters, digits and hyphens" >&2; exit 2 ;; esac
prefix="$project"      # image names: <prefix>-api, <prefix>-web, <prefix>-test
dir="$project"         # ~/<dir> on the VM

ref="${DEPLOY_REF:-local}"
if [ "$ref" = local ]; then tag="local-$(date -u +%Y%m%d%H%M%S)"; else tag="${ref:0:12}"; fi

cd "$(dirname "$0")/.."
ssh_opts=(-i "$VM_KEY" -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ServerAliveInterval=30)
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
upload=".dispatchz-upload-$dir"

echo "== target: $project, release $tag (commit $ref)"
echo "== building the web image for linux/s390x"
# Built into the local daemon and exported with `docker save`: unlike `--output type=docker`, that also works with the default
# builder on a GitHub runner. The tar goes through a pipe, not a file path, so it works from Git Bash on Windows too.
docker buildx build --platform linux/s390x --load --label "org.opencontainers.image.revision=$ref" -t "$prefix-web:$tag" frontend
docker save "$prefix-web:$tag" | gzip -1 > "$work/web.tar.gz"
docker rmi "$prefix-web:$tag" > /dev/null 2>&1 || true

echo "== packing the API build context (tracked files only)"
git ls-files -z -co --exclude-standard -- backend contracts/openapi.json compose.yml .dockerignore \
  | tar --null --ignore-failed-read -T - -czf "$work/bundle.tgz"
ls -l "$work"/web.tar.gz "$work"/bundle.tgz | awk '{print $5, $9}'

echo "== copying to $VM_HOST"
ssh "${ssh_opts[@]}" "$VM_HOST" "rm -rf ~/$upload && mkdir -p ~/$upload"
scp -q "${ssh_opts[@]}" "$work/web.tar.gz" "$work/bundle.tgz" "$VM_HOST:~/$upload/"

ssh "${ssh_opts[@]}" "$VM_HOST" bash -s -- "$mode" "$ref" "$tag" "$project" "$prefix" "$dir" "$web_bind" "$web_port" "$api_port" "$upload" "$target" <<'REMOTE'
set -euo pipefail
mode="$1"; ref="$2"; tag="$3"; project="$4"; prefix="$5"; dir="$6"; web_bind="$7"; web_port="$8"; api_port="$9"; upload="${10}"; target="${11}"
root="$HOME/$dir"; up="$HOME/$upload"; build="$root/.build"
mkdir -p "$root"

# Two deploys of the same stack must not run at once.
exec 9> "$HOME/.$dir.lock"
flock -n 9 || { echo "FAILED: another deploy of $project is running on the VM"; exit 1; }
trap 'rm -rf "$build" "$up"' EXIT

echo "== VM: $(docker --version), $(docker compose version 2>&1 | head -n1)"
if ! docker compose version > /dev/null 2>&1; then
  echo "FAILED: the Docker Compose plugin is missing on the VM. Install it once: sudo apt-get install -y docker-compose-v2"
  echo "        (or the docker-compose-plugin package from Docker's apt repository), then re-run the deploy."
  exit 1
fi
echo "== VM disk: $(df -h "$HOME" | awk 'NR==2 {print $4 " free of " $2}')"
free_kb="$(df -Pk "$HOME" | awk 'NR==2 {print $4}')"
if [ "$free_kb" -lt 4000000 ]; then echo "WARNING: less than 4 GB free; the build may fail. Old images are removed after a successful deploy."; fi

# Earlier versions of this script copied the whole repository into ~/dispatchz. Remove that; keep the recorded commit.
if [ -d "$root/backend" ] || [ -d "$root/frontend" ]; then
  echo "== removing the old source tree from $root"
  find "$root" -mindepth 1 -maxdepth 1 ! -name DEPLOYED_REF ! -name .env ! -name .build -exec rm -rf {} +
fi
rm -f "$HOME/$dir.tgz"

prev_tag="$(cat "$root/DEPLOYED_TAG" 2>/dev/null || true)"
rm -rf "$build" && mkdir "$build"
tar -xzf "$up/bundle.tgz" -C "$build"
# Windows checkouts can leave CRLF behind; Docker and bash both choke on it.
(cd "$build" && sed -i 's/\r$//' backend/Dockerfile .dockerignore backend/requirements*.txt backend/constraints.txt compose.yml)
cd "$build"

if [ "$mode" = tests ]; then
  echo "== tests (inside the image)"
  docker build -q -f backend/Dockerfile --target test -t "$prefix-test" . > /dev/null
  docker run --rm "$prefix-test"
fi

echo "== building the API image $prefix-api:$tag"
# A failed build must stop the deploy before anything running is touched.
if ! docker build --label "org.opencontainers.image.revision=$ref" -f backend/Dockerfile -t "$prefix-api:$tag" . > "$build/build.log" 2>&1; then
  tail -n 30 "$build/build.log"
  echo "FAILED: the API image did not build; the running stack was left alone"
  exit 1
fi
grep -E "model cached|WARNING: model|DONE|naming to" "$build/build.log" || true

echo "== loading the web image $prefix-web:$tag"
gunzip -c "$up/web.tar.gz" | docker load > /dev/null
arch="$(docker info --format '{{.Architecture}}')"
for img in "$prefix-api:$tag" "$prefix-web:$tag"; do
  got="$(docker image inspect -f '{{.Architecture}}' "$img")"
  echo "   $img: $got"
  if [ "$got" != "$arch" ]; then echo "FAILED: $img is $got, this VM is $arch"; exit 1; fi
done

# Settings for compose.yml. WEB_BIND/WEB_PORT stay unset for the shared stack so ~/dispatchz/.env (or the defaults) decide.
export IMAGE_PREFIX="$prefix" API_HOST_PORT="$api_port"
if [ -n "$web_bind" ]; then export WEB_BIND="$web_bind"; fi
if [ -n "$web_port" ]; then export WEB_PORT="$web_port"; fi

compose_file="$build/compose.yml"
stack() { docker compose -p "$project" --project-directory "$root" -f "$compose_file" "$@"; }
start_stack() { stack up -d --no-build --remove-orphans --wait --wait-timeout 180; }

# Before the first compose-based deploy, a single container from the old deploy script holds the API port.
legacy="$project"; if [ "$target" = shared ]; then legacy=dispatchz-api; fi
legacy_found=no
for c in "$legacy" responsematch-api; do
  if [ "$c" = responsematch-api ] && [ "$target" != shared ]; then continue; fi
  if [ -n "$(docker ps -aq --filter "name=^${c}$")" ]; then legacy_found=yes; docker rm -f "$c" > /dev/null; echo "== removed the old container $c"; fi
done

# The web port the stack is actually published on (from the VM's .env, or the default).
web_url() { local hp; hp="$(stack port web 80 | head -n1)"; echo "http://127.0.0.1:${hp##*:}"; }

# Healthy means: the containers' own checks pass AND the API reports status ok through the web proxy (the models load in the
# background, so "degraded" is normal for the first seconds).
wait_ok() {
  local base body="" page=""
  base="$(web_url)"
  for _ in $(seq 1 40); do
    body="$(curl -s "$base/api/v1/health" || true)"
    # Match the TOP-LEVEL status only: the database component inside the JSON is always "ok".
    case "$body" in '{"status":"ok"'*)
      # Into a variable, not `curl | grep -q`: grep exiting early makes curl fail, and pipefail would report that.
      page="$(curl -fs "$base/" || true)"
      case "$page" in *'<html'*) echo "$body"; echo "web UI: $base/ serves the app shell"; return 0 ;; esac ;;
    esac
    sleep 3
  done
  echo "$body"
  return 1
}

check_revision() {
  local svc cid rev
  for svc in api web; do
    cid="$(stack ps -q "$svc")"
    rev="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$cid")"
    echo "   $svc is running commit $rev"
    if [ "$rev" != "$1" ]; then echo "FAILED: $svc is not running the commit just built ($1)"; return 1; fi
  done
}

export DISPATCHZ_TAG="$tag"
echo "== starting the stack"
if start_stack && echo "== waiting for the API (the model loads in the background)" && wait_ok && check_revision "$ref"; then
  cp "$compose_file" "$root/compose.yml"
  if [ -n "$prev_tag" ] && [ "$prev_tag" != "$tag" ]; then echo "$prev_tag" > "$root/PREVIOUS_TAG"; fi
  echo "$tag" > "$root/DEPLOYED_TAG"
  echo "$ref" > "$root/DEPLOYED_REF"
  # Keep the running release and the one before it (the rollback target); delete the rest, and the build cache older than 3 days.
  keep=" $tag ${prev_tag:-} "
  for kind in api web; do
    docker image ls "$prefix-$kind" --format '{{.Tag}}' | while read -r t; do
      case "$keep" in *" $t "*) ;; *) docker rmi "$prefix-$kind:$t" > /dev/null 2>&1 || true ;; esac
    done
  done
  docker image prune -f > /dev/null
  docker builder prune -f --filter until=72h > /dev/null || true
  echo "== deployed release $tag (commit $ref)"
  docker ps --filter "label=com.docker.compose.project=$project" --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  exit 0
fi

echo "FAILED: the new release did not become healthy. Container logs:"
stack logs --tail 40 || true
if [ -n "$prev_tag" ] && [ "$prev_tag" != "$tag" ] && docker image inspect "$prefix-api:$prev_tag" "$prefix-web:$prev_tag" > /dev/null 2>&1 && [ -f "$root/compose.yml" ]; then
  echo "== rolling back to release $prev_tag (commit $(cat "$root/DEPLOYED_REF" 2>/dev/null || echo unknown))"
  compose_file="$root/compose.yml"; export DISPATCHZ_TAG="$prev_tag"
  if start_stack && wait_ok; then
    docker rmi "$prefix-api:$tag" "$prefix-web:$tag" > /dev/null 2>&1 || true
    echo "ROLLED BACK: release $prev_tag is running again"
  else echo "WARNING: the previous release is not healthy either"; fi
elif [ "$legacy_found" = yes ] && docker image inspect "$legacy" > /dev/null 2>&1; then
  echo "== restoring the old single-container API"
  stack down --remove-orphans > /dev/null 2>&1 || true
  docker run -d --name "$legacy" -p 127.0.0.1:"$api_port":8000 -e DEMO_MODE=true --restart unless-stopped "$legacy" > /dev/null
  echo "ROLLED BACK: the old API container is running again (no web UI)"
else
  echo "There is no previous release to roll back to; the new, unhealthy stack is still running."
fi
exit 1
REMOTE
