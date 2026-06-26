#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
ROOT_DIR="${ROOT_DIR:-/private/tmp/voi-micromachine-runtime}"
S2CLIENT_DIR="${S2CLIENT_DIR:-${ROOT_DIR}/s2client-api}"
S2CLIENT_BUILD_DIR="${S2CLIENT_BUILD_DIR:-${S2CLIENT_DIR}/build-latest}"
PROBE_EXECUTABLE="${PROBE_EXECUTABLE:-${S2CLIENT_BUILD_DIR}/bin/voi_bootstrap_probe}"
SC2_ROOT="${SC2_ROOT:-/Users/jinminseong/Desktop/StarCraft2/StarCraft II}"
SC2_LAUNCH_MODE="${SC2_LAUNCH_MODE:-auto}"
SC2_BATTLENET_EXECUTABLE="${SC2_BATTLENET_EXECUTABLE:-/Applications/Battle.net.app/Contents/MacOS/Battle.net}"
SC2_ATTACH_TIMEOUT_MS="${SC2_ATTACH_TIMEOUT_MS:-120000}"
SC2_USE_RUNTIME_DIR_ARGS="${SC2_USE_RUNTIME_DIR_ARGS:-0}"
SC2_TEMP_DIR="${SC2_TEMP_DIR:-/private/tmp/voi-sc2-temp-micromachine-probe}"
SC2_ROOT_ALIAS="${SC2_ROOT_ALIAS:-/private/tmp/voi-sc2-root}"
SC2_POST_CLEAN_SETTLE_SECONDS="${SC2_POST_CLEAN_SETTLE_SECONDS:-5}"
SC2_PORT_START="${SC2_PORT_START:-8168}"
SC2_PORTS=(${SC2_PORTS:-8167 8168})
VOI_SC2_CREATEGAME_MAP_DATA="${VOI_SC2_CREATEGAME_MAP_DATA:-1}"
PROBE_RUN_DIR="${PROBE_RUN_DIR:-/private/tmp/voi-mm-bootstrap-probe}"
PROBE_OUTPUT="${PROBE_OUTPUT:-${PROBE_RUN_DIR}/probe_report.json}"
PROBE_LOG="${PROBE_LOG:-${PROBE_RUN_DIR}/probe.log}"
PROBE_MAX_FRAME="${PROBE_MAX_FRAME:-1200}"
PROBE_STEP_SIZE="${PROBE_STEP_SIZE:-1}"
PROBE_ENEMY_RACE="${PROBE_ENEMY_RACE:-Zerg}"
PROBE_ENEMY_DIFFICULTY="${PROBE_ENEMY_DIFFICULTY:-1}"
MAP_FILE="${MAP_FILE:-AcropolisLE.SC2Map}"
if [[ -z "${SC2_CLEAN_PORTS_BEFORE_LAUNCH+x}" ]]; then
  if [[ -n "${VOI_SC2_CONNECT_PORT:-}" ]]; then
    SC2_CLEAN_PORTS_BEFORE_LAUNCH=0
  else
    SC2_CLEAN_PORTS_BEFORE_LAUNCH=1
  fi
fi

resolve_latest_direct_sc2_executable() {
  local pinned="${SC2_ROOT}/Versions/Base96883/SC2.app/Contents/MacOS/SC2"
  if [[ -x "${pinned}" ]]; then
    printf '%s\n' "${pinned}"
    return
  fi

  local versions_dir="${SC2_ROOT}/Versions"
  if [[ -d "${versions_dir}" ]]; then
    local latest
    latest="$(find "${versions_dir}" -path '*/SC2.app/Contents/MacOS/SC2' -type f | sort -r | head -n 1)"
    if [[ -n "${latest}" && -x "${latest}" ]]; then
      printf '%s\n' "${latest}"
      return
    fi
  fi

  printf '%s\n' "${pinned}"
}

resolve_sc2_executable() {
  case "${SC2_LAUNCH_MODE}" in
    direct)
      resolve_latest_direct_sc2_executable
      ;;
    battlenet)
      printf '%s\n' "${SC2_BATTLENET_EXECUTABLE}"
      ;;
    auto)
      local pinned="${SC2_ROOT}/Versions/Base96883/SC2.app/Contents/MacOS/SC2"
      if [[ -x "${pinned}" ]]; then
        printf '%s\n' "${pinned}"
      else
        resolve_latest_direct_sc2_executable
      fi
      ;;
    *)
      echo "MicroMachine bootstrap probe rejected: SC2_LAUNCH_MODE must be auto, direct, or battlenet." >&2
      exit 2
      ;;
  esac
}

prepare_sc2_runtime_root() {
  if [[ "${SC2_ROOT}" == *" "* ]]; then
    ln -sfn "${SC2_ROOT}" "${SC2_ROOT_ALIAS}"
    printf '%s\n' "${SC2_ROOT_ALIAS}"
  else
    printf '%s\n' "${SC2_ROOT}"
  fi
}

resolve_map_file() {
  local map_file="$1"
  if [[ "${SC2_MAP_AS_PROVIDED:-0}" == "1" ]]; then
    printf '%s\n' "${map_file}"
    return
  fi
  if [[ "${map_file}" == /* ]]; then
    printf '%s\n' "${map_file}"
    return
  fi

  local candidate="${SC2_ROOT}/Maps/${map_file}"
  if [[ -f "${candidate}" ]]; then
    printf '%s\n' "${candidate}"
    return
  fi

  echo "MicroMachine bootstrap probe rejected: map file not found: ${map_file} (looked under ${SC2_ROOT}/Maps)." >&2
  exit 2
}

sc2_port_pids() {
  local port="$1"
  lsof -nP -tiTCP:"${port}" 2>/dev/null | sort -u || true
}

clean_sc2_ports_before_launch() {
  [[ "${SC2_CLEAN_PORTS_BEFORE_LAUNCH}" == "1" ]] || return 0
  local port
  for port in "${SC2_PORTS[@]}"; do
    while IFS= read -r pid; do
      [[ -n "${pid}" ]] || continue
      kill "${pid}" 2>/dev/null || true
    done < <(sc2_port_pids "${port}")
  done
}

settle_after_sc2_port_cleanup() {
  [[ "${SC2_CLEAN_PORTS_BEFORE_LAUNCH}" == "1" ]] || return 0
  [[ "${SC2_POST_CLEAN_SETTLE_SECONDS}" != "0" ]] || return 0
  sleep "${SC2_POST_CLEAN_SETTLE_SECONDS}"
}

prepare_launch_contract() {
  SC2_EXECUTABLE="${SC2_EXECUTABLE:-$(resolve_sc2_executable)}"
  if [[ ! -x "${PROBE_EXECUTABLE}" ]]; then
    echo "MicroMachine bootstrap probe rejected: probe executable is not runnable: ${PROBE_EXECUTABLE}" >&2
    echo "Run integrations/micromachine/scripts/build_macos_local.sh after applying the s2client patch." >&2
    exit 2
  fi
  if [[ ! -x "${SC2_EXECUTABLE}" ]]; then
    echo "MicroMachine bootstrap probe rejected: SC2 executable is not runnable: ${SC2_EXECUTABLE}" >&2
    exit 2
  fi
  if [[ "${SC2_EXECUTABLE}" != "${SC2_BATTLENET_EXECUTABLE}" && "${SC2_USE_RUNTIME_DIR_ARGS}" == "1" ]]; then
    mkdir -p "${SC2_TEMP_DIR}"
    local runtime_root
    runtime_root="$(prepare_sc2_runtime_root)"
    VOI_SC2_EXTRA_ARGS="${VOI_SC2_EXTRA_ARGS:-"-dataDir ${runtime_root} -tempDir ${SC2_TEMP_DIR}"}"
    export VOI_SC2_EXTRA_ARGS
  fi
  MAP_FILE="$(resolve_map_file "${MAP_FILE}")"
}

validate_probe_report() {
  python3 - "$PROBE_OUTPUT" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text())
if payload.get("ok") is not True:
    raise SystemExit(
        "MicroMachine bootstrap probe failed: "
        f"{payload.get('failure_code')} self={payload.get('self_count')} "
        f"workers={payload.get('self_worker_count')} depots={payload.get('resource_depot_count')} "
        f"frame={payload.get('latest_frame')}"
    )
if int(payload.get("self_count", 0) or 0) <= 0:
    raise SystemExit("MicroMachine bootstrap probe false pass: self_count <= 0")
if int(payload.get("self_worker_count", 0) or 0) <= 0:
    raise SystemExit("MicroMachine bootstrap probe false pass: self_worker_count <= 0")
if int(payload.get("resource_depot_count", 0) or 0) <= 0:
    raise SystemExit("MicroMachine bootstrap probe false pass: resource_depot_count <= 0")
print(
    "MicroMachine bootstrap probe passed: "
    f"frame={payload.get('latest_frame')} self={payload.get('self_count')} "
    f"workers={payload.get('self_worker_count')} depots={payload.get('resource_depot_count')}"
)
PY
}

mkdir -p "${PROBE_RUN_DIR}"
rm -f "${PROBE_OUTPUT}" "${PROBE_LOG}"
prepare_launch_contract
clean_sc2_ports_before_launch
settle_after_sc2_port_cleanup

export MAP_FILE
export PROBE_OUTPUT
export PROBE_MAX_FRAME
export PROBE_STEP_SIZE
export PROBE_ENEMY_RACE
export PROBE_ENEMY_DIFFICULTY
export SC2_ATTACH_TIMEOUT_MS
export SC2_PORT_START
export VOI_SC2_CREATEGAME_MAP_DATA

"${PROBE_EXECUTABLE}" \
  -e "${SC2_EXECUTABLE}" \
  -t "${SC2_ATTACH_TIMEOUT_MS}" \
  -s "${PROBE_STEP_SIZE}" \
  -m "${MAP_FILE}" \
  >"${PROBE_LOG}" 2>&1

if [[ ! -f "${PROBE_OUTPUT}" ]]; then
  echo "MicroMachine bootstrap probe rejected: missing probe output ${PROBE_OUTPUT}" >&2
  exit 2
fi

validate_probe_report
