#!/usr/bin/env bash
# scripts/compose.sh — run docker compose with the files and env apply.sh rendered.
# Usage: scripts/compose.sh <compose args...>   e.g. scripts/compose.sh up -d
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/lib.sh"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_DIR="${PROJECT_ROOT}/.nextcloud-easy-deploy"
ENV_FILE="${STATE_DIR}/compose.env"
FILES_LIST="${STATE_DIR}/compose/files.txt"

[[ -f "${ENV_FILE}" && -f "${FILES_LIST}" ]] || die "Missing rendered compose state — run bash apply.sh first"

IFS=' ' read -ra DOCKER_COMPOSE <<<"$(docker_compose_cmd)"

compose_args=(-p "nextcloud-easy-deploy")
while IFS= read -r file || [[ -n "${file}" ]]; do
	[[ -n "${file}" ]] && compose_args+=(-f "${file}")
done <"${FILES_LIST}"

env_args=()
while IFS= read -r line || [[ -n "${line}" ]]; do
	[[ -z "${line}" || "${line}" == \#* ]] && continue
	env_args+=("${line}")
done <"${ENV_FILE}"

cd "${PROJECT_ROOT}/compose"
exec env "${env_args[@]}" "${DOCKER_COMPOSE[@]}" "${compose_args[@]}" "$@"
