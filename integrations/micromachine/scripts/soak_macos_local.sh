#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MICROMACHINE_DIR="${MICROMACHINE_DIR:-/private/tmp/voi-micromachine-runtime/MicroMachine}"
MICROMACHINE_BUILD_DIR="${MICROMACHINE_BUILD_DIR:-${MICROMACHINE_DIR}/build-latest-api}"
SC2_ROOT="${SC2_ROOT:-/Users/jinminseong/Desktop/StarCraft2/StarCraft II}"
SC2_LAUNCH_MODE="${SC2_LAUNCH_MODE:-auto}"
SC2_BATTLENET_EXECUTABLE="${SC2_BATTLENET_EXECUTABLE:-/Applications/Battle.net.app/Contents/MacOS/Battle.net}"
SC2_BATTLENET_GAME="${SC2_BATTLENET_GAME:-s2_kokr}"
SC2_ATTACH_TIMEOUT_MS="${SC2_ATTACH_TIMEOUT_MS:-120000}"
SC2_USE_RUNTIME_DIR_ARGS="${SC2_USE_RUNTIME_DIR_ARGS:-0}"
SC2_TEMP_DIR="${SC2_TEMP_DIR:-/private/tmp/voi-sc2-temp-micromachine}"
SC2_ROOT_ALIAS="${SC2_ROOT_ALIAS:-/private/tmp/voi-sc2-root}"
SC2_POST_CLEAN_SETTLE_SECONDS="${SC2_POST_CLEAN_SETTLE_SECONDS:-5}"
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
      echo "MicroMachine soak rejected: SC2_LAUNCH_MODE must be auto, direct, or battlenet." >&2
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
  if [[ "${map_file}" == /* ]]; then
    printf '%s\n' "${map_file}"
    return
  fi

  local candidate="${SC2_ROOT}/Maps/${map_file}"
  if [[ -f "${candidate}" ]]; then
    printf '%s\n' "${candidate}"
    return
  fi

  echo "MicroMachine soak rejected: map file not found: ${map_file} (looked under ${SC2_ROOT}/Maps)." >&2
  exit 2
}

prepare_launch_contract() {
  if [[ ! -x "${SC2_EXECUTABLE}" ]]; then
    echo "MicroMachine soak rejected: SC2 executable is not runnable: ${SC2_EXECUTABLE}" >&2
    exit 2
  fi
  if [[ "${SC2_EXECUTABLE}" != "${SC2_BATTLENET_EXECUTABLE}" && "${SC2_USE_RUNTIME_DIR_ARGS}" == "1" ]]; then
    mkdir -p "${SC2_TEMP_DIR}"
  fi
  MAP_FILE="$(resolve_map_file "${MAP_FILE}")"
}

SC2_EXECUTABLE="${SC2_EXECUTABLE:-$(resolve_sc2_executable)}"
MAP_FILE="${MAP_FILE:-AcropolisLE.SC2Map}"
SOAK_ENEMY_RACE="${SOAK_ENEMY_RACE:-Zerg}"
SOAK_ENEMY_DIFFICULTY="${SOAK_ENEMY_DIFFICULTY:-1}"
SOAK_TARGET_FRAME="${SOAK_TARGET_FRAME:-12000}"
SOAK_TIMEOUT_SECONDS="${SOAK_TIMEOUT_SECONDS:-1200}"
SOAK_TELEMETRY_STALL_SECONDS="${SOAK_TELEMETRY_STALL_SECONDS:-90}"
SOAK_PRODUCTION_DEADLOCK_FRAME="${SOAK_PRODUCTION_DEADLOCK_FRAME:-9000}"
SOAK_PRODUCTION_STALL_FRAMES="${SOAK_PRODUCTION_STALL_FRAMES:-6000}"
SOAK_INCOME_STALL_FRAMES="${SOAK_INCOME_STALL_FRAMES:-2000}"
SOAK_BOOTSTRAP_NO_START_UNITS_FRAME="${SOAK_BOOTSTRAP_NO_START_UNITS_FRAME:-1200}"
SOAK_MAX_PLACEMENT_FAILURES="${SOAK_MAX_PLACEMENT_FAILURES:-3}"
SOAK_MODULATION_CONSUMPTION_GRACE_FRAMES="${SOAK_MODULATION_CONSUMPTION_GRACE_FRAMES:-128}"
SOAK_POLL_SECONDS="${SOAK_POLL_SECONDS:-2}"
SOAK_BOOTSTRAP_GRACE_SECONDS="${SOAK_BOOTSTRAP_GRACE_SECONDS:-120}"
SOAK_PROFILE_REFRESH_FRAMES="${SOAK_PROFILE_REFRESH_FRAMES:-7000}"
SOAK_AGGRESSIVE_MIN_FRAME="${SOAK_AGGRESSIVE_MIN_FRAME:-13000}"
SOAK_PROFILE_SEQUENCE="${SOAK_PROFILE_SEQUENCE:-default_defensive_to_aggressive}"
SOAK_MAX_ATTEMPTS="${SOAK_MAX_ATTEMPTS:-3}"
SOAK_RETRY_SETTLE_SECONDS="${SOAK_RETRY_SETTLE_SECONDS:-15}"
SOAK_NON_RETRYABLE_FAILURE_CODES="${SOAK_NON_RETRYABLE_FAILURE_CODES:-bootstrap_no_start_units repeated_placement_failures no_production_deadlock production_stall income_stall manager_intervention_missing stale_modulation strategy_profile_missing tactical_effect_missing}"
VOI_SC2_CREATEGAME_MAP_DATA="${VOI_SC2_CREATEGAME_MAP_DATA:-1}"
SOAK_ATTEMPT_INDEX="${SOAK_ATTEMPT_INDEX:-}"
SOAK_RUN_ID="${SOAK_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
SOAK_ARTIFACT_ROOT="${SOAK_ARTIFACT_ROOT:-/private/tmp/voi-mm-soak}"
# Artifact names inside SOAK_RUN_DIR are deterministic for PR evidence and QA.
SOAK_RUN_DIR="${SOAK_RUN_DIR:-${SOAK_ARTIFACT_ROOT}/${SOAK_RUN_ID}}"
BLACKBOARD_DIR="${BLACKBOARD_DIR:-${SOAK_RUN_DIR}}"
BOT_LOG="${BLACKBOARD_DIR}/micromachine.log"
CLASSIFIER_BOT_LOG="${BLACKBOARD_DIR}/micromachine_combined.log"
MICROMACHINE_DATA_DIR="${MICROMACHINE_DATA_DIR:-${MICROMACHINE_DIR}/bin/data}"
RUNTIME_LOG_MARKER="${BLACKBOARD_DIR}/runtime_log_start.marker"
RUNTIME_LOG_BASELINE="${BLACKBOARD_DIR}/runtime_log_baseline.tsv"
SOAK_REPORT="${BLACKBOARD_DIR}/soak_report.json"
SOAK_LIVE_REPORT="${BLACKBOARD_DIR}/soak_live_report.json"
# Final classifier requires CombatCommander and ScoutManager bounded_intervention evidence.
SC2_NET_ADDRESS="${SC2_NET_ADDRESS:-127.0.0.1}"
SC2_PORTS=(${SC2_PORTS:-8167 8168})
BOT_PID=""
BOT_EXIT_CODE=""
BOT_STOPPED=0
BOT_TERMINATION_REASON=""
PREEXISTING_SC2_PORT_PIDS=""
DEFENSIVE_UPDATE_ID="${DEFENSIVE_UPDATE_ID:-soak-defensive-hold}"
AGGRESSIVE_UPDATE_ID="${AGGRESSIVE_UPDATE_ID:-soak-aggressive-pressure}"
AGGRESSIVE_CURRENT_UPDATE_ID=""
ACTIVE_PROFILE_KEY=""
LAST_PROFILE_REFRESH_FRAME=0
SOAK_TARGET_REACHED=0
PROFILE_SCHEDULE_KEYS=()
PROFILE_SCHEDULE_FRAMES=()
PROFILE_SCHEDULE_PUBLISHED=()
SOAK_EXPECTED_PROFILE_TAGS=""
SOAK_EXPECTED_TACTICAL_EFFECTS="${SOAK_EXPECTED_TACTICAL_EFFECTS:-}"

if [[ -z "${SOAK_ATTEMPT_INDEX}" && "${SOAK_MAX_ATTEMPTS}" -gt 1 ]]; then
  mkdir -p "${SOAK_RUN_DIR}"
  for (( attempt = 1; attempt <= SOAK_MAX_ATTEMPTS; attempt++ )); do
    attempt_dir="${SOAK_RUN_DIR}/attempt-${attempt}"
    echo "Starting MicroMachine soak attempt ${attempt}/${SOAK_MAX_ATTEMPTS}: ${attempt_dir}"
    if SOAK_ATTEMPT_INDEX="${attempt}" SOAK_MAX_ATTEMPTS=1 SOAK_RUN_DIR="${attempt_dir}" BLACKBOARD_DIR="${attempt_dir}" "${BASH_SOURCE[0]}"; then
      python3 - <<'PY' "${attempt_dir}/soak_report.json" "${SOAK_RUN_DIR}/soak_report.json" "${attempt}" "${attempt_dir}" "${SOAK_RUN_DIR}"
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
attempt = int(sys.argv[3])
attempt_dir = Path(sys.argv[4])
root = Path(sys.argv[5])
payload = json.loads(source.read_text())
manifest = payload.get("artifact_manifest", {})
if isinstance(manifest, dict):
    fixed_manifest = {}
    for key, value in manifest.items():
        if not isinstance(value, str):
            continue
        artifact = Path(value)
        if artifact.is_absolute():
            fixed_manifest[key] = value
        else:
            fixed_manifest[key] = str((attempt_dir / artifact).relative_to(root))
    payload["artifact_manifest"] = fixed_manifest
payload["selected_attempt"] = attempt
payload["selected_attempt_dir"] = str(attempt_dir)
payload["attempt_summary"] = {
    "attempt": attempt,
    "status": payload.get("status"),
    "latest_frame": payload.get("latest_frame"),
    "failures": payload.get("failures", []),
}
attempts = []
for index in range(1, attempt + 1):
    report_path = root / f"attempt-{index}" / "soak_report.json"
    if not report_path.exists():
        attempts.append({"attempt": index, "status": "missing_report"})
        continue
    report = json.loads(report_path.read_text())
    attempts.append(
        {
            "attempt": index,
            "status": report.get("status"),
            "latest_frame": report.get("latest_frame"),
            "failures": report.get("failures", []),
            "report": str(report_path),
        }
    )
payload["attempts"] = attempts
target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
      echo "MicroMachine soak passed on attempt ${attempt}/${SOAK_MAX_ATTEMPTS}; report: ${SOAK_REPORT}"
      exit 0
    fi

    if [[ -f "${attempt_dir}/soak_report.json" ]]; then
      if python3 - <<'PY' "${attempt_dir}/soak_report.json" "${SOAK_NON_RETRYABLE_FAILURE_CODES}"
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
non_retryable = set(sys.argv[2].split())
codes = {
    failure.get("code")
    for failure in report.get("failures", [])
    if isinstance(failure, dict)
}
retryable_startup_codes = {
    "micromachine_crash",
    "micromachine_process_stopped",
    "telemetry_missing",
}
latest_frame = int(report.get("latest_frame") or 0)
if codes & non_retryable:
    raise SystemExit(0)
if latest_frame == 0 and codes and codes <= retryable_startup_codes:
    raise SystemExit(1)
raise SystemExit(0)
PY
      then
        python3 - <<'PY' "${SOAK_RUN_DIR}" "${SOAK_REPORT}" "${SOAK_MAX_ATTEMPTS}" "${attempt}" "non_retryable_failure"
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
target = Path(sys.argv[2])
attempts = int(sys.argv[3])
stopped_at = int(sys.argv[4])
reason = sys.argv[5]
reports = []
for attempt in range(1, attempts + 1):
    report_path = root / f"attempt-{attempt}" / "soak_report.json"
    if attempt > stopped_at and not report_path.exists():
        reports.append({"attempt": attempt, "status": "not_run"})
        continue
    if not report_path.exists():
        reports.append({"attempt": attempt, "status": "missing_report"})
        continue
    payload = json.loads(report_path.read_text())
    reports.append(
        {
            "attempt": attempt,
            "status": payload.get("status"),
            "latest_frame": payload.get("latest_frame"),
            "failures": payload.get("failures", []),
            "report": str(report_path),
        }
    )
target.write_text(
    json.dumps(
        {
            "status": "failed",
            "ok": False,
            "attempts": reports,
            "max_attempts": attempts,
            "stopped_at_attempt": stopped_at,
            "stop_reason": reason,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
PY
        echo "MicroMachine soak stopped after non-retryable attempt ${attempt}; report: ${SOAK_REPORT}" >&2
        exit 1
      fi
    fi
    if (( attempt < SOAK_MAX_ATTEMPTS )); then
      echo "MicroMachine soak retrying after retryable startup failure; settling ${SOAK_RETRY_SETTLE_SECONDS}s before attempt $((attempt + 1))/${SOAK_MAX_ATTEMPTS}." >&2
      sleep "${SOAK_RETRY_SETTLE_SECONDS}"
    fi
  done

  python3 - <<'PY' "${SOAK_RUN_DIR}" "${SOAK_REPORT}" "${SOAK_MAX_ATTEMPTS}"
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
target = Path(sys.argv[2])
attempts = int(sys.argv[3])
reports = []
for attempt in range(1, attempts + 1):
    report_path = root / f"attempt-{attempt}" / "soak_report.json"
    if not report_path.exists():
        reports.append({"attempt": attempt, "status": "missing_report"})
        continue
    payload = json.loads(report_path.read_text())
    reports.append(
        {
            "attempt": attempt,
            "status": payload.get("status"),
            "latest_frame": payload.get("latest_frame"),
            "failures": payload.get("failures", []),
            "report": str(report_path),
        }
    )
target.write_text(
    json.dumps(
        {
            "status": "failed",
            "ok": False,
            "attempts": reports,
            "max_attempts": attempts,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
PY
  echo "MicroMachine soak failed after ${SOAK_MAX_ATTEMPTS} attempts; report: ${SOAK_REPORT}" >&2
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
    done < <(sc2_port_pids "${port}" || true)
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

refresh_classifier_log() {
  rm -f "${CLASSIFIER_BOT_LOG}"
  local log_file
  while IFS= read -r log_file; do
    [[ -n "${log_file}" && -f "${log_file}" ]] || continue
    {
      printf '%s\n' "--- ${log_file} ---"
      stream_current_run_log "${log_file}"
    } >> "${CLASSIFIER_BOT_LOG}"
  done < <(candidate_bot_logs)
  [[ -f "${CLASSIFIER_BOT_LOG}" ]] || touch "${CLASSIFIER_BOT_LOG}"
}

print_bot_logs() {
  refresh_classifier_log
  tail -200 "${CLASSIFIER_BOT_LOG}" >&2 || true
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

publish_profile() {
  local profile="$1"
  local update_id="$2"
  local frame="$3"
  local ttl_seconds
  ttl_seconds=$((SOAK_TIMEOUT_SECONDS + 300))
  if (( ttl_seconds > 900 )); then
    ttl_seconds=900
  fi
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' "${BLACKBOARD_DIR}" "${profile}" "${update_id}" "${frame}" "${ttl_seconds}"
import sys

from starcraft_commander.micromachine_runtime import (
    MicroMachineFilesystemBlackboard,
    build_micromachine_strategy_profile,
)

directory, profile_name, update_id, frame_text, ttl_text = sys.argv[1:6]
backend = MicroMachineFilesystemBlackboard(directory)
ttl_seconds = int(ttl_text)
vector = build_micromachine_strategy_profile(profile_name, ttl_seconds=ttl_seconds)
backend.publish_vector(vector, current_frame=int(frame_text), update_id=update_id)
PY
}

parse_profile_schedule() {
  local sequence="$1"
  PROFILE_SCHEDULE_KEYS=()
  PROFILE_SCHEDULE_FRAMES=()
  PROFILE_SCHEDULE_PUBLISHED=()
  if [[ "${sequence}" == "default_defensive_to_aggressive" ]]; then
    PROFILE_SCHEDULE_KEYS=("defensive_hold" "aggressive_pressure")
    PROFILE_SCHEDULE_FRAMES=(0 "${SOAK_AGGRESSIVE_MIN_FRAME}")
  elif [[ "${sequence}" == *","* || "${sequence}" == *"@"* ]]; then
    IFS=',' read -r -a entries <<< "${sequence}"
    local entry key frame
    for entry in "${entries[@]}"; do
      [[ -n "${entry}" ]] || continue
      key="${entry%@*}"
      frame="0"
      if [[ "${entry}" == *"@"* ]]; then
        frame="${entry##*@}"
      fi
      if [[ ! "${frame}" =~ ^[0-9]+$ ]]; then
        echo "MicroMachine soak rejected: invalid profile schedule frame in ${entry}" >&2
        exit 2
      fi
      PROFILE_SCHEDULE_KEYS+=("${key}")
      PROFILE_SCHEDULE_FRAMES+=("${frame}")
    done
  else
    PROFILE_SCHEDULE_KEYS=("${sequence}")
    PROFILE_SCHEDULE_FRAMES=(0)
  fi
  if (( ${#PROFILE_SCHEDULE_KEYS[@]} == 0 )); then
    echo "MicroMachine soak rejected: empty SOAK_PROFILE_SEQUENCE" >&2
    exit 2
  fi
  local index
  for (( index = 0; index < ${#PROFILE_SCHEDULE_KEYS[@]}; index++ )); do
    PROFILE_SCHEDULE_PUBLISHED+=(0)
  done
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' "${PROFILE_SCHEDULE_KEYS[@]}"
import sys

from starcraft_commander.micromachine_runtime import MICROMACHINE_STRATEGY_PROFILE_KEYS

allowed = set(MICROMACHINE_STRATEGY_PROFILE_KEYS)
unknown = [key for key in sys.argv[1:] if key not in allowed]
if unknown:
    raise SystemExit(
        "MicroMachine soak rejected: unknown SOAK_PROFILE_SEQUENCE profile(s): "
        + ", ".join(unknown)
    )
PY
  local expected=()
  for (( index = 0; index < ${#PROFILE_SCHEDULE_KEYS[@]}; index++ )); do
    if (( PROFILE_SCHEDULE_FRAMES[$index] <= SOAK_TARGET_FRAME )); then
      expected+=("${PROFILE_SCHEDULE_KEYS[$index]}")
    fi
  done
  if (( ${#expected[@]} == 0 )); then
    echo "MicroMachine soak rejected: SOAK_PROFILE_SEQUENCE has no profile scheduled before SOAK_TARGET_FRAME." >&2
    exit 2
  fi
  SOAK_EXPECTED_PROFILE_TAGS="${expected[*]}"
}

publish_due_profiles() {
  local current_frame="$1"
  local index key frame update_id
  for (( index = 0; index < ${#PROFILE_SCHEDULE_KEYS[@]}; index++ )); do
    [[ "${PROFILE_SCHEDULE_PUBLISHED[$index]}" -eq 0 ]] || continue
    key="${PROFILE_SCHEDULE_KEYS[$index]}"
    frame="${PROFILE_SCHEDULE_FRAMES[$index]}"
    (( current_frame >= frame )) || continue
    if [[ "${key}" == "aggressive_pressure" ]] && ! has_required_macro_evidence; then
      continue
    fi
    update_id="soak-${key}-${current_frame}"
    if [[ "${key}" == "defensive_hold" && "${current_frame}" == "0" ]]; then
      update_id="${DEFENSIVE_UPDATE_ID}"
    elif [[ "${key}" == "aggressive_pressure" ]]; then
      update_id="${AGGRESSIVE_UPDATE_ID}-${current_frame}"
      AGGRESSIVE_CURRENT_UPDATE_ID="${update_id}"
    fi
    publish_profile "${key}" "${update_id}" "${current_frame}"
    PROFILE_SCHEDULE_PUBLISHED[$index]=1
    ACTIVE_PROFILE_KEY="${key}"
    LAST_PROFILE_REFRESH_FRAME="${current_frame}"
  done
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

classify_soak() {
  local mode="$1"
  local report_path="$2"
  local extra_args=()
  refresh_classifier_log
  if [[ "${mode}" == "live" ]]; then
    extra_args+=(--allow-incomplete)
  fi
  if [[ -n "${BOT_EXIT_CODE}" ]]; then
    extra_args+=(--bot-exit-code "${BOT_EXIT_CODE}")
  fi
  if [[ "${BOT_STOPPED}" -eq 1 ]]; then
    extra_args+=(--bot-stopped)
  fi
  if [[ -n "${BOT_TERMINATION_REASON}" ]]; then
    extra_args+=(--termination-reason "${BOT_TERMINATION_REASON}")
  fi
  local command=(
    python3 -m starcraft_commander.micromachine_soak
    --blackboard-dir "${BLACKBOARD_DIR}" \
    --bot-log "${CLASSIFIER_BOT_LOG}" \
    --artifact-dir "${BLACKBOARD_DIR}" \
    --report "${report_path}" \
    --target-frame "${SOAK_TARGET_FRAME}" \
    --timeout-seconds "${SOAK_TIMEOUT_SECONDS}" \
    --telemetry-stall-seconds "${SOAK_TELEMETRY_STALL_SECONDS}" \
    --production-deadlock-frame "${SOAK_PRODUCTION_DEADLOCK_FRAME}" \
    --production-stall-frames "${SOAK_PRODUCTION_STALL_FRAMES}" \
    --income-stall-frames "${SOAK_INCOME_STALL_FRAMES}" \
    --bootstrap-no-start-units-frame "${SOAK_BOOTSTRAP_NO_START_UNITS_FRAME}" \
    --max-placement-failures "${SOAK_MAX_PLACEMENT_FAILURES}" \
    --modulation-consumption-grace-frames "${SOAK_MODULATION_CONSUMPTION_GRACE_FRAMES}" \
    --expected-profile-tags "${SOAK_EXPECTED_PROFILE_TAGS}" \
    --expected-tactical-effects "${SOAK_EXPECTED_TACTICAL_EFFECTS}"
  )
  if (( ${#extra_args[@]} > 0 )); then
    command+=("${extra_args[@]}")
  fi
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" "${command[@]}"
}

fail_from_live_classifier() {
  local reason="$1"
  echo "MicroMachine soak live classifier failed: ${reason}" >&2
  BOT_STOPPED=1
  BOT_TERMINATION_REASON="live_classifier_failure"
  cleanup_runtime
  BOT_PID=""
  classify_soak "final" "${SOAK_REPORT}" >/dev/null || true
  print_bot_logs
  exit 1
}

parse_profile_schedule "${SOAK_PROFILE_SEQUENCE}"
prepare_launch_contract
SC2_RUNTIME_ROOT="$(prepare_sc2_runtime_root)"
if [[ "${SC2_EXECUTABLE}" == "${SC2_BATTLENET_EXECUTABLE}" && -z "${VOI_SC2_EXTRA_ARGS:-}" ]]; then
  VOI_SC2_EXTRA_ARGS="--game=${SC2_BATTLENET_GAME} --gamepath=${SC2_RUNTIME_ROOT}/"
elif [[ -z "${VOI_SC2_EXTRA_ARGS:-}" && "${SC2_USE_RUNTIME_DIR_ARGS}" == "1" ]]; then
  VOI_SC2_EXTRA_ARGS="-dataDir ${SC2_RUNTIME_ROOT} -tempDir ${SC2_TEMP_DIR}"
fi

mkdir -p "${BLACKBOARD_DIR}"
rm -f \
  "${BLACKBOARD_DIR}/latest_telemetry.json" \
  "${BLACKBOARD_DIR}/telemetry.jsonl" \
  "${BLACKBOARD_DIR}/latest_modulation.json" \
  "${BLACKBOARD_DIR}/latest_modulation.kv" \
  "${BLACKBOARD_DIR}/modulation_updates.jsonl" \
  "${BOT_LOG}" \
  "${CLASSIFIER_BOT_LOG}" \
  "${RUNTIME_LOG_MARKER}" \
  "${RUNTIME_LOG_BASELINE}" \
  "${SOAK_REPORT}" \
  "${SOAK_LIVE_REPORT}"

touch "${RUNTIME_LOG_MARKER}"
record_runtime_log_baseline
publish_due_profiles "0"
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
config = json.loads(path.read_text())
config["SC2API"]["PlayAsHuman"] = False
config["SC2API"]["ForceStepMode"] = bool(int(os.environ.get("SOAK_FORCE_STEP_MODE", "0")))
config["SC2API"]["MapFile"] = map_file
config["SC2API"]["PlayVsItSelf"] = bool(int(os.environ.get("SOAK_PLAY_VS_SELF", "0")))
enemy_difficulty = int(os.environ.get("SOAK_ENEMY_DIFFICULTY", "1"))
if enemy_difficulty < 1 or enemy_difficulty > 10:
    raise SystemExit("SOAK_ENEMY_DIFFICULTY must be an integer from 1 to 10")
enemy_race = os.environ.get("SOAK_ENEMY_RACE", "Zerg")
if enemy_race not in {"Terran", "Protoss", "Zerg", "Random"}:
    raise SystemExit("SOAK_ENEMY_RACE must be Terran, Protoss, Zerg, or Random")
config["SC2API"]["EnemyDifficulty"] = enemy_difficulty
config["SC2API"]["EnemyRace"] = enemy_race
config["SC2API"]["StepSize"] = 1
config["Macro"]["SelectStartingBuildBasedOnHistory"] = False
config["Macro"]["PrintGreetingMessage"] = False
config["SC2API Strategy"]["Terran"] = "Terran_MarineRush"
terran_strategies = config["SC2API Strategy"]["Strategies"]
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

deadline=$((SECONDS + SOAK_TIMEOUT_SECONDS))
bootstrap_deadline=$((SECONDS + SOAK_BOOTSTRAP_GRACE_SECONDS))
while kill -0 "${BOT_PID}" 2>/dev/null; do
  current_telemetry_frame="$(telemetry_frame || true)"
  if [[ -n "${current_telemetry_frame}" ]]; then
    publish_due_profiles "${current_telemetry_frame}"

    if [[ -n "${ACTIVE_PROFILE_KEY}" ]] && (( current_telemetry_frame - LAST_PROFILE_REFRESH_FRAME >= SOAK_PROFILE_REFRESH_FRAMES )); then
      publish_profile "${ACTIVE_PROFILE_KEY}" "soak-${ACTIVE_PROFILE_KEY}-refresh-${current_telemetry_frame}" "${current_telemetry_frame}"
      LAST_PROFILE_REFRESH_FRAME="${current_telemetry_frame}"
    fi

    if ! classify_soak "live" "${SOAK_LIVE_REPORT}" >/dev/null; then
      fail_from_live_classifier "frame ${current_telemetry_frame}"
    fi
    if (( current_telemetry_frame >= SOAK_TARGET_FRAME )); then
      SOAK_TARGET_REACHED=1
      break
    fi
  elif (( SECONDS >= bootstrap_deadline )); then
    if ! classify_soak "live" "${SOAK_LIVE_REPORT}" >/dev/null; then
      fail_from_live_classifier "bootstrap grace exceeded"
    fi
  fi

  if (( SECONDS >= deadline )); then
    echo "MicroMachine soak timed out after ${SOAK_TIMEOUT_SECONDS}s before frame ${SOAK_TARGET_FRAME}" >&2
    BOT_STOPPED=1
    BOT_TERMINATION_REASON="timeout"
    cleanup_runtime
    BOT_PID=""
    classify_soak "final" "${SOAK_REPORT}" >/dev/null || true
    print_bot_logs
    exit 1
  fi
  sleep "${SOAK_POLL_SECONDS}"
done

if [[ "${SOAK_TARGET_REACHED}" -eq 1 ]]; then
  cleanup_runtime
  BOT_PID=""
  BOT_STOPPED=1
  BOT_TERMINATION_REASON="target_frame_reached_cleanup"
elif [[ -n "${BOT_PID}" ]]; then
  set +e
  wait "${BOT_PID}"
  BOT_EXIT_CODE="$?"
  set -e
  BOT_STOPPED=1
  BOT_TERMINATION_REASON="process_exited"
fi

if classify_soak "final" "${SOAK_REPORT}" >/dev/null; then
  python3 - <<'PY' "${SOAK_REPORT}"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
print(
    "MicroMachine soak passed: "
    f"frame={payload['latest_frame']} "
    f"target={payload['config']['target_frame']} "
    f"artifacts={sys.argv[1]}"
)
PY
  cleanup_runtime
  exit 0
fi

echo "MicroMachine soak failed; report: ${SOAK_REPORT}" >&2
print_bot_logs
exit 1
