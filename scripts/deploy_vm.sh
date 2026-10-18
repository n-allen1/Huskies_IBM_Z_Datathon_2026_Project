#!/usr/bin/env bash
# Copy the working tree to the LinuxONE VM, rebuild the images there, run the tests, and (re)start the API.
# Run from anywhere in a bash shell on your machine:
#   export VM_KEY="E:\path\to\key.pem" VM_HOST=linux1@<vm-ip>
#   bash scripts/deploy_vm.sh              # tests + rebuild + restart
#   bash scripts/deploy_vm.sh --no-tests   # skip the test image
# The key path and host are never stored in the repo.
set -euo pipefail
: "${VM_KEY:?set VM_KEY to the path of your private key}"
: "${VM_HOST:?set VM_HOST, e.g. linux1@<vm-ip>}"
mode=tests
[ "${1:-}" = "--no-tests" ] && mode=skip

cd "$(dirname "$0")/.."
ssh_opts=(-i "$VM_KEY" -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ServerAliveInterval=30)
archive="${TMPDIR:-/tmp}/dispatchz.tgz"

echo "== packing working tree"
tar --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=node_modules --exclude=.git -czf "$archive" .
echo "== copying to $VM_HOST"
scp "${ssh_opts[@]}" "$archive" "$VM_HOST:~/dispatchz.tgz"

ssh "${ssh_opts[@]}" "$VM_HOST" bash -s -- "$mode" "${DEPLOY_REF:-local}" <<'REMOTE'
set -euo pipefail
# DEPLOYED_REF names the commit that is actually running. It changes only after the new API reports healthy; any other way
# out of this script (failed tests, failed build, rollback) puts the old value back.
deployed="$(cat ~/dispatchz/DEPLOYED_REF 2>/dev/null || echo unknown)"
rm -rf ~/dispatchz && mkdir ~/dispatchz
trap 'echo "$deployed" > ~/dispatchz/DEPLOYED_REF' EXIT
tar -xzf ~/dispatchz.tgz -C ~/dispatchz
cd ~/dispatchz
# Windows checkouts can leave CRLF behind; Docker and bash both choke on it.
sed -i 's/\r$//' backend/Dockerfile .dockerignore backend/requirements*.txt backend/constraints.txt scripts/*.sh

if [ "$1" = tests ]; then
  echo "== tests (inside the image)"
  docker build -q -f backend/Dockerfile --target test -t dispatchz-test . > /dev/null
  docker run --rm dispatchz-test
fi

echo "== building runtime image"
# Keep the image that is running now so a bad deploy can be undone. The dispatchz-api tag is always the running image: a
# rollback below points it back at the good one. (Tagging by container image ID does not work with Docker's containerd image store.)
docker image inspect dispatchz-api > /dev/null 2>&1 && docker tag dispatchz-api dispatchz-api:previous || true
# A failed build must stop the deploy: the old image still exists under the same tag, so checking for it would not notice.
if ! docker build --label "org.opencontainers.image.revision=$2" -f backend/Dockerfile -t dispatchz-api . > build.log 2>&1; then
  tail -n 30 build.log
  echo "FAILED: the runtime image did not build; the running API was left alone"
  exit 1
fi
grep -E "model cached|WARNING: model|ERROR|DONE|naming to" build.log || true

start_api() {
  # "responsematch-api" is the container's old name (before the DispatchZ rename). It still holds port 8000 on a VM
  # deployed earlier, so remove it too.
  docker rm -f dispatchz-api responsematch-api > /dev/null 2>&1 || true
  docker run -d --name dispatchz-api -p 127.0.0.1:8000:8000 -e DEMO_MODE=true --restart unless-stopped "$1" > /dev/null
}

wait_healthy() {
  local body=""
  for _ in $(seq 1 40); do
    body="$(curl -s localhost:8000/api/v1/health || true)"
    # Match the TOP-LEVEL status only: the database component inside the JSON is always "ok".
    case "$body" in '{"status":"ok"'*) echo "$body"; return 0 ;; esac
    sleep 3
  done
  echo "$body"
  return 1
}

start_api dispatchz-api
echo "== waiting for the API (the model loads in the background)"
if ! wait_healthy; then
  echo "FAILED: the API did not report status ok within 2 minutes. Last container logs:"
  docker logs --tail 40 dispatchz-api || true
  if docker image inspect dispatchz-api:previous > /dev/null 2>&1; then
    echo "== rolling back to the previous image"
    # Put the good image back under the main tag, so the next deploy does not record the broken one as "previous".
    docker tag dispatchz-api:previous dispatchz-api
    start_api dispatchz-api
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
