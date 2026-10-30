#!/usr/bin/env bash
# scripts/start-postgres.sh — restore hook: start only PostgreSQL so the
# database dump can be loaded while Nextcloud itself stays stopped.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/lib.sh"

info "Starting PostgreSQL…"
bash "${SCRIPT_DIR}/compose.sh" up -d --wait nextcloud_postgres
