#!/usr/bin/env bash
# Bring up Nextcloud + Collabora + Talk and apply local wiring.
set -euo pipefail
cd "$(dirname "$0")"

need() { command -v "$1" >/dev/null || { echo "missing dependency: $1" >&2; exit 1; }; }
need docker
need openssl

if [[ ! -f .env ]]; then
  echo "Creating .env from .env.example (generating secrets)..."
  cp .env.example .env
  turn=$(openssl rand -hex 32)
  signaling=$(openssl rand -hex 32)
  internal=$(openssl rand -hex 32)
  db_pass=$(openssl rand -hex 16)
  root_pass=$(openssl rand -hex 16)
  admin_pass=$(openssl rand -hex 12)
  collab_pass=$(openssl rand -hex 12)
  sed -i \
    -e "s|^MYSQL_PASSWORD=.*|MYSQL_PASSWORD=${db_pass}|" \
    -e "s|^MYSQL_ROOT_PASSWORD=.*|MYSQL_ROOT_PASSWORD=${root_pass}|" \
    -e "s|^NEXTCLOUD_ADMIN_PASSWORD=.*|NEXTCLOUD_ADMIN_PASSWORD=${admin_pass}|" \
    -e "s|^COLLABORA_ADMIN_PASSWORD=.*|COLLABORA_ADMIN_PASSWORD=${collab_pass}|" \
    -e "s|^TURN_SECRET=.*|TURN_SECRET=${turn}|" \
    -e "s|^SIGNALING_SECRET=.*|SIGNALING_SECRET=${signaling}|" \
    -e "s|^INTERNAL_SECRET=.*|INTERNAL_SECRET=${internal}|" \
    .env
  echo "Wrote secrets to .env — save NEXTCLOUD_ADMIN_PASSWORD for login."
fi

# shellcheck disable=SC1091
set -a && source .env && set +a

: "${MYSQL_PASSWORD:?set MYSQL_PASSWORD in .env}"
: "${MYSQL_ROOT_PASSWORD:?set MYSQL_ROOT_PASSWORD in .env}"
: "${NEXTCLOUD_ADMIN_PASSWORD:?set NEXTCLOUD_ADMIN_PASSWORD in .env}"
: "${COLLABORA_ADMIN_PASSWORD:?set COLLABORA_ADMIN_PASSWORD in .env}"
: "${TURN_SECRET:?set TURN_SECRET in .env}"
: "${SIGNALING_SECRET:?set SIGNALING_SECRET in .env}"
: "${INTERNAL_SECRET:?set INTERNAL_SECRET in .env}"

NEXTCLOUD_URL="${NEXTCLOUD_URL:-http://localhost:8082}"
NEXTCLOUD_PORT="${NEXTCLOUD_PORT:-8082}"
TALK_HOST="${TALK_HOST:-localhost}"
TALK_SIGNALING_PORT="${TALK_SIGNALING_PORT:-8181}"
TALK_TURN_PORT="${TALK_TURN_PORT:-3478}"
COLLABORA_WOPI_URL="${COLLABORA_WOPI_URL:-http://host.docker.internal:9980}"

echo "Starting stack..."
docker compose up -d

occ() {
  docker compose exec -T -u www-data app php occ "$@"
}

echo "Waiting for Nextcloud..."
for i in $(seq 1 60); do
  if occ status 2>/dev/null | grep -q 'installed: true'; then
    break
  fi
  sleep 2
  if [[ $i -eq 60 ]]; then
    echo "Nextcloud did not become ready in time" >&2
    exit 1
  fi
done

# Local split-horizon: browser and containers both use these hostnames
host_port="${NEXTCLOUD_URL#*://}"
host_port="${host_port%%/*}"
occ config:system:set overwrite.cli.url --value="${NEXTCLOUD_URL}"
occ config:system:set overwritehost --value="${host_port}"
occ config:system:set overwriteprotocol --value="${NEXTCLOUD_URL%%://*}"
occ config:system:set allow_local_remote_servers --type=boolean --value=true

APPS=(deck calendar contacts mail spreed richdocuments)
for app in "${APPS[@]}"; do
  if ! occ app:getpath "$app" >/dev/null 2>&1; then
    echo "Installing app: $app"
    occ app:install "$app" || true
  fi
  occ app:enable "$app" >/dev/null || true
done

echo "Configuring Collabora (richdocuments)..."
occ config:app:set richdocuments wopi_url --value="${COLLABORA_WOPI_URL}"
occ config:app:set richdocuments public_wopi_url --value="http://${COLLABORA_DOMAIN:-localhost:9980}" || true
occ richdocuments:activate-config -w "${COLLABORA_WOPI_URL}" || true

echo "Configuring Talk signaling / TURN..."
# Replace existing entries so re-runs stay in sync with .env
while read -r idx; do
  [[ -n "$idx" ]] && occ talk:signaling:delete "$idx" >/dev/null 2>&1 || true
done < <(occ talk:signaling:list 2>/dev/null | awk '/^[0-9]+:/{print $1}' | tr -d ':')
occ talk:signaling:add "ws://${TALK_HOST}:${TALK_SIGNALING_PORT}" "${SIGNALING_SECRET}"

while read -r idx; do
  [[ -n "$idx" ]] && occ talk:turn:delete "$idx" >/dev/null 2>&1 || true
done < <(occ talk:turn:list 2>/dev/null | awk '/^[0-9]+:/{print $1}' | tr -d ':')
occ talk:turn:add turn "${TALK_HOST}:${TALK_TURN_PORT}" udp,tcp --secret="${TURN_SECRET}"
occ talk:stun:add "${TALK_HOST}:${TALK_TURN_PORT}" >/dev/null 2>&1 || true

echo
echo "Ready: ${NEXTCLOUD_URL}"
echo "Login: ${NEXTCLOUD_ADMIN_USER:-admin} / (see NEXTCLOUD_ADMIN_PASSWORD in .env)"
