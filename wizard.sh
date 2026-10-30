#!/usr/bin/env bash
# wizard.sh — interactive setup for nextcloud-easy-deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

EASYDEPLOY_INVOKE_ARGS=("$@")
clear_parent_python_env

DEPLOY_YAML="${SCRIPT_DIR}/deploy.yaml"
NO_APPLY=0
PROXY_MODE=""

usage() {
	echo "Usage: bash wizard.sh [--from-engine] [--no-apply] [--proxy-mode standalone|integrate]"
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--help|-h)
			usage
			exit 0
			;;
		--from-engine)
			NO_APPLY=1
			PROXY_MODE="integrate"
			shift
			;;
		--no-apply)
			NO_APPLY=1
			shift
			;;
		--proxy-mode)
			PROXY_MODE="${2:-}"
			shift 2
			;;
		--proxy-mode=*)
			PROXY_MODE="${1#*=}"
			shift
			;;
		*)
			die "Unknown option: $1"
			;;
	esac
done

print_banner() {
	echo
	echo -e "${BOLD}  Nextcloud Easy Deploy — Setup Wizard${RESET}"
	echo -e "  ─────────────────────────────────────────────────────"
	echo
}

suggested_base_domain() {
	(cd "${SCRIPT_DIR}" && uv run python -c 'from scripts.config_edit import suggested_base_domain; print(suggested_base_domain())' 2>/dev/null) || true
}

gather_config() {
	local base_domain domain data_dir office_on office_domain talk_on proxy_mode proceed

	print_banner
	echo -e "  Press Enter to accept a ${CYAN}[default]${RESET}.\n"
	print_data_dir_hint

	base_domain="$(suggested_base_domain)"
	[[ -n "${base_domain}" ]] || base_domain="example.com"

	ask domain "Nextcloud domain" "cloud.${base_domain}"
	base_domain="$(base_domain_from_host "${domain}")"
	ask data_dir "Nextcloud data directory (code, files, database)" "$(default_data_dir nextcloud)"

	echo
	echo -e "${BOLD}  Office (Collabora Online)${RESET}"
	office_domain="office.${base_domain}"
	ask_yn office_on "Edit documents in the browser with Collabora?" "y"
	if [[ "${office_on}" == "y" ]]; then
		ask office_domain "Office domain" "${office_domain}"
		if [[ "${office_domain,,}" == "${domain,,}" ]]; then
			die "The office domain must differ from the Nextcloud domain."
		fi
	fi

	echo
	echo -e "${BOLD}  Talk (video calls)${RESET}"
	echo "  The high-performance backend makes group calls work; it needs TCP and UDP 3478 open."
	ask_yn talk_on "Install the Talk high-performance backend?" "y"

	echo
	echo -e "${BOLD}  Reverse proxy${RESET}"
	if [[ -n "${PROXY_MODE}" ]]; then
		proxy_mode="${PROXY_MODE,,}"
		info "Proxy mode: ${proxy_mode} (set by easydeploy-engine)"
	else
		echo "  standalone — this kit runs Caddy on :443 (single-service VPS)"
		echo "  integrate  — shared Caddy via easydeploy-engine (multi-service VPS)"
		ask proxy_mode "Proxy mode: standalone or integrate" "standalone"
		proxy_mode="${proxy_mode,,}"
	fi
	if [[ "${proxy_mode}" != "standalone" && "${proxy_mode}" != "integrate" ]]; then
		die "proxy mode must be 'standalone' or 'integrate'"
	fi

	echo
	echo -e "${BOLD}  Summary${RESET}"
	echo "  Nextcloud:   https://${domain}"
	if [[ "${office_on}" == "y" ]]; then
		echo "  Office:      https://${office_domain}"
	else
		echo "  Office:      disabled"
	fi
	if [[ "${talk_on}" == "y" ]]; then
		echo "  Talk:        high-performance backend on (TURN port 3478)"
	else
		echo "  Talk:        built-in only (small calls)"
	fi
	echo "  Data:        ${data_dir}"
	echo "  Proxy mode:  ${proxy_mode}"
	echo
	echo "  Ensure DNS A/AAAA for ${domain} points to this server before continuing."
	if [[ "${office_on}" == "y" ]]; then
		echo "  Also point ${office_domain} here."
	fi
	echo

	if [[ "${NO_APPLY}" == "1" ]]; then
		ask_yn proceed "Write deploy.yaml?" "y"
	else
		ask_yn proceed "Write deploy.yaml and deploy now?" "y"
	fi
	[[ "${proceed}" == "y" ]] || {
		info "Cancelled."
		exit 0
	}

	cd "${SCRIPT_DIR}"
	uv run python - <<PY
from scripts.config_edit import update_from_wizard
from pathlib import Path

update_from_wizard(
    domain=${domain@Q},
    data_dir=${data_dir@Q},
    office_enabled=${office_on@Q} == "y",
    office_domain=${office_domain@Q},
    talk_enabled=${talk_on@Q} == "y",
    proxy_mode=${proxy_mode@Q},
    path=Path(${DEPLOY_YAML@Q}),
)
PY

	success "Wrote ${DEPLOY_YAML}"
}

main() {
	bash "${SCRIPT_DIR}/ensure-dependencies.sh"
	ensure_docker_group_session "${EASYDEPLOY_INVOKE_ARGS[@]}"
	cd "${SCRIPT_DIR}"
	gather_config
	if [[ "${NO_APPLY}" == "1" ]]; then
		info "Skipping apply (--no-apply / --from-engine). easydeploy-engine will apply."
		return 0
	fi
	bash "${SCRIPT_DIR}/apply.sh"
}

main "$@"
