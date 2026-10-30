#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

info "Starting Nextcloud stack…"
bash "${SCRIPT_DIR}/scripts/compose.sh" up -d
success "Started."
