#!/usr/bin/env bash
set -euo pipefail

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live-hold)
      export SMOKE_KEEP_RUNNING_AFTER_PASS=1
      export SMOKE_MAX_ATTEMPTS="${SMOKE_MAX_ATTEMPTS:-1}"
      export SMOKE_MANUAL_LIVE_MODE="${SMOKE_MANUAL_LIVE_MODE:-1}"
      export SMOKE_AUTO_AGGRESSIVE_PROFILE="${SMOKE_AUTO_AGGRESSIVE_PROFILE:-0}"
      ;;
    --blackboard-dir)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "MicroMachine smoke rejected: --blackboard-dir requires a value." >&2
        exit 2
      fi
      export BLACKBOARD_DIR="$2"
      shift
      ;;
    --max-attempts)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "MicroMachine smoke rejected: --max-attempts requires a value." >&2
        exit 2
      fi
      export SMOKE_MAX_ATTEMPTS="$2"
      shift
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "MicroMachine smoke rejected: unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MICROMACHINE_DIR="${MICROMACHINE_DIR:-/private/tmp/voi-micromachine-runtime/MicroMachine}"
ROOT_DIR="${ROOT_DIR:-$(dirname "${MICROMACHINE_DIR}")}"
S2CLIENT_DIR="${S2CLIENT_DIR:-${ROOT_DIR}/s2client-api}"
MICROMACHINE_BUILD_DIR="${MICROMACHINE_BUILD_DIR:-${MICROMACHINE_DIR}/build-latest-api}"
MICROMACHINE_BUILD_IDENTITY_REPORT="${MICROMACHINE_BUILD_IDENTITY_REPORT:-${MICROMACHINE_BUILD_DIR}/voi_build_identity.json}"
SMOKE_REQUIRE_BUILD_IDENTITY="${SMOKE_REQUIRE_BUILD_IDENTITY:-1}"
SC2_ROOT="${SC2_ROOT:-/Users/jinminseong/Desktop/StarCraft2/StarCraft II}"
SC2_LAUNCH_MODE="${SC2_LAUNCH_MODE:-auto}"
SC2_BATTLENET_EXECUTABLE="${SC2_BATTLENET_EXECUTABLE:-/Applications/Battle.net.app/Contents/MacOS/Battle.net}"
SC2_BATTLENET_GAME="${SC2_BATTLENET_GAME:-s2_kokr}"
SC2_ATTACH_TIMEOUT_MS="${SC2_ATTACH_TIMEOUT_MS:-120000}"
SC2_USE_RUNTIME_DIR_ARGS="${SC2_USE_RUNTIME_DIR_ARGS:-0}"
SC2_TEMP_DIR="${SC2_TEMP_DIR:-/private/tmp/voi-sc2-temp-micromachine}"
SC2_ROOT_ALIAS="${SC2_ROOT_ALIAS:-/private/tmp/voi-sc2-root}"
SC2_POST_CLEAN_SETTLE_SECONDS="${SC2_POST_CLEAN_SETTLE_SECONDS:-5}"
VOI_SC2_CREATEGAME_MAP_DATA="${VOI_SC2_CREATEGAME_MAP_DATA:-1}"
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
      echo "MicroMachine smoke rejected: SC2_LAUNCH_MODE must be auto, direct, or battlenet." >&2
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

  echo "MicroMachine smoke rejected: map file not found: ${map_file} (looked under ${SC2_ROOT}/Maps)." >&2
  exit 2
}

prepare_launch_contract() {
  if [[ ! -x "${SC2_EXECUTABLE}" ]]; then
    echo "MicroMachine smoke rejected: SC2 executable is not runnable: ${SC2_EXECUTABLE}" >&2
    exit 2
  fi
  if [[ "${SC2_EXECUTABLE}" != "${SC2_BATTLENET_EXECUTABLE}" && "${SC2_USE_RUNTIME_DIR_ARGS}" == "1" ]]; then
    mkdir -p "${SC2_TEMP_DIR}"
  fi
  MAP_FILE="$(resolve_map_file "${MAP_FILE}")"
}

verify_build_identity() {
  if [[ "${SMOKE_REQUIRE_BUILD_IDENTITY}" != "1" ]]; then
    return
  fi
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' "${MICROMACHINE_BUILD_IDENTITY_REPORT}" "${MICROMACHINE_DIR}" "${S2CLIENT_DIR}" "${MICROMACHINE_BUILD_DIR}"
import json
import sys
from pathlib import Path

from starcraft_commander.micromachine_build_identity import (
    MicroMachineBuildIdentityConfig,
    build_micromachine_build_identity,
)

report_path = Path(sys.argv[1])
if not report_path.exists():
    raise SystemExit(
        "MicroMachine smoke rejected: missing build identity report. "
        f"Run integrations/micromachine/scripts/build_macos_local.sh first: {report_path}"
    )
try:
    recorded = json.loads(report_path.read_text())
except Exception as exc:  # noqa: BLE001 - shell-facing validation error.
    raise SystemExit(f"MicroMachine smoke rejected: invalid build identity report: {exc}") from exc
current = build_micromachine_build_identity(
    MicroMachineBuildIdentityConfig(
        micromachine_dir=Path(sys.argv[2]),
        s2client_dir=Path(sys.argv[3]),
        micromachine_build_dir=Path(sys.argv[4]),
    )
)
if recorded.get("ok") is not True:
    raise SystemExit(
        "MicroMachine smoke rejected: recorded build identity is not ok: "
        f"{recorded.get('failures')}"
    )
if current.get("ok") is not True:
    raise SystemExit(
        "MicroMachine smoke rejected: current build identity is not ok: "
        f"{current.get('failures')}"
    )
if recorded.get("identity") != current.get("identity"):
    raise SystemExit(
        "MicroMachine smoke rejected: stale build identity. "
        f"recorded={recorded.get('identity')} current={current.get('identity')}. "
        "Re-run integrations/micromachine/scripts/build_macos_local.sh."
    )
PY
}

SC2_EXECUTABLE="${SC2_EXECUTABLE:-$(resolve_sc2_executable)}"
BLACKBOARD_DIR="${BLACKBOARD_DIR:-/private/tmp/voi-mm-smoke}"
MAP_FILE="${MAP_FILE:-AcropolisLE.SC2Map}"
MIN_TELEMETRY_FRAME="${MIN_TELEMETRY_FRAME:-5200}"
AGGRESSIVE_PROFILE_FRAME="${AGGRESSIVE_PROFILE_FRAME:-2600}"
SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-600}"
SMOKE_MAX_ATTEMPTS="${SMOKE_MAX_ATTEMPTS:-3}"
SMOKE_RETRY_SETTLE_SECONDS="${SMOKE_RETRY_SETTLE_SECONDS:-15}"
SMOKE_ATTEMPT_INDEX="${SMOKE_ATTEMPT_INDEX:-}"
BOT_LOG="${BLACKBOARD_DIR}/micromachine.log"
CLASSIFIER_BOT_LOG="${BLACKBOARD_DIR}/micromachine_combined.log"
MICROMACHINE_DATA_DIR="${MICROMACHINE_DATA_DIR:-${MICROMACHINE_DIR}/bin/data}"
RUNTIME_LOG_MARKER="${BLACKBOARD_DIR}/runtime_log_start.marker"
RUNTIME_LOG_BASELINE="${BLACKBOARD_DIR}/runtime_log_baseline.tsv"
SC2_NET_ADDRESS="${SC2_NET_ADDRESS:-127.0.0.1}"
SC2_PORTS=(${SC2_PORTS:-8167 8168})
BOT_PID=""
PREEXISTING_SC2_PORT_PIDS=""
DEFENSIVE_UPDATE_ID="${DEFENSIVE_UPDATE_ID:-smoke-defensive-hold}"
AGGRESSIVE_UPDATE_ID="${AGGRESSIVE_UPDATE_ID:-smoke-aggressive-pressure}"
SMOKE_ACTIVE_STRATEGY_UPDATE_ID="${AGGRESSIVE_UPDATE_ID}"
AGGRESSIVE_PROFILE_PUBLISHED=0
SMOKE_AUTO_AGGRESSIVE_PROFILE="${SMOKE_AUTO_AGGRESSIVE_PROFILE:-1}"
SMOKE_MANUAL_LIVE_MODE="${SMOKE_MANUAL_LIVE_MODE:-0}"
NO_START_UNITS_FRAME="${NO_START_UNITS_FRAME:-1200}"
SMOKE_STRATEGY_PROFILE_NAME="${SMOKE_STRATEGY_PROFILE_NAME:-bio_pressure}"
if [[ -z "${SMOKE_REQUIRE_AGGRESSIVE_COMBAT_EVIDENCE:-}" ]]; then
  if [[ "${SMOKE_STRATEGY_PROFILE_NAME}" == "bio_pressure" || "${SMOKE_STRATEGY_PROFILE_NAME}" == "marine_rush" || "${SMOKE_STRATEGY_PROFILE_NAME}" == "aggressive_pressure" ]]; then
    SMOKE_REQUIRE_AGGRESSIVE_COMBAT_EVIDENCE=1
  else
    SMOKE_REQUIRE_AGGRESSIVE_COMBAT_EVIDENCE=0
  fi
fi

expected_strategy_contract() {
  local profile="$1"
  case "${profile}" in
    marine_rush)
      printf '%s\t%s\t%s\n' "marine_rush" "marine_pressure bio_facility" "Marine Barracks"
      ;;
    bio_pressure|aggressive_pressure)
      printf '%s\t%s\t%s\n' "bio_pressure" "bio_marauder_techlab bio_marauder_support starport_transition medivac_drop_support" "BarracksTechLab Marauder Starport Medivac"
      ;;
    tank_defensive_hold|siege_contain|contain_enemy_natural)
      printf '%s\t%s\t%s\n' "${profile}" "factory_transition factory_techlab siege_tank_composition" "Factory FactoryTechLab SiegeTank"
      ;;
    mech_transition|tech_transition)
      printf '%s\t%s\t%s\n' "mech_transition" "factory_transition factory_techlab hellion_harassment cyclone_mech siege_tank_composition thor_mech" "Factory FactoryTechLab Hellion Cyclone SiegeTank Thor"
      ;;
    drop_harassment|worker_line_harassment)
      printf '%s\t%s\t%s\n' "${profile}" "starport_transition drop_reactor medivac_drop_support factory_transition hellion_harassment reaper_harassment" "Starport StarportReactor Medivac Factory Hellion Reaper"
      ;;
    scouting_map_control)
      printf '%s\t%s\t%s\n' "scouting_map_control" "" ""
      ;;
    expand_macro|economic_expansion)
      printf '%s\t%s\t%s\n' "expand_macro" "expand_macro" "CommandCenter"
      ;;
    anti_air_response)
      printf '%s\t%s\t%s\n' "anti_air_response" "starport_transition anti_air_detection_support anti_air_viking" "Starport EngineeringBay Viking"
      ;;
    *)
      echo "MicroMachine smoke rejected: unsupported SMOKE_STRATEGY_PROFILE_NAME=${profile}" >&2
      exit 2
      ;;
  esac
}

IFS=$'\t' read -r SMOKE_EXPECTED_STRATEGY_DOCTRINE SMOKE_EXPECTED_PRODUCTION_ACTIONS SMOKE_EXPECTED_PRODUCTION_ITEMS < <(expected_strategy_contract "${SMOKE_STRATEGY_PROFILE_NAME}")
if [[ -z "${SMOKE_REQUIRE_SCOUT_MOVEMENT_EVIDENCE:-}" ]]; then
  case "${SMOKE_STRATEGY_PROFILE_NAME}" in
    bio_pressure|marine_rush|aggressive_pressure|drop_harassment|worker_line_harassment|scouting_map_control)
      SMOKE_REQUIRE_SCOUT_MOVEMENT_EVIDENCE=1
      ;;
    *)
      SMOKE_REQUIRE_SCOUT_MOVEMENT_EVIDENCE=0
      ;;
  esac
fi
if [[ -z "${SMOKE_REQUIRE_SCOUT_MODULATION_EVIDENCE:-}" ]]; then
  case "${SMOKE_STRATEGY_PROFILE_NAME}" in
    mech_transition|tech_transition)
      SMOKE_REQUIRE_SCOUT_MODULATION_EVIDENCE=0
      ;;
    *)
      SMOKE_REQUIRE_SCOUT_MODULATION_EVIDENCE=1
      ;;
  esac
fi
if [[ -z "${SMOKE_REQUIRE_SQUAD_MODULATION_EVIDENCE:-}" ]]; then
  case "${SMOKE_STRATEGY_PROFILE_NAME}" in
    expand_macro|economic_expansion)
      SMOKE_REQUIRE_SQUAD_MODULATION_EVIDENCE=0
      ;;
    *)
      SMOKE_REQUIRE_SQUAD_MODULATION_EVIDENCE=1
      ;;
  esac
fi

if [[ -z "${SMOKE_ATTEMPT_INDEX}" && "${SMOKE_MAX_ATTEMPTS}" -gt 1 ]]; then
  mkdir -p "${BLACKBOARD_DIR}"
  for (( attempt = 1; attempt <= SMOKE_MAX_ATTEMPTS; attempt++ )); do
    attempt_dir="${BLACKBOARD_DIR}/attempt-${attempt}"
    echo "Starting MicroMachine smoke attempt ${attempt}/${SMOKE_MAX_ATTEMPTS}: ${attempt_dir}"
    if SMOKE_ATTEMPT_INDEX="${attempt}" SMOKE_MAX_ATTEMPTS=1 BLACKBOARD_DIR="${attempt_dir}" "${BASH_SOURCE[0]}"; then
      if [[ -f "${attempt_dir}/latest_telemetry.json" ]]; then
        cp -p "${attempt_dir}/latest_telemetry.json" "${BLACKBOARD_DIR}/latest_telemetry.json"
      fi
      if [[ -f "${attempt_dir}/telemetry.jsonl" ]]; then
        cp -p "${attempt_dir}/telemetry.jsonl" "${BLACKBOARD_DIR}/telemetry.jsonl"
      fi
      if [[ -f "${attempt_dir}/micromachine_combined.log" ]]; then
        cp -p "${attempt_dir}/micromachine_combined.log" "${BLACKBOARD_DIR}/micromachine_combined.log"
      fi
      python3 - <<'PY' "${BLACKBOARD_DIR}" "${attempt}" "${SMOKE_MAX_ATTEMPTS}"
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
selected_attempt = int(sys.argv[2])
max_attempts = int(sys.argv[3])
attempts = []
for index in range(1, selected_attempt + 1):
    attempt_dir = root / f"attempt-{index}"
    telemetry_path = attempt_dir / "latest_telemetry.json"
    latest_frame = 0
    if telemetry_path.exists():
        try:
            latest_frame = int(json.loads(telemetry_path.read_text()).get("frame") or 0)
        except Exception:
            latest_frame = 0
    attempts.append(
        {
            "attempt": index,
            "status": "passed" if index == selected_attempt else "retryable_startup_failure",
            "latest_frame": latest_frame,
            "dir": str(attempt_dir),
        }
    )
(root / "smoke_attempts.json").write_text(
    json.dumps(
        {
            "status": "passed",
            "ok": True,
            "selected_attempt": selected_attempt,
            "max_attempts": max_attempts,
            "attempts": attempts,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
PY
      echo "MicroMachine smoke passed on attempt ${attempt}/${SMOKE_MAX_ATTEMPTS}; blackboard: ${BLACKBOARD_DIR}"
      exit 0
    fi

    if python3 - <<'PY' "${attempt_dir}" "${NO_START_UNITS_FRAME}"
import json
import sys
from pathlib import Path

attempt_dir = Path(sys.argv[1])
startup_frame_threshold = int(sys.argv[2])
telemetry_path = attempt_dir / "latest_telemetry.json"
latest_frame = 0
if telemetry_path.exists():
    try:
        latest_frame = int(json.loads(telemetry_path.read_text()).get("frame") or 0)
    except Exception:
        latest_frame = 0

log_paths = [
    attempt_dir / "micromachine.log",
    attempt_dir / "micromachine_combined.log",
]
log_text = "\n".join(path.read_text(errors="replace") for path in log_paths if path.exists())
non_retryable_terms = (
    "Failed to place Barracks",
    "Failed to place Refinery",
    "Cancel building TERRAN_SUPPLYDEPOT :",
    "Cancel building TERRAN_BARRACKS :",
    "Cancel building TERRAN_REFINERY :",
    "bootstrap_no_start_units",
)
if any(term in log_text for term in non_retryable_terms):
    raise SystemExit(0)
macro_terms = (
    "build command type=TERRAN_SUPPLYDEPOT",
    "build command type=TERRAN_BARRACKS",
    "build command type=TERRAN_REFINERY",
    "create unit item=Marine result=1",
    "create unit item=Reaper result=1",
)
if latest_frame >= startup_frame_threshold or any(term in log_text for term in macro_terms):
    raise SystemExit(0)
raise SystemExit(1)
PY
    then
      python3 - <<'PY' "${BLACKBOARD_DIR}" "${SMOKE_MAX_ATTEMPTS}" "${attempt}" "non_retryable_failure"
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
max_attempts = int(sys.argv[2])
stopped_at = int(sys.argv[3])
reason = sys.argv[4]
attempts = []
for index in range(1, max_attempts + 1):
    attempt_dir = root / f"attempt-{index}"
    telemetry_path = attempt_dir / "latest_telemetry.json"
    latest_frame = 0
    if telemetry_path.exists():
        try:
            latest_frame = int(json.loads(telemetry_path.read_text()).get("frame") or 0)
        except Exception:
            latest_frame = 0
    status = "not_run" if index > stopped_at else "failed"
    attempts.append({"attempt": index, "status": status, "latest_frame": latest_frame, "dir": str(attempt_dir)})
(root / "smoke_attempts.json").write_text(
    json.dumps(
        {
            "status": "failed",
            "ok": False,
            "max_attempts": max_attempts,
            "stopped_at_attempt": stopped_at,
            "stop_reason": reason,
            "attempts": attempts,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
PY
      echo "MicroMachine smoke stopped after non-retryable attempt ${attempt}; summary: ${BLACKBOARD_DIR}/smoke_attempts.json" >&2
      exit 1
    fi

    if (( attempt < SMOKE_MAX_ATTEMPTS )); then
      echo "MicroMachine smoke retrying after retryable frame-0 startup failure; settling ${SMOKE_RETRY_SETTLE_SECONDS}s before attempt $((attempt + 1))/${SMOKE_MAX_ATTEMPTS}." >&2
      sleep "${SMOKE_RETRY_SETTLE_SECONDS}"
    fi
  done

  python3 - <<'PY' "${BLACKBOARD_DIR}" "${SMOKE_MAX_ATTEMPTS}"
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
max_attempts = int(sys.argv[2])
attempts = []
for index in range(1, max_attempts + 1):
    attempt_dir = root / f"attempt-{index}"
    telemetry_path = attempt_dir / "latest_telemetry.json"
    latest_frame = 0
    if telemetry_path.exists():
        try:
            latest_frame = int(json.loads(telemetry_path.read_text()).get("frame") or 0)
        except Exception:
            latest_frame = 0
    attempts.append({"attempt": index, "status": "failed", "latest_frame": latest_frame, "dir": str(attempt_dir)})
(root / "smoke_attempts.json").write_text(
    json.dumps(
        {"status": "failed", "ok": False, "max_attempts": max_attempts, "attempts": attempts},
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
PY
  echo "MicroMachine smoke failed after ${SMOKE_MAX_ATTEMPTS} attempts; summary: ${BLACKBOARD_DIR}/smoke_attempts.json" >&2
  exit 1
fi

REQUIRED_MACRO_EVIDENCE=(
  "build command type=TERRAN_SUPPLYDEPOT"
  "TERRAN_SUPPLYDEPOT UnderConstruction"
  "build command type=TERRAN_BARRACKS"
  "TERRAN_BARRACKS UnderConstruction"
  "build command type=TERRAN_REFINERY"
)

POST_BARRACKS_UNIT_EVIDENCE=(
  "create unit item=Marine result=1"
  "create unit item=Reaper result=1"
)

FORBIDDEN_MACRO_FAILURES=(
  "Failed to place Barracks"
  "Failed to place Refinery"
  "Cancel building TERRAN_SUPPLYDEPOT :"
  "Cancel building TERRAN_BARRACKS :"
  "Cancel building TERRAN_REFINERY :"
)

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

capture_preexisting_sc2_port_pids() {
  local port
  local pids=()
  for port in "${SC2_PORTS[@]}"; do
    while IFS= read -r pid; do
      [[ -n "${pid}" ]] || continue
      pids+=("${pid}")
    done < <(sc2_port_pids "${port}")
  done
  PREEXISTING_SC2_PORT_PIDS="${pids[*]:-}"
}

cleanup_runtime() {
  if [[ -n "${BOT_PID}" ]] && kill -0 "${BOT_PID}" 2>/dev/null; then
    kill "${BOT_PID}" 2>/dev/null || true
    wait "${BOT_PID}" 2>/dev/null || true
  fi

  local port
  for port in "${SC2_PORTS[@]}"; do
    while IFS= read -r pid; do
      [[ -n "${pid}" ]] || continue
      if [[ " ${PREEXISTING_SC2_PORT_PIDS} " == *" ${pid} "* ]]; then
        continue
      fi
      kill "${pid}" 2>/dev/null || true
    done < <(sc2_port_pids "${port}")
  done
}

trap cleanup_runtime EXIT

has_log_term() {
  local term="$1"
  local log_file
  while IFS= read -r log_file; do
    [[ -n "${log_file}" && -f "${log_file}" ]] || continue
    stream_current_run_log "${log_file}" | grep -Fq "${term}" && return 0
  done < <(candidate_bot_logs)
  return 1
}

latest_runtime_log() {
  [[ -d "${MICROMACHINE_DATA_DIR}" && -f "${RUNTIME_LOG_MARKER}" ]] || return 0
  find "${MICROMACHINE_DATA_DIR}" -maxdepth 1 -type f -name '*.log' -newer "${RUNTIME_LOG_MARKER}" -print 2>/dev/null | sort | tail -n 1
}

file_size_bytes() {
  local file="$1"
  stat -f '%z' "${file}" 2>/dev/null || wc -c < "${file}"
}

record_runtime_log_baseline() {
  : > "${RUNTIME_LOG_BASELINE}"
  [[ -d "${MICROMACHINE_DATA_DIR}" ]] || return 0
  local log_file
  while IFS= read -r log_file; do
    [[ -n "${log_file}" && -f "${log_file}" ]] || continue
    printf '%s\t%s\n' "${log_file}" "$(file_size_bytes "${log_file}")" >> "${RUNTIME_LOG_BASELINE}"
  done < <(find "${MICROMACHINE_DATA_DIR}" -maxdepth 1 -type f -name '*.log' -print 2>/dev/null | sort)
}

runtime_log_start_offset() {
  local log_file="$1"
  [[ -f "${RUNTIME_LOG_BASELINE}" ]] || {
    printf '0\n'
    return 0
  }
  awk -v target="${log_file}" -F '\t' '$1 == target { found = 1; print $2 } END { if (!found) print 0 }' "${RUNTIME_LOG_BASELINE}"
}

stream_current_run_log() {
  local log_file="$1"
  if [[ "${log_file}" == "${BOT_LOG}" ]]; then
    cat "${log_file}"
    return 0
  fi
  local offset
  offset="$(runtime_log_start_offset "${log_file}")"
  if [[ "${offset}" =~ ^[0-9]+$ && "${offset}" -gt 0 ]]; then
    tail -c +"$((offset + 1))" "${log_file}"
  else
    cat "${log_file}"
  fi
}

candidate_bot_logs() {
  [[ -f "${BOT_LOG}" ]] && printf '%s\n' "${BOT_LOG}"
  local runtime_log
  runtime_log="$(latest_runtime_log || true)"
  if [[ -n "${runtime_log}" && -f "${runtime_log}" ]]; then
    printf '%s\n' "${runtime_log}"
  fi
}

print_bot_logs() {
  rm -f "${CLASSIFIER_BOT_LOG}"
  local log_file
  while IFS= read -r log_file; do
    [[ -n "${log_file}" && -f "${log_file}" ]] || continue
    {
      printf '%s\n' "--- ${log_file} ---"
      stream_current_run_log "${log_file}"
    } >> "${CLASSIFIER_BOT_LOG}"
    echo "--- ${log_file} ---" >&2
    stream_current_run_log "${log_file}" | tail -200 >&2 || true
  done < <(candidate_bot_logs)
  [[ -f "${CLASSIFIER_BOT_LOG}" ]] || touch "${CLASSIFIER_BOT_LOG}"
}

has_forbidden_macro_failure() {
  local term
  for term in "${FORBIDDEN_MACRO_FAILURES[@]}"; do
    if has_log_term "${term}"; then
      echo "MicroMachine macro smoke saw forbidden failure: ${term}" >&2
      return 0
    fi
  done
  return 1
}

has_required_macro_evidence() {
  local term
  for term in "${REQUIRED_MACRO_EVIDENCE[@]}"; do
    has_log_term "${term}" || return 1
  done
  has_post_barracks_unit_evidence || return 1
  has_positive_gas_income || return 1
  has_positive_mineral_income || return 1
  return 0
}

has_post_barracks_unit_evidence() {
  local term
  for term in "${POST_BARRACKS_UNIT_EVIDENCE[@]}"; do
    has_log_term "${term}" && return 0
  done
  return 1
}

has_positive_gas_income() {
  local log_file
  while IFS= read -r log_file; do
    [[ -n "${log_file}" && -f "${log_file}" ]] || continue
    awk '
      /Gas income:/ {
        for (i = 1; i <= NF; i++) {
          if ($i ~ /^[0-9]+$/ && $i > 0) {
            found = 1
          }
        }
      }
      END { exit(found ? 0 : 1) }
    ' < <(stream_current_run_log "${log_file}") && return 0
  done < <(candidate_bot_logs)
  return 1
}

has_positive_mineral_income() {
  local log_file
  while IFS= read -r log_file; do
    [[ -n "${log_file}" && -f "${log_file}" ]] || continue
    awk '
      /Mineral income:/ {
        for (i = 1; i <= NF; i++) {
          if ($i ~ /^[0-9]+$/ && $i > 0) {
            found = 1
          }
        }
      }
      END { exit(found ? 0 : 1) }
    ' < <(stream_current_run_log "${log_file}") && return 0
  done < <(candidate_bot_logs)
  return 1
}

print_missing_macro_evidence() {
  local term
  for term in "${REQUIRED_MACRO_EVIDENCE[@]}"; do
    if ! has_log_term "${term}"; then
      echo "missing macro evidence: ${term}" >&2
    fi
  done
  if ! has_post_barracks_unit_evidence; then
    echo "missing post-Barracks unit evidence: ${POST_BARRACKS_UNIT_EVIDENCE[*]}" >&2
  fi
  if ! has_positive_gas_income; then
    echo "missing positive gas income after Refinery completion" >&2
  fi
  if ! has_positive_mineral_income; then
    echo "missing positive mineral income after macro opening" >&2
  fi
}

publish_profile() {
  local profile="$1"
  local update_id="$2"
  local frame="$3"
  # MicroMachineFilesystemBlackboard writes latest_modulation.kv for the C++ hook.
  # Historical smoke contracts used build_tank_defensive_hold_profile and
  # build_bio_pressure_profile directly; the strategy matrix now routes through
  # build_micromachine_strategy_profile so every supported play style shares the
  # same safe DSL compiler path.
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' "${BLACKBOARD_DIR}" "${profile}" "${update_id}" "${frame}"
import sys

from starcraft_commander.micromachine_runtime import (
    MicroMachineFilesystemBlackboard,
    build_micromachine_strategy_profile,
)

directory, profile_name, update_id, frame_text = sys.argv[1:5]
backend = MicroMachineFilesystemBlackboard(directory)
if profile_name == "aggressive_pressure":
    profile_name = "bio_pressure"
vector = build_micromachine_strategy_profile(profile_name)
backend.publish_vector(vector, current_frame=int(frame_text), update_id=update_id)
PY
}

smoke_strategy_update_id() {
  local profile="$1"
  local frame="$2"
  local safe_profile
  safe_profile="${profile//[^A-Za-z0-9_.-]/-}"
  printf 'smoke-%s-%s\n' "${safe_profile}" "${frame}"
}

telemetry_frame() {
  [[ -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]] || return 1
  python3 - <<'PY' "${BLACKBOARD_DIR}/latest_telemetry.json"
import json
import sys
from pathlib import Path

try:
    print(int(json.loads(Path(sys.argv[1]).read_text()).get("frame", 0)))
except Exception:
    raise SystemExit(1)
PY
}

has_no_start_units_bootstrap_blocker() {
  [[ -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]] || return 1
  python3 - <<'PY' "${BLACKBOARD_DIR}/latest_telemetry.json" "${NO_START_UNITS_FRAME}"
import json
import sys
from pathlib import Path

threshold = int(sys.argv[2])
try:
    payload = json.loads(Path(sys.argv[1]).read_text())
except Exception:
    raise SystemExit(1)
frame = int(payload.get("frame", 0) or 0)
ccbot = payload.get("managers", {}).get("CCBot", {})
if (
    frame >= threshold
    and ccbot.get("bootstrap_status") == "waiting_for_initial_observation"
    and int(ccbot.get("player_id", 0) or 0) > 0
    and int(ccbot.get("self_count", 0) or 0) == 0
    and int(ccbot.get("resource_depot_count", 0) or 0) == 0
    and int(ccbot.get("game_info_width", 0) or 0) > 0
    and int(ccbot.get("game_info_height", 0) or 0) > 0
):
    raise SystemExit(0)
raise SystemExit(1)
PY
}

has_live_hold_preflight_evidence() {
  [[ -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]] || return 1
  python3 - <<'PY' "${BLACKBOARD_DIR}/latest_telemetry.json"
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text())
except Exception:
    raise SystemExit(1)
if payload.get("protocol_version") != "voi-mm-bridge/v1":
    raise SystemExit(1)
managers = payload.get("managers", {})
commander = managers.get("GameCommander", {})
workers = managers.get("WorkerManager", {})
if commander.get("policy_active") is not True:
    raise SystemExit(1)
if workers.get("active") is not True:
    raise SystemExit(1)
if workers.get("repeat_order_guard_active") is not True:
    raise SystemExit(1)
if int(workers.get("repeat_order_guard_frames", 0)) != 32:
    raise SystemExit(1)
consumed_axes = {
    axis.strip()
    for axis in str(workers.get("consumed_axes", "")).split(",")
    if axis.strip()
}
if "workers.repeat_order_guard_frames" not in consumed_axes:
    raise SystemExit(1)
if "repeat_order_suppressed_count" not in workers:
    raise SystemExit(1)
if int(workers.get("repeat_order_suppressed_count", 0)) != 0:
    raise SystemExit(1)
if "self_position_command_block_count" not in workers:
    raise SystemExit(1)
if "root_cause_status" not in workers:
    raise SystemExit(1)
if "root_cause_reason" not in workers:
    raise SystemExit(1)
if int(workers.get("self_position_command_block_count", 0)) != 0:
    raise SystemExit(1)
if workers.get("root_cause_status") == "self_position_move_blocked":
    raise SystemExit(1)
if (
    workers.get("root_cause_status") == "duplicate_command_safety_blocked"
    and str(workers.get("root_cause_reason", "")).startswith("scout_")
):
    raise SystemExit(1)
raise SystemExit(0)
PY
}

print_missing_live_hold_preflight() {
  echo "MicroMachine live hold preflight did not pass: expected worker guard frame=32, zero repeat-order suppressions, zero self-position blocks, and no ScoutManager duplicate move safety blocks." >&2
}

print_no_start_units_bootstrap_blocker() {
  echo "MicroMachine bootstrap_no_start_units: SC2 API joined and map info loaded, but the participant has no starting self units or resource depot." >&2
  cat "${BLACKBOARD_DIR}/latest_telemetry.json" >&2 || true
}

prepare_launch_contract
verify_build_identity
SC2_RUNTIME_ROOT="$(prepare_sc2_runtime_root)"
if [[ "${SC2_EXECUTABLE}" == "${SC2_BATTLENET_EXECUTABLE}" && -z "${VOI_SC2_EXTRA_ARGS:-}" ]]; then
  VOI_SC2_EXTRA_ARGS="--game=${SC2_BATTLENET_GAME} --gamepath=${SC2_RUNTIME_ROOT}/"
elif [[ -z "${VOI_SC2_EXTRA_ARGS:-}" && "${SC2_USE_RUNTIME_DIR_ARGS}" == "1" ]]; then
  VOI_SC2_EXTRA_ARGS="-dataDir ${SC2_RUNTIME_ROOT} -tempDir ${SC2_TEMP_DIR}"
fi

mkdir -p "${BLACKBOARD_DIR}"
rm -f "${BLACKBOARD_DIR}/latest_telemetry.json" "${BLACKBOARD_DIR}/telemetry.jsonl" "${BOT_LOG}" "${CLASSIFIER_BOT_LOG}" "${RUNTIME_LOG_BASELINE}"
touch "${RUNTIME_LOG_MARKER}"
record_runtime_log_baseline
if [[ "${SMOKE_MANUAL_LIVE_MODE}" == "1" ]]; then
  publish_profile "defensive_hold" "${DEFENSIVE_UPDATE_ID}" "0"
else
  SMOKE_ACTIVE_STRATEGY_UPDATE_ID="$(smoke_strategy_update_id "${SMOKE_STRATEGY_PROFILE_NAME}" "0")"
  AGGRESSIVE_UPDATE_ID="${SMOKE_ACTIVE_STRATEGY_UPDATE_ID}"
  DEFENSIVE_UPDATE_ID="${SMOKE_ACTIVE_STRATEGY_UPDATE_ID}"
  publish_profile "${SMOKE_STRATEGY_PROFILE_NAME}" "${SMOKE_ACTIVE_STRATEGY_UPDATE_ID}" "0"
  AGGRESSIVE_PROFILE_PUBLISHED=1
fi
clean_sc2_ports_before_launch
settle_after_sc2_port_cleanup
capture_preexisting_sc2_port_pids

python3 - <<'PY' "${MICROMACHINE_DIR}/bin/BotConfig.txt" "${MAP_FILE}"
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
map_file = sys.argv[2]
profile = os.environ.get("SMOKE_STRATEGY_PROFILE_NAME", "bio_pressure")
config = json.loads(path.read_text())
config["SC2API"]["PlayAsHuman"] = False
config["SC2API"]["ForceStepMode"] = bool(int(os.environ.get("SMOKE_FORCE_STEP_MODE", "0")))
config["SC2API"]["MapFile"] = map_file
config["SC2API"]["PlayVsItSelf"] = bool(int(os.environ.get("SMOKE_PLAY_VS_SELF", "0")))
config["SC2API"]["EnemyDifficulty"] = int(os.environ.get("SMOKE_ENEMY_DIFFICULTY", "1"))
config["SC2API"]["EnemyRace"] = "Zerg"
config["SC2API"]["StepSize"] = 1
config["Macro"]["SelectStartingBuildBasedOnHistory"] = False
config["Macro"]["PrintGreetingMessage"] = False
terran_strategies = config["SC2API Strategy"]["Strategies"]
strategy_by_profile = {
    "marine_rush": "Terran_MarineRush",
    "bio_pressure": "Terran_MarineRush",
    "aggressive_pressure": "Terran_MarineRush",
    "drop_harassment": "Terran_RefineryOpener",
    "worker_line_harassment": "Terran_ReaperHarass",
    "scouting_map_control": "Terran_ReaperHarass",
    "tank_defensive_hold": "Terran_Hellion",
    "siege_contain": "Terran_Hellion",
    "contain_enemy_natural": "Terran_Hellion",
    "mech_transition": "Terran_Hellion",
    "tech_transition": "Terran_Hellion",
    "anti_air_response": "Terran_RefineryOpener",
    "expand_macro": "Terran_FastExpand",
    "economic_expansion": "Terran_FastExpand",
}
selected_strategy = strategy_by_profile.get(profile, "Terran_MarineRush")
if selected_strategy not in terran_strategies:
    raise SystemExit(f"Unsupported Terran strategy {selected_strategy!r} for smoke profile {profile!r}")
config["SC2API Strategy"]["Terran"] = selected_strategy
if selected_strategy == "Terran_MarineRush":
    marine_rush = terran_strategies["Terran_MarineRush"]["OpeningBuildOrder"]
    if "Marine" not in marine_rush:
        first_barracks = marine_rush.index("Barracks")
        marine_rush.insert(first_barracks + 1, "Marine")
path.write_text(json.dumps(config, indent=4) + "\n")
PY

(
  cd "${MICROMACHINE_DIR}/bin"
  VOI_MICROMACHINE_BLACKBOARD_DIR="${BLACKBOARD_DIR}" \
    VOI_SC2_EXTRA_ARGS="${VOI_SC2_EXTRA_ARGS:-}" \
    VOI_SC2_CREATEGAME_MAP_DATA="${VOI_SC2_CREATEGAME_MAP_DATA}" \
    VOI_SC2_BOOTSTRAP_SELF_UNITS="${VOI_SC2_BOOTSTRAP_SELF_UNITS:-${VOI_SC2_CONNECT_PORT:+1}}" \
    "${MICROMACHINE_BUILD_DIR}/bin/MicroMachine" \
    -e "${SC2_EXECUTABLE}" \
    -t "${SC2_ATTACH_TIMEOUT_MS}"
) >"${BOT_LOG}" 2>&1 &
BOT_PID=$!

deadline=$((SECONDS + SMOKE_TIMEOUT_SECONDS))
while kill -0 "${BOT_PID}" 2>/dev/null; do
  if has_forbidden_macro_failure; then
    cleanup_runtime
    print_bot_logs
    exit 1
  fi

  if [[ -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]]; then
    current_telemetry_frame="$(telemetry_frame || true)"
    if has_no_start_units_bootstrap_blocker; then
      cleanup_runtime
      print_no_start_units_bootstrap_blocker
      print_bot_logs
      exit 1
    fi
    if [[ "${SMOKE_MANUAL_LIVE_MODE}" == "1" && -n "${current_telemetry_frame}" && "${current_telemetry_frame}" -ge "${NO_START_UNITS_FRAME}" ]] && has_required_macro_evidence && has_live_hold_preflight_evidence; then
      print_bot_logs >/dev/null 2>&1
      echo "MicroMachine manual live hold preflight passed; keeping runtime alive for manual DSL commands."
      echo "MicroMachine manual live hold active; automatic aggressive smoke profile is disabled."
      while kill -0 "${BOT_PID}" 2>/dev/null; do
        sleep 2
      done
      wait "${BOT_PID}" 2>/dev/null || true
      exit 0
    fi
    if [[ "${SMOKE_AUTO_AGGRESSIVE_PROFILE}" == "1" && "${AGGRESSIVE_PROFILE_PUBLISHED}" -eq 0 && -n "${current_telemetry_frame}" && "${current_telemetry_frame}" -ge "${AGGRESSIVE_PROFILE_FRAME}" ]] && has_required_macro_evidence; then
      if [[ "${SMOKE_STRATEGY_PROFILE_NAME}" == "bio_pressure" || "${SMOKE_STRATEGY_PROFILE_NAME}" == "marine_rush" || "${SMOKE_STRATEGY_PROFILE_NAME}" == "aggressive_pressure" ]]; then
        SMOKE_ACTIVE_STRATEGY_UPDATE_ID="${AGGRESSIVE_UPDATE_ID}"
      else
        SMOKE_ACTIVE_STRATEGY_UPDATE_ID="$(smoke_strategy_update_id "${SMOKE_STRATEGY_PROFILE_NAME}" "${current_telemetry_frame}")"
        AGGRESSIVE_UPDATE_ID="${SMOKE_ACTIVE_STRATEGY_UPDATE_ID}"
      fi
      publish_profile "${SMOKE_STRATEGY_PROFILE_NAME}" "${SMOKE_ACTIVE_STRATEGY_UPDATE_ID}" "${current_telemetry_frame}"
      AGGRESSIVE_PROFILE_PUBLISHED=1
    fi

    if python3 - "${BLACKBOARD_DIR}/latest_telemetry.json" "${MIN_TELEMETRY_FRAME}" "${AGGRESSIVE_UPDATE_ID}" <<'PY'
import json
import sys
from pathlib import Path

min_frame = int(sys.argv[2])
aggressive_update_id = sys.argv[3]
try:
    payload = json.loads(Path(sys.argv[1]).read_text())
except json.JSONDecodeError:
    raise SystemExit(1)
if payload.get("frame", 0) >= min_frame:
    commander = payload.get("managers", {}).get("GameCommander", {})
    if commander.get("policy_active") is not True:
        raise SystemExit(1)
    if commander.get("update_id") != aggressive_update_id:
        raise SystemExit(1)
    raise SystemExit(0)
raise SystemExit(1)
PY
    then
      if has_required_macro_evidence; then
        if [[ "${SMOKE_KEEP_RUNNING_AFTER_PASS:-0}" != "1" ]]; then
          cleanup_runtime
        fi
        break
      fi
    fi
  fi

  if (( SECONDS >= deadline )); then
    cleanup_runtime
    echo "MicroMachine smoke timed out after ${SMOKE_TIMEOUT_SECONDS}s" >&2
    if [[ "${SMOKE_MANUAL_LIVE_MODE}" == "1" ]] && has_required_macro_evidence; then
      print_missing_live_hold_preflight
    fi
    print_missing_macro_evidence
    print_bot_logs
    exit 1
  fi
  sleep 2
done

if has_forbidden_macro_failure; then
  print_bot_logs
  exit 1
fi

if [[ ! -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]]; then
  wait "${BOT_PID}" 2>/dev/null || true
  echo "MicroMachine did not emit telemetry" >&2
  print_bot_logs
  exit 1
fi

if ! has_required_macro_evidence; then
  echo "MicroMachine reached SC2 API but did not execute the required macro opening" >&2
  if has_no_start_units_bootstrap_blocker; then
    print_no_start_units_bootstrap_blocker
  fi
  print_missing_macro_evidence
  print_bot_logs
  exit 1
fi

print_bot_logs >/dev/null 2>&1

python3 - <<'PY' "${BLACKBOARD_DIR}/latest_telemetry.json" "${MIN_TELEMETRY_FRAME}" "${BOT_LOG}" "${DEFENSIVE_UPDATE_ID}" "${AGGRESSIVE_UPDATE_ID}" "${SMOKE_EXPECTED_STRATEGY_DOCTRINE}" "${SMOKE_EXPECTED_PRODUCTION_ACTIONS}" "${SMOKE_EXPECTED_PRODUCTION_ITEMS}" "${SMOKE_REQUIRE_AGGRESSIVE_COMBAT_EVIDENCE}" "${SMOKE_REQUIRE_SCOUT_MOVEMENT_EVIDENCE}" "${SMOKE_REQUIRE_SCOUT_MODULATION_EVIDENCE}" "${SMOKE_REQUIRE_SQUAD_MODULATION_EVIDENCE}"
import json
import sys
import time
from pathlib import Path

telemetry = Path(sys.argv[1])
min_frame = int(sys.argv[2])
bot_log = Path(sys.argv[3])
defensive_update_id = sys.argv[4]
aggressive_update_id = sys.argv[5]
expected_strategy_doctrine = sys.argv[6]
expected_production_actions = {item for item in sys.argv[7].split() if item}
expected_production_items = {item for item in sys.argv[8].split() if item}
require_aggressive_combat = sys.argv[9] == "1"
require_scout_movement = sys.argv[10] == "1"
require_scout_modulation = sys.argv[11] == "1"
require_squad_modulation = sys.argv[12] == "1"
pressure_override_contract = {
    "marine_rush": {"force_when_threshold_met"},
    "bio_pressure": {"earlier_if_safe"},
    "aggressive_pressure": {"earlier_if_safe"},
}
pressure_requires_rally = expected_strategy_doctrine in {"bio_pressure", "aggressive_pressure"}
pressure_requires_contain = expected_strategy_doctrine in {"bio_pressure", "aggressive_pressure"}
pressure_requires_target_keys = {
    "bio_pressure": ("target_worker_line_bias", "target_townhall_bias", "target_army_bias"),
    "aggressive_pressure": ("target_worker_line_bias", "target_townhall_bias", "target_army_bias"),
    "marine_rush": ("target_worker_line_bias", "target_army_bias"),
}

def load_json_retry(path):
    last_error = None
    for _ in range(8):
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            last_error = exc
            time.sleep(0.05)
    raise SystemExit(f"could not read stable JSON from {path}: {last_error}")

payload = load_json_retry(telemetry)
archive = telemetry.with_name("telemetry.jsonl")

def iter_telemetry_entries():
    yield payload
    if archive.exists():
        for line in archive.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                yield entry

def modulation_issued_at_frame(update_id):
    candidates = []
    latest_modulation = telemetry.with_name("latest_modulation.json")
    if latest_modulation.exists():
        try:
            entry = json.loads(latest_modulation.read_text())
        except json.JSONDecodeError:
            entry = {}
        if isinstance(entry, dict) and entry.get("update_id") == update_id:
            candidates.append(entry)
    modulation_archive = telemetry.with_name("modulation_updates.jsonl")
    if modulation_archive.exists():
        for line in modulation_archive.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("update_id") == update_id:
                candidates.append(entry)
    if not candidates:
        return None
    return max(int(entry.get("issued_at_frame", 0) or 0) for entry in candidates)

aggressive_issued_at_frame = modulation_issued_at_frame(aggressive_update_id)
if require_aggressive_combat and aggressive_issued_at_frame is None:
    raise SystemExit(
        "missing aggressive modulation issued_at_frame evidence: "
        f"update_id={aggressive_update_id}, telemetry={telemetry}"
    )
aggressive_issued_at_frame = int(aggressive_issued_at_frame or 0)

def profile_main_attack_command_seen():
    best = {}
    for entry in iter_telemetry_entries():
        managers_entry = entry.get("managers", {})
        if not isinstance(managers_entry, dict):
            continue
        commander_entry = managers_entry.get("GameCommander", {})
        combat_entry = managers_entry.get("CombatCommander", {})
        tactical_entry = managers_entry.get("TacticalTask", {})
        if not isinstance(commander_entry, dict) or not isinstance(combat_entry, dict):
            continue
        if commander_entry.get("update_id") != aggressive_update_id:
            continue
        command = str(combat_entry.get("main_attack_last_issued_action", "") or "")
        frame = int(entry.get("frame", 0) or 0)
        command_frame = int(combat_entry.get("main_attack_last_action_frame", 0) or 0)
        command_count = int(combat_entry.get("main_attack_actual_command_issued_count", 0) or 0)
        status = str(combat_entry.get("main_attack_order_status", "") or "")
        best = {
            "frame": frame,
            "main_attack_actual_command_issued_count": command_count,
            "main_attack_last_action_frame": command_frame,
            "main_attack_last_issued_action": command,
            "main_attack_order_status": status,
            "main_attack_scope_threshold_met": combat_entry.get("main_attack_scope_threshold_met"),
            "main_attack_simulation_won": combat_entry.get("main_attack_simulation_won"),
            "aggressive_issued_at_frame": aggressive_issued_at_frame,
        }
        issued_main_attack = "squad=MainAttack" in command
        if (
            issued_main_attack
            and command_count > 0
            and status == "Attack"
            and combat_entry.get("main_attack_scope_threshold_met") is True
            and combat_entry.get("main_attack_simulation_won") is True
            and command_frame > 0
            and command_frame >= aggressive_issued_at_frame
        ):
            return True, best
    return False, best

if payload.get("protocol_version") != "voi-mm-bridge/v1":
    raise SystemExit(f"unexpected telemetry protocol in {telemetry}: {payload!r}")
if payload.get("frame", 0) < min_frame:
    raise SystemExit(
        f"telemetry frame {payload.get('frame')} did not reach required frame {min_frame}; "
        f"bot log: {bot_log}"
    )
commander = payload.get("managers", {}).get("GameCommander")
if not commander:
    raise SystemExit(
        "MicroMachine reached SC2 API but did not initialize GameCommander; "
        f"latest managers={sorted(payload.get('managers', {}).keys())}, "
        f"last_failure={payload.get('last_failure')!r}, telemetry={telemetry}, bot log={bot_log}"
    )
if commander.get("policy_active") is not True:
    raise SystemExit(f"GameCommander policy is not active: {commander!r}")
if commander.get("update_id") != aggressive_update_id:
    raise SystemExit(f"unexpected GameCommander update id: {commander!r}")
managers = payload.get("managers", {})
combat = managers.get("CombatCommander")
if not combat or combat.get("active") is not True:
    raise SystemExit(f"missing CombatCommander activity evidence: {managers!r}")
if combat.get("bounded_intervention") is not True:
    raise SystemExit(f"missing aggressive CombatCommander modulation evidence: {combat!r}")
combat_consumed_axes = {
    axis.strip()
    for axis in str(combat.get("consumed_axes", "")).split(",")
    if axis.strip()
}
for axis in (
    "combat.attack_timing_bias",
    "combat.commitment_level",
    "combat.attack_condition_override",
):
    if axis not in combat_consumed_axes:
        raise SystemExit(f"missing deep CombatCommander consumed axis {axis}: {combat!r}")
if require_aggressive_combat:
    if combat.get("aggression", 0) <= 0:
        raise SystemExit(f"missing positive aggression evidence: {combat!r}")
    main_attack_command_count = int(
        combat.get("main_attack_actual_command_issued_count", 0) or 0
    )
    main_attack_command = str(combat.get("main_attack_last_issued_action", "") or "")
    main_attack_command_frame = int(combat.get("main_attack_last_action_frame", 0) or 0)
    if main_attack_command_count <= 0:
        raise SystemExit(f"missing actual CombatCommander command evidence: {combat!r}")
    if main_attack_command in ("", "none") or "squad=MainAttack" not in main_attack_command:
        raise SystemExit(f"missing MainAttack CombatCommander action evidence: {combat!r}")
    if main_attack_command_frame <= 0:
        raise SystemExit(f"missing issued-only CombatCommander command frame evidence: {combat!r}")
    if main_attack_command_frame < aggressive_issued_at_frame:
        raise SystemExit(
            "MainAttack command evidence predates the aggressive modulation update: "
            f"issued_at_frame={aggressive_issued_at_frame}, combat={combat!r}"
        )
    for axis in (
        "combat.retreat_patience_bias",
        "combat.rally_before_attack_bias",
        "scope.min_units",
    ):
        if axis not in combat_consumed_axes:
            raise SystemExit(f"missing deep CombatCommander consumed axis {axis}: {combat!r}")
    if float(combat.get("attack_timing_bias", 0)) <= 0:
        raise SystemExit(f"missing attack timing bias evidence: {combat!r}")
    if float(combat.get("commitment_level", 0)) <= 0:
        raise SystemExit(f"missing commitment level evidence: {combat!r}")
    allowed_overrides = pressure_override_contract.get(
        expected_strategy_doctrine,
        {"earlier_if_safe", "force_when_threshold_met"},
    )
    if combat.get("attack_condition_override") not in allowed_overrides:
        raise SystemExit(
            "missing profile-specific attack condition override evidence: "
            f"expected one of {sorted(allowed_overrides)}, combat={combat!r}"
        )
    if combat.get("main_attack_order_status") != "Attack":
        raise SystemExit(f"missing aggressive attack order evidence: {combat!r}")
    if combat.get("main_attack_scope_threshold_met") is not True:
        raise SystemExit(f"missing attack scope threshold evidence: {combat!r}")
    if combat.get("main_attack_simulation_won") is not True:
        raise SystemExit(f"missing attack simulation safety evidence: {combat!r}")
    main_attack_seen, main_attack_evidence = profile_main_attack_command_seen()
    if not main_attack_seen:
        raise SystemExit(
            "missing archived MainAttack command evidence for aggressive profile: "
            f"best={main_attack_evidence!r}, latest={combat!r}"
        )
    if int(combat.get("main_attack_unit_count", 0)) < int(combat.get("main_attack_scope_min_units", 1)):
        raise SystemExit(f"attack order did not satisfy scope units: {combat!r}")
    if float(combat.get("retreat_patience_bias", 0)) <= 0:
        raise SystemExit(f"missing retreat patience evidence: {combat!r}")
    if pressure_requires_rally and float(combat.get("rally_before_attack_bias", 0)) <= 0:
        raise SystemExit(f"missing rally-before-attack evidence: {combat!r}")
squad = managers.get("Squad")
if not squad or squad.get("active") is not True:
    raise SystemExit(f"missing Squad activity evidence: {managers!r}")
if require_squad_modulation and squad.get("bounded_intervention") is not True:
    raise SystemExit(f"missing Squad bounded intervention evidence: {squad!r}")
squad_consumed_axes = {
    axis.strip()
    for axis in str(squad.get("consumed_axes", "")).split(",")
    if axis.strip()
}
if require_squad_modulation or squad.get("bounded_intervention") is True:
    for axis in (
        "squad.contain_bias",
        "squad.reinforce_bias",
        "scope.location_intent",
        "scope.min_units",
        "combat.target_priority_biases.*",
    ):
        if axis not in squad_consumed_axes:
            raise SystemExit(f"missing deep Squad consumed axis {axis}: {squad!r}")
if require_aggressive_combat:
    if pressure_requires_contain and float(squad.get("contain_bias", 0)) <= 0:
        raise SystemExit(f"missing contain bias evidence: {squad!r}")
    if float(squad.get("reinforce_bias", 0)) <= 0:
        raise SystemExit(f"missing reinforce bias evidence: {squad!r}")
    if squad.get("scope_location_intent") != "enemy_natural":
        raise SystemExit(f"missing semantic scope location evidence: {squad!r}")
    if int(squad.get("scope_min_units", 0)) < 1:
        raise SystemExit(f"missing semantic scope unit threshold evidence: {squad!r}")
    for key in pressure_requires_target_keys.get(expected_strategy_doctrine, ("target_worker_line_bias", "target_townhall_bias", "target_army_bias")):
        if float(squad.get(key, 0)) <= 0:
            raise SystemExit(f"missing target priority evidence {key}: {squad!r}")
production = managers.get("ProductionManager")
if not production or production.get("active") is not True:
    raise SystemExit(f"missing ProductionManager activity evidence: {managers!r}")
latest_supply_block_frame = int(production.get("last_supply_block_frame", 0) or 0)
latest_supply_recovery_frame = int(production.get("last_supply_recovery_frame", 0) or 0)
latest_supply_provider_command_frame = int(production.get("last_supply_provider_command_frame", 0) or 0)
supply_provider_under_construction_count = int(production.get("supply_provider_under_construction_count", 0) or 0)
if (
    payload.get("frame", 0) >= min_frame
    and latest_supply_block_frame > 0
    and latest_supply_provider_command_frame < latest_supply_block_frame
    and supply_provider_under_construction_count <= 0
):
    if latest_supply_recovery_frame >= latest_supply_block_frame:
        raise SystemExit(
            "ProductionManager reached target frame with pending supply recovery but "
            "no subsequent SupplyDepot command or under-construction evidence: "
            f"{production!r}"
        )
    raise SystemExit(
        "ProductionManager reached target frame with unresolved supply block and no "
        "SupplyDepot recovery evidence: "
        f"{production!r}"
    )
production_contract_required = bool(expected_production_actions or expected_production_items)
if production_contract_required:
    if production.get("bounded_intervention") is not True:
        raise SystemExit(f"missing ProductionManager bounded intervention evidence: {production!r}")
    if production.get("policy_update_id") != aggressive_update_id:
        raise SystemExit(f"ProductionManager did not consume latest aggressive update: {production!r}")
    if production.get("strategy_doctrine") != expected_strategy_doctrine:
        raise SystemExit(f"ProductionManager did not consume expected strategy doctrine {expected_strategy_doctrine}: {production!r}")
    if production.get("last_doctrine") != expected_strategy_doctrine:
        raise SystemExit(f"ProductionManager latest doctrine mismatch: {production!r}")
    if production.get("last_doctrine_update_id") != aggressive_update_id:
        raise SystemExit(f"ProductionManager doctrine action came from stale update: {production!r}")
    if production.get("last_doctrine_fresh") is not True:
        raise SystemExit(f"ProductionManager doctrine action is not fresh: {production!r}")
    if str(production.get("last_doctrine_action", "") or "") in ("", "none"):
        raise SystemExit(f"ProductionManager did not queue a doctrine action: {production!r}")
    if str(production.get("last_doctrine_queue_item", "") or "") in ("", "none"):
        raise SystemExit(f"ProductionManager doctrine action did not queue an item: {production!r}")
allowed_doctrine_evidence = {"queued", "queued_existing", "command_issued"}
if production_contract_required and str(production.get("last_doctrine_evidence", "") or "") not in allowed_doctrine_evidence:
    raise SystemExit(
        "ProductionManager doctrine action lacks live queue evidence: "
        f"{production!r}"
    )
expected_action_item_pairs = {
    "marine_rush": {("marine_pressure", "Marine"), ("bio_facility", "Barracks")},
    "bio_pressure": {
        ("bio_marauder_techlab", "BarracksTechLab"),
        ("bio_marauder_support", "Marauder"),
        ("starport_transition", "Starport"),
        ("medivac_drop_support", "Medivac"),
    },
    "tank_defensive_hold": {
        ("factory_transition", "Factory"),
        ("factory_techlab", "FactoryTechLab"),
        ("siege_tank_composition", "SiegeTank"),
    },
    "siege_contain": {
        ("factory_transition", "Factory"),
        ("factory_techlab", "FactoryTechLab"),
        ("siege_tank_composition", "SiegeTank"),
    },
    "contain_enemy_natural": {
        ("factory_transition", "Factory"),
        ("factory_techlab", "FactoryTechLab"),
        ("siege_tank_composition", "SiegeTank"),
    },
    "mech_transition": {
        ("factory_transition", "Factory"),
        ("factory_techlab", "FactoryTechLab"),
        ("hellion_harassment", "Hellion"),
        ("cyclone_mech", "Cyclone"),
        ("siege_tank_composition", "SiegeTank"),
        ("thor_mech", "Thor"),
    },
    "drop_harassment": {
        ("starport_transition", "Starport"),
        ("drop_reactor", "StarportReactor"),
        ("medivac_drop_support", "Medivac"),
        ("factory_transition", "Factory"),
        ("hellion_harassment", "Hellion"),
        ("reaper_harassment", "Reaper"),
    },
    "worker_line_harassment": {
        ("starport_transition", "Starport"),
        ("drop_reactor", "StarportReactor"),
        ("medivac_drop_support", "Medivac"),
        ("factory_transition", "Factory"),
        ("hellion_harassment", "Hellion"),
        ("reaper_harassment", "Reaper"),
    },
    "expand_macro": {("expand_macro", "CommandCenter")},
    "anti_air_response": {
        ("starport_transition", "Starport"),
        ("anti_air_detection_support", "EngineeringBay"),
        ("anti_air_viking", "Viking"),
    },
}
expected_pairs = expected_action_item_pairs.get(expected_strategy_doctrine, set())

def production_matches_expected(production_entry):
    if not isinstance(production_entry, dict):
        return False
    doctrine_ok = production_entry.get("strategy_doctrine") == expected_strategy_doctrine and production_entry.get("last_doctrine") == expected_strategy_doctrine
    update_ok = production_entry.get("policy_update_id") == aggressive_update_id and production_entry.get("last_doctrine_update_id") == aggressive_update_id
    fresh_ok = production_entry.get("last_doctrine_fresh") is True
    action = str(production_entry.get("last_doctrine_action", "") or "")
    item = str(production_entry.get("last_doctrine_queue_item", "") or "")
    evidence = str(production_entry.get("last_doctrine_evidence", "") or "")
    frame = int(production_entry.get("last_doctrine_frame", 0) or 0)
    issued_at_frame = int(production_entry.get("policy_issued_at_frame", 0) or 0)
    action_ok = not expected_production_actions or action in expected_production_actions
    item_ok = not expected_production_items or item in expected_production_items
    pair_ok = not expected_pairs or (action, item) in expected_pairs
    evidence_ok = evidence in allowed_doctrine_evidence
    frame_ok = frame > 0 and (issued_at_frame <= 0 or frame >= issued_at_frame)
    return doctrine_ok and update_ok and fresh_ok and action_ok and item_ok and pair_ok and evidence_ok and frame_ok and action not in ("", "none") and item not in ("", "none")

def find_expected_production_entry():
    observed_actions = set()
    observed_items = set()
    for entry in iter_telemetry_entries():
        candidate = entry.get("managers", {}).get("ProductionManager", {})
        if not isinstance(candidate, dict):
            continue
        action = str(candidate.get("last_doctrine_action", "") or "")
        item = str(candidate.get("last_doctrine_queue_item", "") or "")
        if action and action != "none":
            observed_actions.add(action)
        if item and item != "none":
            observed_items.add(item)
        if production_matches_expected(candidate):
            return candidate, observed_actions, observed_items
    return None, observed_actions, observed_items

matching_production, observed_production_actions, observed_production_items = find_expected_production_entry()
if production_contract_required and matching_production is None:
    raise SystemExit(
        "ProductionManager did not emit expected strategy action/item evidence; "
        f"expected_actions={sorted(expected_production_actions)}, "
        f"expected_items={sorted(expected_production_items)}, "
        f"observed_actions={sorted(observed_production_actions)}, "
        f"observed_items={sorted(observed_production_items)}, latest={production!r}"
    )
if production_contract_required and int(matching_production.get("last_doctrine_frame", 0)) <= 0:
    raise SystemExit(f"ProductionManager doctrine action frame is missing: {matching_production!r}")
expected_actual_items_by_doctrine = {
    "marine_rush": {"Marine", "Barracks"},
    "bio_pressure": {"Marauder", "BarracksTechLab", "Starport", "Medivac"},
    "tank_defensive_hold": {"FactoryTechLab", "SiegeTank"},
    "siege_contain": {"FactoryTechLab", "SiegeTank"},
    "contain_enemy_natural": {"FactoryTechLab", "SiegeTank"},
    "mech_transition": {"Hellion", "Cyclone", "SiegeTank", "Thor"},
    "drop_harassment": {"Starport", "StarportReactor", "Medivac", "Hellion", "Reaper"},
    "worker_line_harassment": {"Starport", "StarportReactor", "Medivac", "Hellion", "Reaper"},
    "expand_macro": {"CommandCenter"},
    "anti_air_response": {"Starport", "EngineeringBay", "Viking"},
}
actual_item_aliases = {
    "TERRAN_SUPPLYDEPOT": "SupplyDepot",
    "TERRAN_BARRACKS": "Barracks",
    "TERRAN_BARRACKSTECHLAB": "BarracksTechLab",
    "TERRAN_FACTORY": "Factory",
    "TERRAN_FACTORYTECHLAB": "FactoryTechLab",
    "TERRAN_STARPORT": "Starport",
    "TERRAN_STARPORTREACTOR": "StarportReactor",
    "TERRAN_COMMANDCENTER": "CommandCenter",
    "TERRAN_ENGINEERINGBAY": "EngineeringBay",
    "TERRAN_MARINE": "Marine",
    "TERRAN_MARAUDER": "Marauder",
    "TERRAN_REAPER": "Reaper",
    "TERRAN_HELLION": "Hellion",
    "TERRAN_CYCLONE": "Cyclone",
    "TERRAN_THOR": "Thor",
    "TERRAN_SIEGETANK": "SiegeTank",
    "TERRAN_MEDIVAC": "Medivac",
    "TERRAN_VIKINGFIGHTER": "Viking",
}

def find_expected_actual_production_command():
    expected_actual_items = expected_actual_items_by_doctrine.get(
        expected_strategy_doctrine,
        expected_production_items,
    )
    observed_actual_items = set()
    observed_actual_commands = set()
    for entry in iter_telemetry_entries():
        candidate = entry.get("managers", {}).get("ProductionManager", {})
        if not isinstance(candidate, dict):
            continue
        item = actual_item_aliases.get(
            str(candidate.get("last_actual_production_command_item", "") or ""),
            str(candidate.get("last_actual_production_command_item", "") or ""),
        )
        kind = str(candidate.get("last_actual_production_command_kind", "") or "")
        update_id = str(candidate.get("last_actual_production_command_update_id", "") or "")
        frame = int(candidate.get("last_actual_production_command_frame", 0) or 0)
        count = int(candidate.get("actual_production_command_issued_count", 0) or 0)
        policy_issued_at = int(candidate.get("policy_issued_at_frame", 0) or 0)
        if item and item != "none":
            observed_actual_items.add(item)
        if kind and kind != "none" and item and item != "none":
            observed_actual_commands.add(f"{kind}|{item}")
        if (
            count > 0
            and update_id == aggressive_update_id
            and item in expected_actual_items
            and frame > 0
            and (policy_issued_at <= 0 or frame >= policy_issued_at)
        ):
            return candidate, observed_actual_items, observed_actual_commands
    return None, observed_actual_items, observed_actual_commands

matching_actual_production, observed_actual_items, observed_actual_commands = find_expected_actual_production_command()
if production_contract_required and matching_actual_production is None:
    raise SystemExit(
        "ProductionManager queued the expected strategy but did not issue a matching "
        "actual production/build command; "
        f"expected_actual_items={sorted(expected_actual_items_by_doctrine.get(expected_strategy_doctrine, expected_production_items))}, "
        f"observed_actual_items={sorted(observed_actual_items)}, "
        f"observed_actual_commands={sorted(observed_actual_commands)}, latest={production!r}"
    )
positive_bias_expectations = {
    "marine_rush": ("queue_bias_marine", "composition_bias_bio"),
    "bio_pressure": ("queue_bias_medivac", "facility_bias_starport", "tech_unit_bias_medivac"),
    "tank_defensive_hold": ("queue_bias_factory", "queue_bias_siege_tank", "composition_bias_siege"),
    "siege_contain": ("queue_bias_factory", "queue_bias_siege_tank", "composition_bias_siege"),
    "contain_enemy_natural": ("queue_bias_factory", "queue_bias_siege_tank", "composition_bias_siege"),
    "mech_transition": ("queue_bias_factory", "composition_bias_mech", "queue_bias_siege_tank"),
    "drop_harassment": ("queue_bias_starport", "queue_bias_medivac", "composition_bias_drop"),
    "worker_line_harassment": ("composition_bias_harass", "composition_bias_worker_line"),
    "expand_macro": ("queue_bias_command_center", "composition_bias_macro"),
    "anti_air_response": ("queue_bias_starport", "queue_bias_viking", "composition_bias_anti_air"),
}
expected_bias_keys = positive_bias_expectations.get(expected_strategy_doctrine, ())
if production_contract_required and expected_bias_keys and not any(float(production.get(key, 0)) > 0 for key in expected_bias_keys):
    raise SystemExit(
        f"ProductionManager missing positive bias evidence for {expected_strategy_doctrine}: "
        f"expected one of {expected_bias_keys}, production={production!r}"
    )
scout = managers.get("ScoutManager")
if not scout or scout.get("active") is not True:
    raise SystemExit(f"missing ScoutManager activity evidence: {managers!r}")

def require_positive(payload, key, label):
    if float(payload.get(key, 0) or 0) <= 0:
        raise SystemExit(f"{label} missing positive {key}: {payload!r}")

def require_negative(payload, key, label):
    if float(payload.get(key, 0) or 0) >= 0:
        raise SystemExit(f"{label} missing negative {key}: {payload!r}")

if expected_strategy_doctrine in ("tank_defensive_hold", "siege_contain", "contain_enemy_natural"):
    require_positive(combat, "defend_bias", "tank/siege combat contract")
    require_negative(combat, "aggression", "tank/siege combat contract")
    require_positive(production, "composition_bias_siege", "tank/siege production contract")
    require_positive(production, "queue_bias_factory", "tank/siege production contract")
    require_positive(production, "queue_bias_siege_tank", "tank/siege production contract")
    require_positive(squad, "target_army_bias", "tank/siege squad target contract")
elif expected_strategy_doctrine == "mech_transition":
    require_positive(production, "queue_bias_factory", "mech production contract")
    require_positive(production, "composition_bias_mech", "mech production contract")
    require_positive(production, "tech_switch_urgency", "mech production contract")
    require_positive(squad, "reinforce_bias", "mech squad contract")
    require_positive(squad, "target_army_bias", "mech squad target contract")
elif expected_strategy_doctrine in ("drop_harassment", "worker_line_harassment"):
    require_positive(production, "queue_bias_factory", "drop production prerequisite contract")
    require_positive(production, "queue_bias_starport", "drop production contract")
    require_positive(production, "queue_bias_medivac", "drop production contract")
    require_positive(production, "composition_bias_drop", "drop production contract")
    require_positive(combat, "aggression", "drop combat contract")
    require_positive(combat, "commitment_level", "drop combat contract")
    require_positive(squad, "target_worker_line_bias", "drop squad target contract")
    require_positive(scout, "scout_priority", "drop scout contract")
elif expected_strategy_doctrine == "expand_macro":
    require_positive(production, "queue_bias_command_center", "expand production contract")
    require_positive(production, "composition_bias_macro", "expand production contract")
    require_positive(production, "production_continuity_bias", "expand production contract")
    require_positive(combat, "defend_bias", "expand combat safety contract")
    require_negative(combat, "aggression", "expand combat safety contract")
if production_contract_required:
    production_consumed_axes = {
        axis.strip()
        for axis in str(production.get("consumed_axes", "")).split(",")
        if axis.strip()
    }
    for axis in (
        "strategy.doctrine",
        "production.queue_biases.*",
        "production.composition_biases.*",
        "production.production_facility_biases.*",
        "production.tech_switch_urgency",
        "tech.unit_biases.*",
    ):
        if axis not in production_consumed_axes:
            raise SystemExit(f"missing ProductionManager consumed axis {axis}: {production!r}")
workers = managers.get("WorkerManager")
if not workers or workers.get("active") is not True:
    raise SystemExit(f"missing WorkerManager activity evidence: {managers!r}")
if workers.get("repeat_order_guard_active") is not True:
    raise SystemExit(f"worker repeat-order guard is not active: {workers!r}")
if int(workers.get("repeat_order_guard_frames", 0)) != 32:
    raise SystemExit(f"worker repeat-order guard window did not come from the active blackboard profile: {workers!r}")
worker_consumed_axes = {
    axis.strip()
    for axis in str(workers.get("consumed_axes", "")).split(",")
    if axis.strip()
}
if "workers.repeat_order_guard_frames" not in worker_consumed_axes:
    raise SystemExit(f"missing WorkerManager consumed axis evidence: {workers!r}")
if "repeat_order_suppressed_count" not in workers:
    raise SystemExit(f"missing worker repeat-order safety telemetry: {workers!r}")
if int(workers.get("repeat_order_suppressed_count", 0)) != 0:
    raise SystemExit(f"worker repeat-order safety guard had to suppress commands; root cause remains active: {workers!r}")
if "self_position_command_block_count" not in workers:
    raise SystemExit(f"missing worker self-position root-cause telemetry: {workers!r}")
if "root_cause_status" not in workers:
    raise SystemExit(f"missing worker root-cause status telemetry: {workers!r}")
if "root_cause_reason" not in workers:
    raise SystemExit(f"missing worker root-cause reason telemetry: {workers!r}")
for field in (
    "trace_contract_version",
    "trace_event_count",
    "last_trace_frame",
    "last_trace_status",
    "last_trace_reason",
    "last_trace_target_kind",
):
    if field not in workers:
        raise SystemExit(f"missing bounded worker command trace field {field}: {workers!r}")
if int(workers.get("trace_contract_version", 0)) != 1:
    raise SystemExit(f"invalid worker trace contract version: {workers!r}")
if int(workers.get("trace_event_count", 0)) <= 0:
    raise SystemExit(f"worker trace did not observe any command candidates: {workers!r}")
last_trace_frame = int(workers.get("last_trace_frame", 0) or 0)
latest_payload_frame = int(payload.get("frame", 0) or 0)
if last_trace_frame <= 0 or last_trace_frame > latest_payload_frame:
    raise SystemExit(f"worker trace frame is invalid: {workers!r}")
if latest_payload_frame - last_trace_frame > 4096:
    raise SystemExit(f"worker trace is stale relative to latest telemetry: {workers!r}")
for field in ("last_trace_status", "last_trace_reason", "last_trace_target_kind"):
    if str(workers.get(field, "") or "") in ("", "none", "unknown"):
        raise SystemExit(f"worker trace field {field} is not meaningful: {workers!r}")
if int(workers.get("self_position_command_block_count", 0)) != 0:
    raise SystemExit(f"worker self-position command root-cause blocks were observed: {workers!r}")
if workers.get("root_cause_status") == "self_position_move_blocked":
    raise SystemExit(f"worker self-position command root cause is still active: {workers!r}")
if (
    workers.get("root_cause_status") == "duplicate_command_safety_blocked"
    and str(workers.get("root_cause_reason", "")).startswith("scout_")
):
    raise SystemExit(f"ScoutManager still generates duplicate worker move commands: {workers!r}")
worker_noop_position_trace_kinds = {
    "micro_smart_move_position",
    "queued_position",
    "unit_move_position",
    "unit_move_tile_position",
    "unit_smart_position",
}
if (
    str(workers.get("last_trace_status", "") or "") == "accepted_candidate"
    and str(workers.get("last_trace_target_kind", "") or "") in worker_noop_position_trace_kinds
    and int(workers.get("last_trace_target_tag", 0) or 0) == 0
    and float(workers.get("last_trace_distance_sq", 999999.0) or 999999.0) <= 1.0
):
    raise SystemExit(f"worker move/smart self-position candidate was accepted: {workers!r}")
scout = managers.get("ScoutManager")
if not scout or scout.get("active") is not True:
    raise SystemExit(f"missing ScoutManager activity evidence: {managers!r}")
if require_scout_modulation and scout.get("bounded_intervention") is not True:
    raise SystemExit(f"missing ScoutManager modulation evidence: {scout!r}")
scout_consumed_axes = {
    axis.strip()
    for axis in str(scout.get("consumed_axes", "")).split(",")
    if axis.strip()
}
if require_scout_modulation or scout.get("bounded_intervention") is True:
    for axis in ("scouting.scout_priority", "scouting.risk_tolerance"):
        if axis not in scout_consumed_axes:
            raise SystemExit(f"missing ScoutManager consumed axis {axis}: {scout!r}")
if require_scout_movement:
    if scout.get("has_worker_scout") is not True and int(scout.get("scout_unit_count", 0)) <= 0:
        raise SystemExit(f"no scout movement evidence: {scout!r}")
    if scout.get("status") in (None, "", "None"):
        raise SystemExit(f"no scout status evidence: {scout!r}")
    scout_command_count = int(scout.get("actual_command_issued_count", 0) or 0)
    scout_last_command = str(scout.get("last_actual_command", "") or "")
    scout_last_command_frame = int(scout.get("last_actual_command_frame", 0) or 0)
    worker_scout_trace = (
        str(workers.get("last_trace_reason", "") or "").startswith("scout_")
        and str(workers.get("last_trace_status", "") or "") == "accepted_candidate"
    )
    if scout_command_count <= 0 and not worker_scout_trace:
        raise SystemExit(
            "no actual scout command evidence: "
            f"scout={scout!r}, worker_trace={workers!r}"
        )
    if scout_command_count > 0 and (scout_last_command in ("", "none") or scout_last_command_frame <= 0):
        raise SystemExit(f"incomplete actual scout command evidence: {scout!r}")
if not archive.exists():
    raise SystemExit(f"missing telemetry archive: {archive}")
updates = []
worker_archive_violation = None
for line in archive.read_text().splitlines():
    if not line.strip():
        continue
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        continue
    commander_entry = entry.get("managers", {}).get("GameCommander", {})
    update_id = commander_entry.get("update_id")
    if update_id:
        updates.append(update_id)
    worker_entry = entry.get("managers", {}).get("WorkerManager", {})
    if not isinstance(worker_entry, dict):
        continue
    if "root_cause_status" not in worker_entry:
        worker_archive_violation = {
            "code": "missing_worker_root_cause_status",
            "frame": entry.get("frame"),
            "workers": worker_entry,
        }
        break
    if "root_cause_reason" not in worker_entry:
        worker_archive_violation = {
            "code": "missing_worker_root_cause_reason",
            "frame": entry.get("frame"),
            "workers": worker_entry,
        }
        break
    for trace_field in (
        "trace_contract_version",
        "trace_event_count",
        "last_trace_frame",
        "last_trace_status",
        "last_trace_reason",
        "last_trace_target_kind",
    ):
        if trace_field not in worker_entry:
            worker_archive_violation = {
                "code": "missing_worker_trace_contract",
                "field": trace_field,
                "frame": entry.get("frame"),
                "workers": worker_entry,
            }
            break
    if worker_archive_violation is not None:
        break
    worker_entry_frame = int(entry.get("frame", 0) or 0)
    worker_trace_frame = int(worker_entry.get("last_trace_frame", 0) or 0)
    if int(worker_entry.get("trace_contract_version", 0)) != 1:
        worker_archive_violation = {
            "code": "invalid_worker_trace_contract",
            "frame": entry.get("frame"),
            "workers": worker_entry,
        }
        break
    if worker_entry_frame >= 512 and (
        int(worker_entry.get("trace_event_count", 0)) <= 0
        or worker_trace_frame <= 0
        or worker_trace_frame > worker_entry_frame
        or str(worker_entry.get("last_trace_status", "") or "") in ("", "none", "unknown")
        or str(worker_entry.get("last_trace_reason", "") or "") in ("", "none", "unknown")
        or str(worker_entry.get("last_trace_target_kind", "") or "") in ("", "none", "unknown")
    ):
        worker_archive_violation = {
            "code": "invalid_worker_trace_evidence",
            "frame": entry.get("frame"),
            "workers": worker_entry,
        }
        break
    if int(worker_entry.get("repeat_order_suppressed_count", 0)) != 0:
        worker_archive_violation = {
            "code": "archived_worker_repeat_order_suppression",
            "frame": entry.get("frame"),
            "workers": worker_entry,
        }
        break
    if int(worker_entry.get("self_position_command_block_count", 0)) != 0:
        worker_archive_violation = {
            "code": "archived_worker_self_position_command",
            "frame": entry.get("frame"),
            "workers": worker_entry,
        }
        break
    if worker_entry.get("root_cause_status") == "self_position_move_blocked":
        worker_archive_violation = {
            "code": "archived_worker_self_position_status",
            "frame": entry.get("frame"),
            "workers": worker_entry,
        }
        break
    if (
        worker_entry.get("root_cause_status") == "duplicate_command_safety_blocked"
        and str(worker_entry.get("root_cause_reason", "")).startswith("scout_")
    ):
        worker_archive_violation = {
            "code": "archived_scout_duplicate_worker_move",
            "frame": entry.get("frame"),
            "workers": worker_entry,
        }
        break
    if (
        str(worker_entry.get("last_trace_status", "") or "") == "accepted_candidate"
        and str(worker_entry.get("last_trace_target_kind", "") or "") in worker_noop_position_trace_kinds
        and int(worker_entry.get("last_trace_target_tag", 0) or 0) == 0
        and float(worker_entry.get("last_trace_distance_sq", 999999.0) or 999999.0) <= 1.0
    ):
        worker_archive_violation = {
            "code": "archived_worker_move_self_position_candidate",
            "frame": entry.get("frame"),
            "workers": worker_entry,
        }
        break
if worker_archive_violation is not None:
    raise SystemExit(f"worker root-cause archive violation: {worker_archive_violation!r}")
for expected in (defensive_update_id, aggressive_update_id):
    if expected not in updates:
        raise SystemExit(f"stale modulation or missing profile transition: {expected} not in {archive}")
print(json.dumps(payload, sort_keys=True))
PY

if [[ "${SMOKE_KEEP_RUNNING_AFTER_PASS:-0}" == "1" ]]; then
  echo "MicroMachine smoke live hold active; keeping runtime alive after pass criteria."
  while kill -0 "${BOT_PID}" 2>/dev/null; do
    sleep 2
  done
fi
