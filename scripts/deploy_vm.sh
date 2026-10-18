#!/usr/bin/env bash
# Copy the working tree to the LinuxONE VM, rebuild the images there, run the tests, and (re)start the API.
# Run from anywhere in a bash shell on your machine:
#   export VM_KEY="E:\path\to\key.pem" VM_HOST=linux1@<vm-ip>
#   bash scripts/deploy_vm.sh --dev              # YOUR OWN test container (dispatchz-dev on VM port 8001); the demo is untouched
#   bash scripts/deploy_vm.sh --dev --no-tests   # same, skipping the test image
#   bash scripts/deploy_vm.sh --yes              # replace the SHARED demo API (dispatchz-api on port 8000)
# The key path and host are never stored in the repo. The GitHub Actions workflow (.github/workflows/ci.yml) runs the shared
# deploy on every push to main (it sets CI=true, so it needs no --yes). The script exits non-zero if the tests fail or the API
# does not become healthy, and in that case puts the previous image back, so a failed deploy never leaves a broken API running.
# Optional environment:
#   DEPLOY_REF  recorded as an image label and in ~/dispatchz/DEPLOYED_REF (~/<name>/ for --dev) (CI passes the commit SHA)
#   DEV_NAME    with --dev: a suffix, so several people can each have a container (dispatchz-dev-<DEV_NAME>)
#   DEV_PORT    with --dev: the VM port to publish on (default 8001; give each DEV_NAME its own port)
# Reach a dev container with an SSH tunnel:  ssh -i <key> -L 8001:localhost:8001 <host>
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
  name="dispatchz-dev${DEV_NAME:+-$DEV_NAME}"
  port="${DEV_PORT:-8001}"
else
  name=dispatchz-api
  port=8000
  # The shared container is the demo everybody looks at; make replacing it from a laptop a deliberate act.
  if [ -z "${CI:-}" ] && [ "$confirmed" != yes ]; then
    if [ -t 0 ]; then
      read -r -p "This replaces the SHARED demo API (dispatchz-api). Continue? [y/N] " answer
      case "$answer" in y|Y|yes) ;; *) echo "Aborted. Use --dev to test without touching the demo."; exit 1 ;; esac
    else
      echo "Refusing to replace the shared demo API without --yes. Use --dev to test without touching it." >&2
      exit 1
    fi
  fi
fi
case "$name" in *[!a-z0-9-]*) echo "DEV_NAME may contain only lowercase letters, digits and hyphens" >&2; exit 2 ;; esac
# Source directory and archive on the VM. The shared deployment keeps its original ~/dispatchz location.
dir="$name"; [ "$target" = shared ] && dir=dispatchz
case "$port" in ''|*[!0-9]*) echo "DEV_PORT must be a number" >&2; exit 2 ;; esac

cd "$(dirname "$0")/.."
ssh_opts=(-i "$VM_KEY" -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ServerAliveInterval=30)
archive="${TMPDIR:-/tmp}/$dir.tgz"

echo "== target: $name on VM port $port"
echo "== packing working tree"
tar --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=node_modules --exclude=.git -czf "$archive" .
echo "== copying to $VM_HOST"
scp "${ssh_opts[@]}" "$archive" "$VM_HOST:~/$dir.tgz"

ssh "${ssh_opts[@]}" "$VM_HOST" bash -s -- "$mode" "${DEPLOY_REF:-local}" "$name" "$port" "$dir" <<'REMOTE'
set -euo pipefail
name="$3"; port="$4"; dir="$5"
test_image="$name-test"; [ "$name" = dispatchz-api ] && test_image=dispatchz-test
# DEPLOYED_REF names the commit that is actually running. It changes only after the new API reports healthy; any other way
# out of this script (failed tests, failed build, rollback) puts the old value back.
deployed="$(cat ~/"$dir"/DEPLOYED_REF 2>/dev/null || echo unknown)"
rm -rf ~/"$dir" && mkdir ~/"$dir"
trap 'echo "$deployed" > ~/"$dir"/DEPLOYED_REF' EXIT
tar -xzf ~/"$dir".tgz -C ~/"$dir"
cd ~/"$dir"
# Windows checkouts can leave CRLF behind; Docker and bash both choke on it.
sed -i 's/\r$//' backend/Dockerfile .dockerignore backend/requirements*.txt backend/constraints.txt scripts/*.sh

if [ "$1" = tests ]; then
  echo "== tests (inside the image)"
  docker build -q -f backend/Dockerfile --target test -t "$test_image" . > /dev/null
  docker run --rm "$test_image"
fi

echo "== building runtime image"
# Keep the image that is running now so a bad deploy can be undone. The $name tag is always the running image: a
# rollback below points it back at the good one. (Tagging by container image ID does not work with Docker's containerd image store.)
docker image inspect "$name" > /dev/null 2>&1 && docker tag "$name" "$name:previous" || true
# A failed build must stop the deploy: the old image still exists under the same tag, so checking for it would not notice.
if ! docker build --label "org.opencontainers.image.revision=$2" -f backend/Dockerfile -t "$name" . > build.log 2>&1; then
  tail -n 30 build.log
  echo "FAILED: the runtime image did not build; the running API was left alone"
  exit 1
fi
grep -E "model cached|WARNING: model|ERROR|DONE|naming to" build.log || true

start_api() {
  # "responsematch-api" is the shared container's old name (before the DispatchZ rename). It still holds port 8000 on a VM
  # deployed earlier, so remove it too.
  if [ "$name" = dispatchz-api ]; then docker rm -f responsematch-api > /dev/null 2>&1 || true; fi
  docker rm -f "$name" > /dev/null 2>&1 || true
  docker run -d --name "$name" -p 127.0.0.1:"$port":8000 -e DEMO_MODE=true --restart unless-stopped "$1" > /dev/null
}

wait_healthy() {
  local body=""
  for _ in $(seq 1 40); do
    body="$(curl -s localhost:"$port"/api/v1/health || true)"
    # Match the TOP-LEVEL status only: the database component inside the JSON is always "ok".
    case "$body" in '{"status":"ok"'*) echo "$body"; return 0 ;; esac
    sleep 3
  done
  echo "$body"
  return 1
}

start_api "$name"
echo "== waiting for the API (the model loads in the background)"
if ! wait_healthy; then
  echo "FAILED: the API did not report status ok within 2 minutes. Last container logs:"
  docker logs --tail 40 "$name" || true
  if docker image inspect "$name:previous" > /dev/null 2>&1; then
    echo "== rolling back to the previous image"
    # Put the good image back under the main tag, so the next deploy does not record the broken one as "previous".
    docker tag "$name:previous" "$name"
    start_api "$name"
    wait_healthy || echo "WARNING: the previous image is not healthy either"
    echo "ROLLED BACK: the previous version (commit $deployed) is running again"
  else
    echo "There is no previous image to roll back to; the new, unhealthy container is still running."
  fi
  exit 1
fi
deployed="$2"
docker ps --format 'table {{.Names}}\t{{.Status}}'
REMOTE
