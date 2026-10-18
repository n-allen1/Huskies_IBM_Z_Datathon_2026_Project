#!/usr/bin/env bash
# Local PostgreSQL for development, and the API running against it.
#
#   scripts/db_dev.sh up        start (or create) the database container and wait until it is ready
#   scripts/db_dev.sh migrate   apply the Alembic migrations in database/
#   scripts/db_dev.sh api       run the API on :${API_PORT:-8000} with STORE=postgres (seeds an empty database)
#   scripts/db_dev.sh psql      open psql inside the container
#   scripts/db_dev.sh status    show whether the container is running and the migration it is at
#   scripts/db_dev.sh down      stop and remove the container (the data volume is kept)
#   scripts/db_dev.sh destroy   down, and delete the data volume
#
# The container has its own name, volume and published port, so it does not touch any other
# container or compose project. Settings come from the environment or an untracked .env
# (see .env.example): DB_PASSWORD is required; DB_PORT, DB_USER, DB_NAME, DB_CONTAINER, API_PORT
# and PYTHON are optional. The port is published on 127.0.0.1 only.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then set -a; . ./.env; set +a; fi

: "${DB_PASSWORD:?Set DB_PASSWORD in .env or the environment (never commit it)}"
export DB_HOST="${DB_HOST:-localhost}" DB_PORT="${DB_PORT:-5432}" DB_USER="${DB_USER:-rm_user}" DB_NAME="${DB_NAME:-responsematch}"
NAME="${DB_CONTAINER:-dispatchz-dev-db}"
PY="${PYTHON:-python}"
IMAGE="${DB_IMAGE:-postgres:16-alpine}"

state() {
  local s
  s="$(docker ps -a --filter "name=^${NAME}$" --format '{{.State}}')"
  echo "${s:-absent}"
}

wait_ready() {
  for _ in $(seq 1 60); do
    if docker exec "$NAME" pg_isready -q -U "$DB_USER" -d "$DB_NAME"; then return 0; fi
    sleep 1
  done
  echo "PostgreSQL in $NAME did not become ready; see: docker logs $NAME" >&2
  return 1
}

case "${1:-}" in
  up)
    case "$(state)" in
      running) echo "$NAME is already running." ;;
      absent)
        docker run -d --name "$NAME" --label purpose=dispatchz-dev-db \
          -e POSTGRES_USER="$DB_USER" -e POSTGRES_PASSWORD="$DB_PASSWORD" -e POSTGRES_DB="$DB_NAME" \
          -v "$NAME-data:/var/lib/postgresql/data" -p "127.0.0.1:$DB_PORT:5432" "$IMAGE" >/dev/null
        ;;
      *) docker start "$NAME" >/dev/null ;;
    esac
    wait_ready
    echo "PostgreSQL is ready on 127.0.0.1:$DB_PORT (database $DB_NAME, user $DB_USER). Next: scripts/db_dev.sh migrate"
    ;;
  migrate)
    (cd database && "$PY" -m alembic upgrade head)
    ;;
  api)
    cd backend
    STORE=postgres DEMO_MODE="${DEMO_MODE:-true}" exec "$PY" -m uvicorn app.main:app --port "${API_PORT:-8000}"
    ;;
  psql)
    exec docker exec -it "$NAME" psql -U "$DB_USER" -d "$DB_NAME"
    ;;
  status)
    echo "container $NAME: $(state)"
    if [ "$(state)" = running ]; then
      docker exec "$NAME" psql -tA -U "$DB_USER" -d "$DB_NAME" -c "SELECT 'migration: ' || version_num FROM alembic_version" 2>/dev/null \
        || echo "migration: none applied"
    fi
    ;;
  down)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    echo "Removed container $NAME; volume $NAME-data kept."
    ;;
  destroy)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm "$NAME-data" >/dev/null 2>&1 || true
    echo "Removed container and volume $NAME-data."
    ;;
  *)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
