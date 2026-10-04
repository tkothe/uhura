#!/usr/bin/env bash
# Smoke test for the Docker image, run the way docs/setup.md runs it: an env file made from
# .env.example, `docker run --env-file … -v …:/data`. Needs docker, curl and openssl; no
# ElevenLabs account. Used by CI; locally: scripts/docker_smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."

image=uhura-smoke:test
name=uhura-smoke-$$
volume=uhura-smoke-$$
port=${PORT:-18787}
url=http://127.0.0.1:$port
tmp=$(mktemp -d)

cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  docker rmi "$image" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

fail() {
  echo "FAIL $*"
  docker logs "$name" 2>&1 | tail -20 || true
  exit 1
}

# An env file as a new user would have it: the template with fresh credentials.
token=$(openssl rand -hex 24)
secret=$(openssl rand -hex 24)
sed -e "s/^UHURA_TOKENS=.*/UHURA_TOKENS=smoke:$token/" \
    -e "s/^UHURA_TOOL_SECRET=.*/UHURA_TOOL_SECRET=$secret/" .env.example > "$tmp/.env"

docker build -q -t "$image" . >/dev/null
echo "ok   image builds"

docker run -d --name "$name" --env-file "$tmp/.env" -p "127.0.0.1:$port:8787" -v "$volume:/data" "$image" >/dev/null
for _ in $(seq 1 30); do
  curl -sf "$url/health" >/dev/null && break
  [ "$(docker inspect -f '{{.State.Running}}' "$name")" = true ] || fail "container stopped on start"
  sleep 1
done
curl -sf "$url/health" >/dev/null || fail "/health did not answer"
echo "ok   starts with an env file made from .env.example"

code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $token" "$url/calls")
[ "$code" = 200 ] || fail "an authenticated request returned HTTP $code"
echo "ok   answers an authenticated request"

[ "$(docker exec "$name" id -un)" = uhura ] || fail "does not run as the non-root user uhura"
echo "ok   runs as the non-root user uhura"

docker exec "$name" test -f /data/uhura.db || fail "the database is not in the /data volume"
echo "ok   keeps its database in the /data volume"
docker rm -f "$name" >/dev/null

sed "s/^UHURA_TOKENS=.*/UHURA_TOKENS=smoke:change-me/" "$tmp/.env" > "$tmp/weak.env"
if out=$(docker run --rm --env-file "$tmp/weak.env" "$image" 2>&1); then
  fail "started with a placeholder token"
fi
echo "$out" | grep -q "Not starting" || fail "unexpected output with a placeholder token: $out"
echo "ok   refuses to start with a placeholder token"
