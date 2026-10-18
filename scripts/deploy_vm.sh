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
ssh_opts=(-i "$VM_KEY" -o StrictHostKeyChecking=accept-new)
archive="${TMPDIR:-/tmp}/dispatchz.tgz"

echo "== packing working tree"
tar --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=node_modules --exclude=.git -czf "$archive" .
echo "== copying to $VM_HOST"
scp "${ssh_opts[@]}" "$archive" "$VM_HOST:~/dispatchz.tgz"

ssh "${ssh_opts[@]}" "$VM_HOST" bash -s -- "$mode" <<'REMOTE'
set -euo pipefail
rm -rf ~/dispatchz && mkdir ~/dispatchz
tar -xzf ~/dispatchz.tgz -C ~/dispatchz
cd ~/dispatchz
# Windows checkouts can leave CRLF behind; Docker and bash both choke on it.
sed -i 's/\r$//' backend/Dockerfile .dockerignore backend/requirements*.txt scripts/*.sh

if [ "$1" = tests ]; then
  echo "== tests (inside the image)"
  docker build -q -f backend/Dockerfile --target test -t dispatchz-test . > /dev/null
  docker run --rm dispatchz-test
fi

echo "== building runtime image"
docker build -f backend/Dockerfile -t dispatchz-api . 2>&1 | grep -E "model cached|WARNING: model|ERROR|DONE|naming to" || true

# "responsematch-api" is the container's old name (before the DispatchZ rename). It still holds port 8000 on a VM
# deployed earlier, so remove it too.
docker rm -f dispatchz-api responsematch-api > /dev/null 2>&1 || true
docker run -d --name dispatchz-api -p 127.0.0.1:8000:8000 -e DEMO_MODE=true --restart unless-stopped dispatchz-api > /dev/null

echo "== waiting for the API (the model loads in the background)"
body=""
for _ in $(seq 1 40); do
  body="$(curl -s localhost:8000/api/v1/health || true)"
  # Match the TOP-LEVEL status only: the database component inside the JSON is always "ok".
  case "$body" in '{"status":"ok"'*) break ;; esac
  sleep 3
done
echo "$body"
docker ps --format 'table {{.Names}}\t{{.Status}}'
REMOTE
