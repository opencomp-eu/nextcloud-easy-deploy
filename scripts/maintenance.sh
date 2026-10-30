#!/usr/bin/env bash
# scripts/maintenance.sh on|off — toggle Nextcloud maintenance mode.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/lib.sh"

case "${1:-}" in
	on | off) ;;
	*) die "Usage: scripts/maintenance.sh on|off" ;;
esac

docker exec -u www-data nextcloud php occ --no-interaction maintenance:mode "--$1"
