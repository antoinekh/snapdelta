#!/bin/bash
# Run the tests of the PostgreSQL store against a server in a container that this script starts and removes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${POSTGRES_IMAGE:-postgres:18-alpine}"
PORT="${POSTGRES_PORT:-55432}"
NAME="snapdelta-test-postgres-$$"

usage() {
  cat <<'USAGE'
Usage: scripts/test-postgres.sh [PYTEST ARGUMENTS]

Start a PostgreSQL server in a container, run the tests of packages/snapdelta-postgres
against it, then remove the container. Needs docker.

Options:
  -h, --help   Show this help

Environment:
  POSTGRES_IMAGE   The image of the server (default: postgres:18-alpine)
  POSTGRES_PORT    The port of the server on 127.0.0.1 (default: 55432)

Examples:
  scripts/test-postgres.sh
  scripts/test-postgres.sh -k contract
  POSTGRES_IMAGE=postgres:16 make test-postgres
USAGE
}

step() {
  if [[ -t 1 ]]; then
    printf '\n\033[1;36m%s\033[0m\n' "$*"
  else
    printf '\n%s\n' "$*"
  fi
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  usage
  exit 0
fi

step "Start $IMAGE on 127.0.0.1:$PORT"
docker run --detach --rm --name "$NAME" --env POSTGRES_PASSWORD=snapdelta \
  --publish "127.0.0.1:$PORT:5432" "$IMAGE" >/dev/null
trap 'docker stop "$NAME" >/dev/null' EXIT

step "Wait for the server"
tries=0
until docker exec "$NAME" pg_isready --username postgres --quiet; do
  (( tries += 1 ))
  if (( tries > 60 )); then
    echo "the server did not start" >&2
    exit 1
  fi
  sleep 1
done

step "Run the tests"
cd "$ROOT"
SNAPDELTA_POSTGRES_URL="postgresql://postgres@127.0.0.1:$PORT/postgres" PGPASSWORD=snapdelta \
  uv run pytest -q packages/snapdelta-postgres "$@"
