#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

if [[ ! -f "${SCRIPT_DIR}/.nextcloud-easy-deploy/compose/files.txt" ]]; then
	info "Nothing to stop (apply.sh has not run)."
	exit 0
fi

info "Stopping Nextcloud stack…"
bash "${SCRIPT_DIR}/scripts/compose.sh" down --remove-orphans || true
success "Stopped."
