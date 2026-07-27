#!/usr/bin/env bash
set -euo pipefail

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live-hold)
      export SMOKE_KEEP_RUNNING_AFTER_PASS=1
      export SMOKE_MAX_ATTEMPTS="${SMOKE_MAX_ATTEMPTS:-1}"
      export SMOKE_MANUAL_LIVE_MODE="${SMOKE_MANUAL_LIVE_MODE:-1}"
      export SMOKE_AUTO_AGGRESSIVE_PROFILE="${SMOKE_AUTO_AGGRESSIVE_PROFILE:-0}"
      export SMOKE_ENEMY_DIFFICULTY="${SMOKE_ENEMY_DIFFICULTY:-7}"
      ;;
    --fresh-live-session)
      export SMOKE_FRESH_LIVE_SESSION=1
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
    --enemy-difficulty)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "MicroMachine smoke rejected: --enemy-difficulty requires a value." >&2
        exit 2
      fi
      if [[ ! "$2" =~ ^([1-9]|10)$ ]]; then
        echo "MicroMachine smoke rejected: --enemy-difficulty must be an integer from 1 to 10." >&2
        exit 2
      fi
      export SMOKE_ENEMY_DIFFICULTY="$2"
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

SMOKE_ENEMY_DIFFICULTY="${SMOKE_ENEMY_DIFFICULTY:-1}"
if [[ ! "${SMOKE_ENEMY_DIFFICULTY}" =~ ^([1-9]|10)$ ]]; then
  echo "MicroMachine smoke rejected: SMOKE_ENEMY_DIFFICULTY must be an integer from 1 to 10." >&2
  exit 2
fi
export SMOKE_ENEMY_DIFFICULTY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MICROMACHINE_DIR="${MICROMACHINE_DIR:-/private/tmp/voi-micromachine-runtime/MicroMachine}"
ROOT_DIR="${ROOT_DIR:-$(dirname "${MICROMACHINE_DIR}")}"
S2CLIENT_DIR="${S2CLIENT_DIR:-${ROOT_DIR}/s2client-api}"
MICROMACHINE_BUILD_DIR="${MICROMACHINE_BUILD_DIR:-${MICROMACHINE_DIR}/build-latest-api}"
MICROMACHINE_BUILD_IDENTITY_REPORT="${MICROMACHINE_BUILD_IDENTITY_REPORT:-${MICROMACHINE_BUILD_DIR}/voi_build_identity.json}"
SMOKE_REQUIRE_BUILD_IDENTITY="${SMOKE_REQUIRE_BUILD_IDENTITY:-1}"

discover_sc2_root() {
  local configured="${SC2_ROOT:-${SC2PATH:-}}"
  if [[ -n "${configured}" ]]; then
    printf '%s\n' "${configured/#\~/${HOME}}"
    return
  fi
  local candidate
  for candidate in \
    "${HOME}/Desktop/StarCraft2/StarCraft II" \
    "/Applications/StarCraft II" \
    "${HOME}/Applications/StarCraft II"; do
    if [[ -d "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done
  printf '%s\n' "/Applications/StarCraft II"
}

SC2_ROOT="$(discover_sc2_root)"
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
    latest="$(
      find "${versions_dir}" -path '*/SC2.app/Contents/MacOS/SC2' -type f 2>/dev/null |
        awk -F/ '
          {
            for (part = 1; part <= NF - 4; ++part) {
              if ($part ~ /^Base[0-9]+$/ &&
                  $(part + 1) == "SC2.app" &&
                  $(part + 2) == "Contents" &&
                  $(part + 3) == "MacOS" &&
                  $(part + 4) == "SC2") {
                version = substr($part, 5) + 0
                if (!found || version > maximum) {
                  found = 1
                  maximum = version
                  selected = $0
                }
              }
            }
          }
          END {
            if (found) {
              print selected
            }
          }
        '
    )"
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
import os
import sys
from pathlib import Path

from starcraft_commander.micromachine_build_identity import (
    MicroMachineBuildIdentityConfig,
    build_micromachine_build_identity,
    micromachine_build_identity_admission_error,
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
admission_error = micromachine_build_identity_admission_error(
    recorded,
    current,
)
if admission_error:
    raise SystemExit(
        "MicroMachine smoke rejected: stale build identity or unsupported "
        f"schema: {admission_error}. "
        "Re-run integrations/micromachine/scripts/build_macos_local.sh."
    )
PY
}

snapshot_runtime_identity() {
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' \
    "${MICROMACHINE_BUILD_IDENTITY_REPORT}" \
    "${MICROMACHINE_BUILD_DIR}/bin/MicroMachine" \
    "${RUNTIME_IDENTITY_SNAPSHOT}" \
    "${SMOKE_RUN_ID}" \
    "${SMOKE_REPO_HEAD_SHA}" \
    "${BASH_SOURCE[0]}" \
    "${REPO_ROOT}"
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from starcraft_commander.micromachine_build_identity import (
    build_runtime_workspace_identity,
)

report_path = Path(sys.argv[1])
binary_path = Path(sys.argv[2])
snapshot_path = Path(sys.argv[3])
run_id = sys.argv[4]
repo_head_sha = sys.argv[5]
smoke_script_path = Path(sys.argv[6]).resolve()
repo_root = Path(sys.argv[7]).resolve()

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def git_head(path):
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    head = completed.stdout.strip()
    if completed.returncode != 0 or not head:
        raise SystemExit(
            "MicroMachine smoke rejected: cannot resolve repository HEAD "
            f"for runtime provenance: {completed.stderr.strip()}"
        )
    return head

try:
    report = json.loads(report_path.read_text())
except (json.JSONDecodeError, OSError) as exc:
    raise SystemExit(
        f"MicroMachine smoke rejected: cannot snapshot build report: {exc}"
    )
if report.get("ok") is not True:
    raise SystemExit(
        "MicroMachine smoke rejected: cannot snapshot non-passing build identity."
    )
binary_sha256 = sha256(binary_path)
reported_binary_sha256 = report.get("checksums", {}).get("binary_sha256")
if binary_sha256 != reported_binary_sha256:
    raise SystemExit(
        "MicroMachine smoke rejected: executable changed before launch: "
        f"reported={reported_binary_sha256} observed={binary_sha256}"
    )
observed = report.get("observed", {})
runtime_workspace = build_runtime_workspace_identity(repo_root)
actual_repo_head_sha = git_head(repo_root)
if repo_head_sha != actual_repo_head_sha:
    raise SystemExit(
        "MicroMachine smoke rejected: configured repository HEAD does not "
        f"match the live checkout: configured={repo_head_sha} "
        f"actual={actual_repo_head_sha}"
    )
payload = {
    "schema_version": 1,
    "run_id": run_id,
    "repo_head_sha": actual_repo_head_sha,
    "smoke_script": str(smoke_script_path),
    "smoke_script_sha256": sha256(smoke_script_path),
    "python_runtime_source_identity": runtime_workspace["identity"],
    "python_runtime_source_files": runtime_workspace["files"],
    "build_identity_report": str(report_path),
    "build_identity_report_sha256": sha256(report_path),
    "build_identity": report.get("identity"),
    "build_schema_version": report.get("schema_version"),
    "micromachine_source_state_sha256": (
        observed.get("micromachine_source_state_sha256")
        if isinstance(observed, dict)
        else None
    ),
    "binary": str(binary_path),
    "binary_sha256": binary_sha256,
}
snapshot_path.parent.mkdir(parents=True, exist_ok=True)
temporary = snapshot_path.with_name(snapshot_path.name + ".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
os.replace(temporary, snapshot_path)
PY
}

verify_runtime_identity_snapshot() {
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' \
    "${MICROMACHINE_BUILD_IDENTITY_REPORT}" \
    "${MICROMACHINE_BUILD_DIR}/bin/MicroMachine" \
    "${RUNTIME_IDENTITY_SNAPSHOT}" \
    "${SMOKE_RUN_ID}" \
    "${SMOKE_REPO_HEAD_SHA}" \
    "${BASH_SOURCE[0]}" \
    "${REPO_ROOT}"
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from starcraft_commander.micromachine_build_identity import (
    build_runtime_workspace_identity,
)

report_path = Path(sys.argv[1])
binary_path = Path(sys.argv[2])
snapshot_path = Path(sys.argv[3])
run_id = sys.argv[4]
repo_head_sha = sys.argv[5]
smoke_script_path = Path(sys.argv[6]).resolve()
repo_root = Path(sys.argv[7]).resolve()

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def git_head(path):
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    head = completed.stdout.strip()
    if completed.returncode != 0 or not head:
        raise SystemExit(
            "MicroMachine smoke rejected: cannot resolve repository HEAD "
            f"for runtime provenance: {completed.stderr.strip()}"
        )
    return head

try:
    snapshot = json.loads(snapshot_path.read_text())
except (json.JSONDecodeError, OSError) as exc:
    raise SystemExit(
        f"MicroMachine smoke rejected: invalid runtime identity snapshot: {exc}"
    )
if sha256(report_path) != snapshot.get("build_identity_report_sha256"):
    raise SystemExit(
        "MicroMachine smoke rejected: build identity report changed during live run."
    )
if sha256(binary_path) != snapshot.get("binary_sha256"):
    raise SystemExit(
        "MicroMachine smoke rejected: executable changed during live run."
    )
if snapshot.get("run_id") != run_id:
    raise SystemExit(
        "MicroMachine smoke rejected: runtime run ID changed during live run."
    )
actual_repo_head_sha = git_head(repo_root)
if snapshot.get("repo_head_sha") != actual_repo_head_sha:
    raise SystemExit(
        "MicroMachine smoke rejected: repository HEAD changed during live run: "
        f"started={snapshot.get('repo_head_sha')} actual={actual_repo_head_sha}"
    )
if repo_head_sha != actual_repo_head_sha:
    raise SystemExit(
        "MicroMachine smoke rejected: configured repository HEAD changed "
        f"during live run: configured={repo_head_sha} "
        f"actual={actual_repo_head_sha}"
    )
if snapshot.get("smoke_script") != str(smoke_script_path):
    raise SystemExit(
        "MicroMachine smoke rejected: smoke script path changed during live run."
    )
if snapshot.get("smoke_script_sha256") != sha256(smoke_script_path):
    raise SystemExit(
        "MicroMachine smoke rejected: smoke script changed during live run."
    )
runtime_workspace = build_runtime_workspace_identity(repo_root)
if (
    snapshot.get("python_runtime_source_identity")
    != runtime_workspace.get("identity")
    or snapshot.get("python_runtime_source_files")
    != runtime_workspace.get("files")
):
    raise SystemExit(
        "MicroMachine smoke rejected: Python runtime workspace changed during "
        "live run."
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
SMOKE_RUN_ID="${SMOKE_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$-${RANDOM}}"
SMOKE_REPO_HEAD_SHA="${SMOKE_REPO_HEAD_SHA:-$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || printf 'unknown')}"
export SMOKE_RUN_ID SMOKE_REPO_HEAD_SHA
BOT_LOG="${BLACKBOARD_DIR}/micromachine.log"
CLASSIFIER_BOT_LOG="${BLACKBOARD_DIR}/micromachine_combined.log"
TELEMETRY_CLEANUP_SNAPSHOT="${BLACKBOARD_DIR}/latest_telemetry.pre_cleanup.json"
RUNTIME_IDENTITY_SNAPSHOT="${BLACKBOARD_DIR}/runtime_identity.snapshot.json"
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
SMOKE_STRATEGY_EVIDENCE_UPDATE_ID="${AGGRESSIVE_UPDATE_ID}"
AGGRESSIVE_PROFILE_PUBLISHED=0
SMOKE_AUTO_AGGRESSIVE_PROFILE="${SMOKE_AUTO_AGGRESSIVE_PROFILE:-1}"
SMOKE_MANUAL_LIVE_MODE="${SMOKE_MANUAL_LIVE_MODE:-0}"
SMOKE_FRESH_LIVE_SESSION="${SMOKE_FRESH_LIVE_SESSION:-0}"
SMOKE_MODULATION_ARCHIVE_START_OFFSET=0
if [[ -z "${SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE:-}" ]]; then
  if [[ "${SMOKE_MANUAL_LIVE_MODE}" == "1" ]]; then
    SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE=0
  else
    SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE=1
  fi
fi
SMOKE_OPERATION_UPDATE_ID="${SMOKE_OPERATION_UPDATE_ID:-smoke-parallel-operations}"
SMOKE_SCOUT_OPERATION_ID="${SMOKE_SCOUT_OPERATION_ID:-smoke-scout-alpha}"
SMOKE_ATTACK_OPERATION_ID="${SMOKE_ATTACK_OPERATION_ID:-smoke-attack-bravo}"
SMOKE_RESTORE_UPDATE_ID="${SMOKE_RESTORE_UPDATE_ID:-}"
OPERATION_LIFECYCLE_PHASE="pending"
OPERATION_CANCEL_ISSUED_FRAME=0
OPERATION_RESTORE_ISSUED_FRAME=0
OPERATION_CANCEL_TELEMETRY_FRAME=0
OPERATION_CANCEL_MAIN_ATTACK_COMMAND_BASELINE=0
OPERATION_CANCEL_MAIN_ATTACK_ACTION_FRAME_BASELINE=0
OPERATION_CANCEL_MAIN_ATTACK_HOME_DISTANCE_BASELINE=0
OPERATION_CANCEL_MAIN_ATTACK_UNIT_SAMPLES='[]'
OPERATION_CANCEL_OWNER_RELEASE_COUNT_BASELINE=0
OPERATION_CANCEL_QUEUE_PURGE_COUNT_BASELINE=0
OPERATION_CANCEL_QUEUE_PURGE_FRAME_BASELINE=0
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
      printf '%s\t%s\t%s\n' "bio_pressure" "bio_facility bio_marauder_techlab bio_ghost_techlab bio_marauder_support starport_transition medivac_drop_support" "Barracks BarracksTechLab Marauder Starport Medivac"
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

reset_promoted_smoke_artifacts() {
  local artifact
  for artifact in \
    latest_telemetry.json \
    latest_telemetry.pre_cleanup.json \
    telemetry.jsonl \
    micromachine_combined.log \
    micromachine.log \
    latest_modulation.json \
    latest_modulation.kv \
    modulation_updates.jsonl \
    runtime_log_start.marker \
    runtime_log_baseline.tsv \
    runtime_identity.snapshot.json \
    smoke_attempts.json
  do
    rm -f "${BLACKBOARD_DIR}/${artifact}"
  done
}

reset_current_smoke_artifacts() {
  mkdir -p "${BLACKBOARD_DIR}"
  rm -f \
    "${BLACKBOARD_DIR}/latest_telemetry.json" \
    "${TELEMETRY_CLEANUP_SNAPSHOT}" \
    "${BLACKBOARD_DIR}/telemetry.jsonl" \
    "${BOT_LOG}" \
    "${CLASSIFIER_BOT_LOG}" \
    "${RUNTIME_LOG_BASELINE}" \
    "${RUNTIME_IDENTITY_SNAPSHOT}" \
    "${BLACKBOARD_DIR}/smoke_attempts.json"
  if [[ "${SMOKE_MANUAL_LIVE_MODE:-0}" != "1" ]]; then
    rm -f \
      "${BLACKBOARD_DIR}/latest_modulation.json" \
      "${BLACKBOARD_DIR}/latest_modulation.kv" \
      "${BLACKBOARD_DIR}/latest_modulation_compile_result.json" \
      "${BLACKBOARD_DIR}/modulation_updates.jsonl"
  fi
}

write_smoke_attempt_status() {
  local attempt_dir="$1"
  local attempt="$2"
  local status="$3"
  python3 - <<'PY' "${attempt_dir}" "${attempt}" "${status}"
import json
import sys
from pathlib import Path

attempt_dir = Path(sys.argv[1])
attempt_dir.mkdir(parents=True, exist_ok=True)
(attempt_dir / "attempt_status.json").write_text(
    json.dumps(
        {
            "attempt": int(sys.argv[2]),
            "status": sys.argv[3],
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
PY
}

write_smoke_attempt_summary() {
  local status="$1"
  local selected_attempt="${2:-0}"
  local stopped_at="${3:-0}"
  local stop_reason="${4:-}"
  python3 - <<'PY' \
    "${BLACKBOARD_DIR}" \
    "${SMOKE_RUN_ROOT}" \
    "${SMOKE_RUN_ID}" \
    "${SMOKE_REPO_HEAD_SHA}" \
    "${SMOKE_MAX_ATTEMPTS}" \
    "${status}" \
    "${selected_attempt}" \
    "${stopped_at}" \
    "${stop_reason}"
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
run_root = Path(sys.argv[2])
run_id = sys.argv[3]
repo_head_sha = sys.argv[4]
max_attempts = int(sys.argv[5])
status = sys.argv[6]
selected_attempt = int(sys.argv[7])
stopped_at = int(sys.argv[8])
stop_reason = sys.argv[9]

snapshot_attempt = selected_attempt or stopped_at
identity_snapshot_path = (
    run_root / f"attempt-{snapshot_attempt}" / "runtime_identity.snapshot.json"
    if snapshot_attempt
    else None
)
build_identity = {}
if identity_snapshot_path is not None and identity_snapshot_path.exists():
    try:
        build_identity = json.loads(identity_snapshot_path.read_text())
    except (json.JSONDecodeError, OSError):
        build_identity = {}
runtime_source_files = build_identity.get("python_runtime_source_files")
runtime_source_file_count = (
    len(runtime_source_files)
    if isinstance(runtime_source_files, list)
    else 0
)

attempts = []
for index in range(1, max_attempts + 1):
    attempt_dir = run_root / f"attempt-{index}"
    attempt_status_path = attempt_dir / "attempt_status.json"
    attempt_status = "not_run"
    if attempt_status_path.exists():
        try:
            attempt_status = str(
                json.loads(attempt_status_path.read_text()).get(
                    "status",
                    "failed",
                )
            )
        except (json.JSONDecodeError, OSError):
            attempt_status = "failed"
    telemetry_path = attempt_dir / "latest_telemetry.json"
    latest_frame = 0
    if telemetry_path.exists():
        try:
            latest_frame = int(
                json.loads(telemetry_path.read_text()).get("frame") or 0
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            latest_frame = 0
    attempts.append(
        {
            "attempt": index,
            "status": attempt_status,
            "latest_frame": latest_frame,
            "dir": str(attempt_dir),
        }
    )

payload = {
    "status": status,
    "ok": status == "passed",
    "run_id": run_id,
    "run_root": str(run_root),
    "repo_head_sha": repo_head_sha,
    "runtime_identity_snapshot": (
        str(identity_snapshot_path) if identity_snapshot_path else None
    ),
    "build_identity_report": build_identity.get("build_identity_report"),
    "build_identity_report_sha256": build_identity.get(
        "build_identity_report_sha256"
    ),
    "build_identity": build_identity.get("build_identity"),
    "build_schema_version": build_identity.get("build_schema_version"),
    "micromachine_source_state_sha256": build_identity.get(
        "micromachine_source_state_sha256"
    ),
    "binary": build_identity.get("binary"),
    "binary_sha256": build_identity.get("binary_sha256"),
    "smoke_script": build_identity.get("smoke_script"),
    "smoke_script_sha256": build_identity.get("smoke_script_sha256"),
    "python_runtime_source_identity": build_identity.get(
        "python_runtime_source_identity"
    ),
    "python_runtime_source_file_count": runtime_source_file_count,
    "max_attempts": max_attempts,
    "selected_attempt": selected_attempt or None,
    "selected_attempt_dir": (
        str(run_root / f"attempt-{selected_attempt}")
        if selected_attempt
        else None
    ),
    "stopped_at_attempt": stopped_at or None,
    "stop_reason": stop_reason or None,
    "attempts": attempts,
}
(root / "smoke_attempts.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n"
)
PY
}

if [[ -z "${SMOKE_ATTEMPT_INDEX}" && "${SMOKE_MAX_ATTEMPTS}" -gt 1 ]]; then
  mkdir -p "${BLACKBOARD_DIR}"
  reset_promoted_smoke_artifacts
  SMOKE_RUN_ROOT="${BLACKBOARD_DIR}/runs/${SMOKE_RUN_ID}"
  mkdir -p "${SMOKE_RUN_ROOT}"
  for (( attempt = 1; attempt <= SMOKE_MAX_ATTEMPTS; attempt++ )); do
    attempt_dir="${SMOKE_RUN_ROOT}/attempt-${attempt}"
    echo "Starting MicroMachine smoke attempt ${attempt}/${SMOKE_MAX_ATTEMPTS}: ${attempt_dir}"
    if SMOKE_ATTEMPT_INDEX="${attempt}" SMOKE_MAX_ATTEMPTS=1 SMOKE_RUN_ID="${SMOKE_RUN_ID}" SMOKE_REPO_HEAD_SHA="${SMOKE_REPO_HEAD_SHA}" BLACKBOARD_DIR="${attempt_dir}" "${BASH_SOURCE[0]}"; then
      write_smoke_attempt_status "${attempt_dir}" "${attempt}" "passed"
      if [[ -f "${attempt_dir}/latest_telemetry.json" ]]; then
        cp -p "${attempt_dir}/latest_telemetry.json" "${BLACKBOARD_DIR}/latest_telemetry.json"
      fi
      if [[ -f "${attempt_dir}/telemetry.jsonl" ]]; then
        cp -p "${attempt_dir}/telemetry.jsonl" "${BLACKBOARD_DIR}/telemetry.jsonl"
      fi
      if [[ -f "${attempt_dir}/micromachine_combined.log" ]]; then
        cp -p "${attempt_dir}/micromachine_combined.log" "${BLACKBOARD_DIR}/micromachine_combined.log"
      fi
      write_smoke_attempt_summary "passed" "${attempt}" "${attempt}" ""
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
      write_smoke_attempt_status "${attempt_dir}" "${attempt}" "non_retryable_failure"
      write_smoke_attempt_summary "failed" "0" "${attempt}" "non_retryable_failure"
      echo "MicroMachine smoke stopped after non-retryable attempt ${attempt}; summary: ${BLACKBOARD_DIR}/smoke_attempts.json" >&2
      exit 1
    fi

    write_smoke_attempt_status "${attempt_dir}" "${attempt}" "retryable_startup_failure"
    if (( attempt < SMOKE_MAX_ATTEMPTS )); then
      echo "MicroMachine smoke retrying after retryable frame-0 startup failure; settling ${SMOKE_RETRY_SETTLE_SECONDS}s before attempt $((attempt + 1))/${SMOKE_MAX_ATTEMPTS}." >&2
      sleep "${SMOKE_RETRY_SETTLE_SECONDS}"
    fi
  done

  write_smoke_attempt_summary "failed" "0" "${SMOKE_MAX_ATTEMPTS}" "retryable_startup_failure_exhausted"
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

snapshot_latest_telemetry_for_cleanup() {
  python3 - <<'PY' \
    "${BLACKBOARD_DIR}/latest_telemetry.json" \
    "${BLACKBOARD_DIR}/telemetry.jsonl" \
    "${TELEMETRY_CLEANUP_SNAPSHOT}"
import json
import os
import sys
import time
from pathlib import Path

latest = Path(sys.argv[1])
archive = Path(sys.argv[2])
snapshot = Path(sys.argv[3])
payload = None
for _ in range(20):
    try:
        candidate = json.loads(latest.read_text())
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        time.sleep(0.02)
        continue
    if isinstance(candidate, dict):
        payload = candidate
        break

if payload is None and archive.exists():
    try:
        with archive.open() as handle:
            for line in handle:
                try:
                    candidate = json.loads(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if isinstance(candidate, dict):
                    payload = candidate
    except OSError:
        pass

if payload is None:
    raise SystemExit(0)

snapshot.parent.mkdir(parents=True, exist_ok=True)
temporary = snapshot.with_name(snapshot.name + ".tmp")
temporary.write_text(
    json.dumps(payload, separators=(",", ":")) + "\n"
)
os.replace(temporary, snapshot)
PY
}

restore_latest_telemetry_after_cleanup() {
  python3 - <<'PY' \
    "${TELEMETRY_CLEANUP_SNAPSHOT}" \
    "${BLACKBOARD_DIR}/latest_telemetry.json"
import os
import sys
from pathlib import Path

snapshot = Path(sys.argv[1])
latest = Path(sys.argv[2])
if snapshot.exists() and snapshot.stat().st_size > 0:
    os.replace(snapshot, latest)
PY
}

cleanup_runtime() {
  snapshot_latest_telemetry_for_cleanup

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

  restore_latest_telemetry_after_cleanup
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

has_expected_strategy_profile_evidence() {
  local strategy_evidence_update_id="${1:-${SMOKE_STRATEGY_EVIDENCE_UPDATE_ID}}"
  [[ -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]] || return 1
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - \
    "${BLACKBOARD_DIR}/latest_telemetry.json" \
    "${strategy_evidence_update_id}" \
    "${SMOKE_EXPECTED_STRATEGY_DOCTRINE}" \
    "${SMOKE_EXPECTED_PRODUCTION_ACTIONS}" \
    "${SMOKE_EXPECTED_PRODUCTION_ITEMS}" <<'PY'
import json
import sys
from pathlib import Path

from starcraft_commander.micromachine_production_evidence import (
    expected_production_pairs,
    find_causal_production_evidence,
)

telemetry = Path(sys.argv[1])
update_id = sys.argv[2]
doctrine = sys.argv[3]
expected_actions = {value for value in sys.argv[4].split() if value}
expected_items = {value for value in sys.argv[5].split() if value}
if not expected_actions and not expected_items:
    raise SystemExit(0)

entries = []
try:
    payload = json.loads(telemetry.read_text())
except (json.JSONDecodeError, OSError):
    raise SystemExit(1)
if isinstance(payload, dict):
    entries.append(payload)
archive = telemetry.with_name("telemetry.jsonl")
if archive.exists():
    for line in archive.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)

expected_pairs = expected_production_pairs(
    doctrine,
    expected_actions=expected_actions,
    expected_items=expected_items,
)
causal_evidence = find_causal_production_evidence(
    entries,
    expected_doctrine=doctrine,
    expected_update_id=update_id,
    expected_pairs=expected_pairs,
)
raise SystemExit(0 if causal_evidence.matched else 1)
PY
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
    build_manual_live_autonomy_profile,
    build_micromachine_strategy_profile,
)

directory, profile_name, update_id, frame_text = sys.argv[1:5]
backend = MicroMachineFilesystemBlackboard(directory)
if profile_name == "manual_live_autonomy":
    vector = build_manual_live_autonomy_profile()
elif profile_name == "aggressive_pressure":
    profile_name = "bio_pressure"
    vector = build_micromachine_strategy_profile(profile_name)
else:
    vector = build_micromachine_strategy_profile(profile_name)
backend.publish_vector(vector, current_frame=int(frame_text), update_id=update_id)
PY
}

publish_parallel_operations() {
  local frame="$1"
  local terminal_state="$2"
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' \
    "${BLACKBOARD_DIR}" \
    "${SMOKE_OPERATION_UPDATE_ID}" \
    "${SMOKE_SCOUT_OPERATION_ID}" \
    "${SMOKE_ATTACK_OPERATION_ID}" \
    "${frame}" \
    "${terminal_state}"
import sys
from pathlib import Path

from starcraft_commander.micromachine_runtime import (
    MicroMachineFilesystemBlackboard,
)
from starcraft_commander.policy_modulation import PolicyModulationVector

directory = Path(sys.argv[1])
update_id, scout_id, attack_id = sys.argv[2:5]
frame = int(sys.argv[5])
terminal_state = sys.argv[6]
cancelled = terminal_state == "cancelled"

def operation(operation_id, task_type, location, route_type, target_type):
    operation_cancelled = cancelled and operation_id == attack_id
    attack_operation = task_type == "pressure_with_main_army"
    persistent_until_cancelled = task_type in {
        "scout_with_units",
        "pressure_with_main_army",
    }
    lifetime = {
        "mode": (
            "until_cancelled"
            if operation_cancelled or persistent_until_cancelled
            else "until_completed"
        ),
        "completion_conditions": (
            ["cancelled_by_user"]
            if operation_cancelled or persistent_until_cancelled
            else ["target_reached"]
        ),
        "completion_state": (
            "cancelled" if operation_cancelled else "active"
        ),
        "reason": (
            "difficulty_smoke_selective_attack_cancel"
            if operation_cancelled
            else ""
        ),
    }
    return {
        "operation_id": operation_id,
        "goal": f"difficulty smoke {task_type}",
        "generation": 1,
        "issued_at_frame": frame,
        "command_layer": "operation",
        "tactical_task": {
            "task_type": task_type,
            "location_intent": location,
            "production_targets": (
                ["TERRAN_MARINE", "TERRAN_SIEGETANK"]
                if task_type == "pressure_with_main_army"
                else ["TERRAN_MARINE"]
            ),
            "priority": 0.95,
            "min_units": 1,
            "max_units": 1,
            "duration_seconds": 300,
            "allow_partial": attack_operation,
        },
        "scope": {
            "army_group": "scout" if task_type == "scout_with_units" else "harass",
            "location_intent": location,
            "min_units": 1,
            "max_units": 1,
            "allow_partial_scope": attack_operation,
        },
        "composition_requirements": (
            [
                {
                    "unit_type": "TERRAN_MARINE",
                    "count": 4,
                    "role": "frontline",
                },
                {
                    "unit_type": "TERRAN_SIEGETANK",
                    "count": 4,
                    "role": "siege_support",
                }
            ]
            if attack_operation
            else [
                {
                    "unit_type": "TERRAN_MARINE",
                    "count": 3,
                    "role": "scout",
                }
            ]
        ),
        "lifetime": lifetime,
        "route_intent": {
            "route_type": route_type,
            "avoid_enemy_strength": task_type == "scout_with_units",
        },
        "target_intent": {"target_type": target_type, "priority": 0.9},
    }

vector = PolicyModulationVector.from_mapping(
    {
        "goal": "difficulty smoke parallel scout and attack lifecycle",
        "source": "smoke_keyword",
        "override_level": "directive",
        "command_layer": "operation",
        "confidence": 1.0,
        "ttl_seconds": 300,
        "operations": [
            operation(
                scout_id,
                "scout_with_units",
                "enemy_main",
                "safe_path",
                "enemy_main",
            ),
            operation(
                attack_id,
                "pressure_with_main_army",
                "enemy_natural",
                "direct",
                "army",
            ),
        ],
        "tags": ["difficulty_smoke", "parallel_operations", terminal_state],
    }
)
backend = MicroMachineFilesystemBlackboard(directory)
backend.publish_vector(vector, current_frame=frame, update_id=update_id)
PY
}

operation_restore_ready() {
  local restore_update_id="$1"
  local restore_frame="$2"
  local command_baseline="$3"
  local action_frame_baseline="$4"
  local home_distance_baseline="$5"
  local unit_samples_baseline="$6"
  [[ -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]] || return 1
  python3 - <<'PY' \
    "${BLACKBOARD_DIR}/latest_telemetry.json" \
    "${restore_update_id}" \
    "${restore_frame}" \
    "${command_baseline}" \
    "${action_frame_baseline}" \
    "${home_distance_baseline}" \
    "${unit_samples_baseline}" \
    "${SMOKE_MIN_MAIN_ATTACK_HOME_DISTANCE:-12.0}"
import json
import os
import sys
from pathlib import Path

telemetry = Path(sys.argv[1])
restore_update_id = sys.argv[2]
restore_frame = int(sys.argv[3])
command_baseline = int(sys.argv[4])
action_frame_baseline = int(sys.argv[5])
home_distance_baseline = float(sys.argv[6])
try:
    baseline_samples = json.loads(sys.argv[7])
except (json.JSONDecodeError, TypeError, ValueError):
    raise SystemExit(1)
minimum_home_distance = float(sys.argv[8])
minimum_displacement = float(
    os.environ.get(
        "SMOKE_MIN_POST_CANCEL_MAIN_ATTACK_DISPLACEMENT",
        "4.0",
    )
)

def unit_positions(samples):
    if not isinstance(samples, list):
        return {}
    positions = {}
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        try:
            tag = int(sample.get("tag", 0) or 0)
            x = float(sample.get("x", 0.0) or 0.0)
            y = float(sample.get("y", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if tag > 0:
            positions[tag] = (x, y)
    return positions

baseline_positions = unit_positions(baseline_samples)
restore_initial_positions = {}

entries = []
archive = telemetry.with_name("telemetry.jsonl")
if archive.exists():
    for line in archive.read_text().splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
try:
    latest = json.loads(telemetry.read_text())
except (json.JSONDecodeError, ValueError):
    latest = None
if isinstance(latest, dict):
    entries.append(latest)
if not entries:
    raise SystemExit(1)

restore_consumed_frames = []
for entry in entries:
    frame = int(entry.get("frame", 0) or 0)
    if frame < restore_frame:
        continue
    commander = entry.get("managers", {}).get("GameCommander", {})
    if (
        isinstance(commander, dict)
        and commander.get("policy_active") is True
        and str(commander.get("update_id", "") or "") == restore_update_id
    ):
        restore_consumed_frames.append(frame)
if not restore_consumed_frames:
    raise SystemExit(1)
first_restore_consumed_frame = min(restore_consumed_frames)

for entry in entries:
    frame = int(entry.get("frame", 0) or 0)
    if frame < first_restore_consumed_frame:
        continue
    managers = entry.get("managers", {})
    if not isinstance(managers, dict):
        continue
    commander = managers.get("GameCommander", {})
    combat = managers.get("CombatCommander", {})
    if not isinstance(commander, dict) or not isinstance(combat, dict):
        continue
    if str(commander.get("update_id", "") or "") != restore_update_id:
        continue
    if commander.get("policy_active") is not True:
        continue
    command_count = int(
        combat.get("main_attack_actual_command_issued_count", 0) or 0
    )
    command_frame = int(combat.get("main_attack_last_action_frame", 0) or 0)
    command = str(combat.get("main_attack_last_issued_action", "") or "")
    current_home_distance = float(
        combat.get("main_attack_home_distance", 0.0) or 0.0
    )
    post_cancel_displacement = abs(
        current_home_distance - home_distance_baseline
    )
    current_positions = unit_positions(
        combat.get("main_attack_unit_samples", [])
    )
    for tag, position in current_positions.items():
        restore_initial_positions.setdefault(tag, position)
    reference_positions = dict(restore_initial_positions)
    reference_positions.update(baseline_positions)
    same_tag_displacements = {
        tag: (
            (
                current_positions[tag][0] - baseline_position[0]
            )
            ** 2
            + (
                current_positions[tag][1] - baseline_position[1]
            )
            ** 2
        )
        ** 0.5
        for tag, baseline_position in reference_positions.items()
        if tag in current_positions
    }
    maximum_same_tag_displacement = max(
        same_tag_displacements.values(),
        default=0.0,
    )
    unit_count = int(combat.get("main_attack_unit_count", 0) or 0)
    minimum_units = int(
        combat.get("main_attack_scope_min_units", 1) or 1
    )
    if (
        command_count > command_baseline
        and command_frame > action_frame_baseline
        and command_frame >= first_restore_consumed_frame
        and frame >= command_frame
        and str(combat.get("main_attack_order_status", "") or "") == "Attack"
        and "squad=MainAttack" in command
        and unit_count >= minimum_units
        and current_home_distance >= minimum_home_distance
        and maximum_same_tag_displacement >= minimum_displacement
    ):
        raise SystemExit(0)
raise SystemExit(1)
PY
}

operation_production_baseline() {
  [[ -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]] || return 1
  python3 - <<'PY' \
    "${BLACKBOARD_DIR}/latest_telemetry.json" \
    "${SMOKE_ATTACK_OPERATION_ID}"
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text())
except (json.JSONDecodeError, OSError, TypeError, ValueError):
    raise SystemExit(1)
production = payload.get("managers", {}).get("ProductionManager", {})
if not isinstance(production, dict):
    raise SystemExit(1)
owned_items = production.get("operation_owned_queue_items", [])
if not isinstance(owned_items, list):
    raise SystemExit(1)
attack_owner = (sys.argv[2], 1)
purgeable_attack_claim = any(
    isinstance(item, dict)
    and item.get("exclusive_operation_owned") is True
    and item.get("preserve_without_operation_owners") is False
    and any(
        isinstance(owner, dict)
        and (
            str(owner.get("operation_id", "") or ""),
            int(owner.get("generation", 0) or 0),
        )
        == attack_owner
        for owner in item.get("owners", [])
        if isinstance(item.get("owners"), list)
    )
    for item in owned_items
)
if not purgeable_attack_claim:
    raise SystemExit(1)
values = (
    production.get("operation_production_owner_release_count"),
    production.get("operation_production_queue_purge_count"),
    production.get("last_operation_production_queue_purge_frame"),
)
if any(type(value) is not int or value < 0 for value in values):
    raise SystemExit(1)
print(*values, sep="\t")
PY
}

operation_lifecycle_ready() {
  local expected_phase="$1"
  local cancel_frame="$2"
  local owner_release_count_baseline="${3:-0}"
  local queue_purge_count_baseline="${4:-0}"
  local queue_purge_frame_baseline="${5:-0}"
  [[ -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]] || return 1
  python3 - <<'PY' \
    "${BLACKBOARD_DIR}/latest_telemetry.json" \
    "${SMOKE_OPERATION_UPDATE_ID}" \
    "${SMOKE_SCOUT_OPERATION_ID}" \
    "${SMOKE_ATTACK_OPERATION_ID}" \
    "${expected_phase}" \
    "${cancel_frame}" \
    "${owner_release_count_baseline}" \
    "${queue_purge_count_baseline}" \
    "${queue_purge_frame_baseline}"
import json
import sys
from pathlib import Path

telemetry = Path(sys.argv[1])
update_id, scout_id, attack_id, phase = sys.argv[2:6]
cancel_frame = int(sys.argv[6])
owner_release_count_baseline = int(sys.argv[7])
queue_purge_count_baseline = int(sys.argv[8])
queue_purge_frame_baseline = int(sys.argv[9])
emit_cancel_baseline = phase == "cancelled_baseline"
if emit_cancel_baseline:
    phase = "cancelled"
expected_ids = (scout_id, attack_id)
thresholds = {scout_id: 8.0, attack_id: 12.0}
expected_generation = 1

def operation_owner_present(
    queue_items,
    operation_id,
    generation,
    expected_item=None,
    require_purgeable=False,
):
    if not isinstance(queue_items, list):
        return False
    for queue_item in queue_items:
        if not isinstance(queue_item, dict):
            continue
        item_name = str(queue_item.get("item", "") or "")
        if expected_item is not None and item_name != expected_item:
            continue
        if require_purgeable and (
            queue_item.get("exclusive_operation_owned") is not True
            or queue_item.get("preserve_without_operation_owners") is not False
        ):
            continue
        owners = queue_item.get("owners", [])
        if not isinstance(owners, list):
            continue
        if any(
            isinstance(owner, dict)
            and str(owner.get("operation_id", "") or "") == operation_id
            and int(owner.get("generation", 0) or 0) == generation
            for owner in owners
        ):
            return True
    return False

entries = []
archive = telemetry.with_name("telemetry.jsonl")
if archive.exists():
    for line in archive.read_text().splitlines():
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(item, dict):
            entries.append(item)
try:
    latest = json.loads(telemetry.read_text())
except (json.JSONDecodeError, ValueError):
    latest = None
if isinstance(latest, dict):
    entries.append(latest)
if not entries:
    raise SystemExit(1)

if phase == "active":
    evidence = {
        operation_id: {"assigned": False, "submitted": False, "movement": False}
        for operation_id in expected_ids
    }
    exclusive_assignment_observed = False
    live_production_owner_observed = False
    for entry in entries:
        managers = entry.get("managers", {})
        if not isinstance(managers, dict):
            continue
        director = managers.get("OperationDirector", {})
        if not isinstance(director, dict):
            continue
        if str(director.get("policy_update_id", "") or "") != update_id:
            continue
        production = managers.get("ProductionManager", {})
        live_production_owner = (
            isinstance(production, dict)
            and str(production.get("policy_update_id", "") or "") == update_id
            and operation_owner_present(
                production.get("operation_owned_queue_items", []),
                attack_id,
                expected_generation,
                require_purgeable=True,
            )
        )
        if live_production_owner:
            live_production_owner_observed = True
        operations = director.get("operations", [])
        if not isinstance(operations, list):
            continue
        current_assignments = {}
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operation_id", "") or "")
            if operation_id not in evidence:
                continue
            if int(operation.get("generation", 0) or 0) != expected_generation:
                continue
            tags = operation.get("assigned_unit_tags", [])
            assigned_count = int(operation.get("assigned_count", 0) or 0)
            assigned_frame = int(operation.get("assigned_frame", 0) or 0)
            assignment_valid = (
                isinstance(tags, list)
                and assigned_count > 0
                and assigned_frame > 0
                and assigned_count == len(tags)
                and len(set(tags)) == len(tags)
            )
            if assignment_valid:
                evidence[operation_id]["assigned"] = True
                current_assignments[operation_id] = set(tags)
            submitted = (
                operation.get("submission_observed") is True
                and int(operation.get("submitted_frame", 0) or 0) > 0
            )
            if submitted:
                evidence[operation_id]["submitted"] = True
            distance = float(operation.get("max_home_distance", 0.0) or 0.0)
            engagement_observed = (
                operation.get("engaged") is True
                and int(operation.get("last_action_frame", 0) or 0) > 0
            )
            if (
                distance >= thresholds[operation_id]
                or engagement_observed
            ):
                evidence[operation_id]["movement"] = True
        if all(operation_id in current_assignments for operation_id in expected_ids):
            assigned_tags = [
                current_assignments[operation_id]
                for operation_id in expected_ids
            ]
            all_tags = set().union(*assigned_tags)
            if (
                assigned_tags[0].isdisjoint(assigned_tags[1])
                and int(director.get("owned_unit_count", -1)) == len(all_tags)
            ):
                exclusive_assignment_observed = True
    raise SystemExit(
        0
        if (
            exclusive_assignment_observed
            and live_production_owner_observed
            and all(all(signals.values()) for signals in evidence.values())
        )
        else 1
    )

if phase != "cancelled":
    raise SystemExit(1)

reconciled_cancelled = None
for entry in entries:
    frame = int(entry.get("frame", 0) or 0)
    if frame <= cancel_frame:
        continue
    managers = entry.get("managers", {})
    if not isinstance(managers, dict):
        continue
    director = managers.get("OperationDirector", {})
    if not isinstance(director, dict):
        continue
    if str(director.get("policy_update_id", "") or "") != update_id:
        continue
    operations = director.get("operations", [])
    if not isinstance(operations, list):
        continue
    by_id = {
        str(operation.get("operation_id", "") or ""): operation
        for operation in operations
        if (
            isinstance(operation, dict)
            and int(operation.get("generation", 0) or 0)
                == expected_generation
        )
    }
    if not all(operation_id in by_id for operation_id in expected_ids):
        continue
    attack_operation = by_id[attack_id]
    attack_tags = attack_operation.get("assigned_unit_tags")
    if (
        str(attack_operation.get("status", "") or "").upper()
            != "CANCELLED"
        or attack_operation.get("completed") is not True
        or int(attack_operation.get("assigned_count", -1)) != 0
        or not isinstance(attack_tags, list)
        or attack_tags
    ):
        continue
    scout_operation = by_id[scout_id]
    scout_tags = scout_operation.get("assigned_unit_tags")
    scout_status = str(scout_operation.get("status", "") or "").upper()
    if (
        scout_operation.get("completed") is True
        or scout_status
            in {
                "CANCELLED",
                "COMPLETED",
                "EXPIRED",
                "FAILED",
                "SUPERSEDED",
            }
        or not isinstance(scout_tags, list)
        or int(scout_operation.get("assigned_count", -1))
            != len(scout_tags)
        or not scout_tags
        or int(director.get("owned_unit_count", -1))
            != len(set(scout_tags))
    ):
        continue

    production = managers.get("ProductionManager", {})
    if not isinstance(production, dict):
        continue
    operation_owned_queue_item_count = production.get(
        "operation_owned_queue_item_count"
    )
    owner_release_count = production.get(
        "operation_production_owner_release_count"
    )
    queue_purge_count = production.get(
        "operation_production_queue_purge_count"
    )
    purge_events = production.get(
        "operation_production_queue_purge_events",
        [],
    )
    if not isinstance(purge_events, list):
        continue
    matching_purge_event = None
    for purge_event in purge_events:
        if not isinstance(purge_event, dict):
            continue
        purge_event_generation = purge_event.get("generation")
        purge_event_frame = purge_event.get("frame")
        purge_event_removed_count = purge_event.get("removed_count")
        if (
            str(purge_event.get("operation_id", "") or "") == attack_id
            and type(purge_event_generation) is int
            and purge_event_generation == expected_generation
            and type(purge_event_frame) is int
            and purge_event_frame >= cancel_frame
            and purge_event_frame > queue_purge_frame_baseline
            and type(purge_event_removed_count) is int
            and purge_event_removed_count > 0
        ):
            matching_purge_event = purge_event
            break
    operation_owned_queue_items = production.get(
        "operation_owned_queue_items",
        [],
    )
    if (
        type(operation_owned_queue_item_count) is not int
        or operation_owned_queue_item_count < 0
        or operation_owner_present(
            operation_owned_queue_items,
            attack_id,
            expected_generation,
        )
        or type(owner_release_count) is not int
        or owner_release_count <= owner_release_count_baseline
        or type(queue_purge_count) is not int
        or queue_purge_count <= queue_purge_count_baseline
        or matching_purge_event is None
    ):
        continue
    candidate = (frame, director, by_id, production, entry)
    if reconciled_cancelled is None or frame < reconciled_cancelled[0]:
        reconciled_cancelled = candidate

if reconciled_cancelled is None:
    raise SystemExit(1)
cancelled_frame, director, by_id, cancelled_production, cancelled_entry = (
    reconciled_cancelled
)
if emit_cancel_baseline:
    combat = cancelled_entry.get("managers", {}).get("CombatCommander", {})
    if not isinstance(combat, dict):
        combat = None
    if combat is None:
        raise SystemExit(1)
    unit_samples = combat.get("main_attack_unit_samples", [])
    if not isinstance(unit_samples, list):
        unit_samples = []
    print(
        cancelled_frame,
        int(combat.get("main_attack_actual_command_issued_count", 0) or 0),
        int(combat.get("main_attack_last_action_frame", 0) or 0),
        float(combat.get("main_attack_home_distance", 0.0) or 0.0),
        json.dumps(unit_samples, separators=(",", ":")),
        sep="\t",
    )
raise SystemExit(0)
PY
}

preserve_existing_live_modulation() {
  local frame="$1"
  # In manual live mode the web/voice cockpit may publish a user tactical command
  # before the SC2 runtime is launched. Preserve that command instead of replacing
  # it with the smoke-only defensive hold bootstrap profile.
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' "${BLACKBOARD_DIR}" "${frame}"
import json
import sys
from pathlib import Path

from starcraft_commander.micromachine_bridge import MicroMachineBlackboardUpdate
from starcraft_commander.micromachine_runtime import MicroMachineFilesystemBlackboard
from starcraft_commander.policy_modulation import PolicyModulationVector

directory = Path(sys.argv[1])
frame = int(sys.argv[2])
latest_path = directory / "latest_modulation.json"
if not latest_path.exists():
    raise SystemExit(1)
try:
    payload = json.loads(latest_path.read_text())
except Exception:
    raise SystemExit(1)

update_id = str(payload.get("update_id", "")).strip()
if not update_id or update_id.startswith(("smoke-", "soak-")):
    raise SystemExit(1)

vector_payload = payload.get("vector")
if not isinstance(vector_payload, dict):
    raise SystemExit(1)

source = str(vector_payload.get("source", "")).strip().lower()
raw_tags = vector_payload.get("tags", [])
tags = {
    str(tag).strip()
    for tag in raw_tags
    if isinstance(tag, str) and tag.strip()
}
tactical_task = vector_payload.get("tactical_task", {})
if not isinstance(tactical_task, dict):
    tactical_task = {}
scope = vector_payload.get("scope", {})
if not isinstance(scope, dict):
    scope = {}
combat = vector_payload.get("combat", {})
if not isinstance(combat, dict):
    combat = {}

live_tags = {
    "live_text",
    "keyword_provider",
    "scout_with_units",
    "aggressive_pressure",
    "marine_rush",
}
task_type = str(tactical_task.get("task_type", "")).strip()
task_location = str(tactical_task.get("location_intent", "")).strip()
scope_location = str(scope.get("location_intent", "")).strip()
attack_override = str(combat.get("attack_condition_override", "")).strip()
has_live_intent = (
    source == "ui"
    or bool(tags & live_tags)
    or task_type in {"pressure_with_main_army", "scout_with_units"}
    or bool(task_location)
    or bool(scope_location)
    or attack_override in {"earlier_if_safe", "force_when_threshold_met"}
)
if not has_live_intent:
    raise SystemExit(1)

vector = PolicyModulationVector.from_mapping(vector_payload)
update = MicroMachineBlackboardUpdate(
    update_id=update_id,
    vector=vector,
    issued_at_frame=frame,
    rollback_update_id=payload.get("rollback_update_id"),
)
MicroMachineFilesystemBlackboard(directory).publish_update(update, current_frame=frame)
print(update_id)
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
import time
from pathlib import Path

path = Path(sys.argv[1])
for _ in range(8):
    try:
        frame = int(json.loads(path.read_text()).get("frame", 0))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        time.sleep(0.05)
        continue
    print(frame)
    raise SystemExit(0)
raise SystemExit(1)
PY
}

operation_publish_ready() {
  local strategy_update_id="$1"
  local minimum_frame="$2"
  [[ -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]] || return 1
  python3 - <<'PY' \
    "${BLACKBOARD_DIR}/latest_telemetry.json" \
    "${strategy_update_id}" \
    "${minimum_frame}"
import json
import sys
from pathlib import Path

telemetry_path = Path(sys.argv[1])
strategy_update_id = sys.argv[2]
minimum_frame = int(sys.argv[3])
try:
    payload = json.loads(telemetry_path.read_text())
    frame = int(payload.get("frame", 0) or 0)
    managers = payload.get("managers", {})
    commander = managers.get("GameCommander", {})
    combat = managers.get("CombatCommander", {})
    combat_unit_count = int(combat.get("combat_unit_count", 0) or 0)
except (AttributeError, json.JSONDecodeError, OSError, TypeError, ValueError):
    raise SystemExit(1)
if (
    frame >= minimum_frame
    and commander.get("policy_active") is True
    and commander.get("update_id") == strategy_update_id
    and combat_unit_count >= 2
):
    raise SystemExit(0)
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

reset_current_smoke_artifacts
prepare_launch_contract
verify_build_identity
SC2_RUNTIME_ROOT="$(prepare_sc2_runtime_root)"
if [[ "${SC2_EXECUTABLE}" == "${SC2_BATTLENET_EXECUTABLE}" && -z "${VOI_SC2_EXTRA_ARGS:-}" ]]; then
  VOI_SC2_EXTRA_ARGS="--game=${SC2_BATTLENET_GAME} --gamepath=${SC2_RUNTIME_ROOT}/"
elif [[ -z "${VOI_SC2_EXTRA_ARGS:-}" && "${SC2_USE_RUNTIME_DIR_ARGS}" == "1" ]]; then
  VOI_SC2_EXTRA_ARGS="-dataDir ${SC2_RUNTIME_ROOT} -tempDir ${SC2_TEMP_DIR}"
fi

snapshot_runtime_identity
if [[ "${SMOKE_MANUAL_LIVE_MODE}" == "1" && "${SMOKE_FRESH_LIVE_SESSION}" == "1" ]]; then
  rm -f \
    "${BLACKBOARD_DIR}/latest_modulation.json" \
    "${BLACKBOARD_DIR}/latest_modulation.kv" \
    "${BLACKBOARD_DIR}/latest_modulation_compile_result.json"
  echo "MicroMachine fresh live session cleared detached tactical command state."
fi
if [[ -f "${BLACKBOARD_DIR}/modulation_updates.jsonl" ]]; then
  SMOKE_MODULATION_ARCHIVE_START_OFFSET="$(
    wc -c <"${BLACKBOARD_DIR}/modulation_updates.jsonl" |
      tr -d '[:space:]'
  )"
fi
touch "${RUNTIME_LOG_MARKER}"
record_runtime_log_baseline
if [[ "${SMOKE_MANUAL_LIVE_MODE}" == "1" ]]; then
  if preserved_update_id="$(preserve_existing_live_modulation "0")"; then
    DEFENSIVE_UPDATE_ID="${preserved_update_id}"
    AGGRESSIVE_UPDATE_ID="${preserved_update_id}"
    SMOKE_ACTIVE_STRATEGY_UPDATE_ID="${preserved_update_id}"
    SMOKE_STRATEGY_EVIDENCE_UPDATE_ID="${preserved_update_id}"
    AGGRESSIVE_PROFILE_PUBLISHED=1
    echo "MicroMachine manual live mode preserved existing tactical blackboard command: ${preserved_update_id}"
  else
    DEFENSIVE_UPDATE_ID="smoke-manual-live-autonomy"
    AGGRESSIVE_UPDATE_ID="${DEFENSIVE_UPDATE_ID}"
    SMOKE_ACTIVE_STRATEGY_UPDATE_ID="${DEFENSIVE_UPDATE_ID}"
    SMOKE_STRATEGY_EVIDENCE_UPDATE_ID="${DEFENSIVE_UPDATE_ID}"
    publish_profile "manual_live_autonomy" "${DEFENSIVE_UPDATE_ID}" "0"
  fi
else
  SMOKE_ACTIVE_STRATEGY_UPDATE_ID="$(smoke_strategy_update_id "${SMOKE_STRATEGY_PROFILE_NAME}" "0")"
  AGGRESSIVE_UPDATE_ID="${SMOKE_ACTIVE_STRATEGY_UPDATE_ID}"
  DEFENSIVE_UPDATE_ID="${SMOKE_ACTIVE_STRATEGY_UPDATE_ID}"
  SMOKE_STRATEGY_EVIDENCE_UPDATE_ID="${SMOKE_ACTIVE_STRATEGY_UPDATE_ID}"
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
config["SC2API"]["EnemyDifficulty"] = int(os.environ["SMOKE_ENEMY_DIFFICULTY"])
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
    if [[ -z "${current_telemetry_frame}" ]]; then
      sleep 1
      continue
    fi
    if has_no_start_units_bootstrap_blocker; then
      cleanup_runtime
      print_no_start_units_bootstrap_blocker
      print_bot_logs
      exit 1
    fi
    if [[ "${SMOKE_MANUAL_LIVE_MODE}" == "1" && -n "${current_telemetry_frame}" && "${current_telemetry_frame}" -ge "${NO_START_UNITS_FRAME}" ]] && has_required_macro_evidence && has_live_hold_preflight_evidence; then
      print_bot_logs >/dev/null 2>&1
      echo "MicroMachine manual live hold preflight passed; keeping runtime alive for manual DSL commands."
      echo "MicroMachine manual live autonomy active; automatic aggressive smoke profile is disabled."
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
      SMOKE_STRATEGY_EVIDENCE_UPDATE_ID="${SMOKE_ACTIVE_STRATEGY_UPDATE_ID}"
      AGGRESSIVE_PROFILE_PUBLISHED=1
    fi

    if [[ "${SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE}" == "1" && "${OPERATION_LIFECYCLE_PHASE}" == "pending" && "${AGGRESSIVE_PROFILE_PUBLISHED}" -eq 1 ]] && has_required_macro_evidence && operation_publish_ready "${SMOKE_STRATEGY_EVIDENCE_UPDATE_ID}" "${AGGRESSIVE_PROFILE_FRAME}"; then
      publish_parallel_operations "${current_telemetry_frame}" "active"
      OPERATION_LIFECYCLE_PHASE="active"
      echo "MicroMachine difficulty smoke published parallel scout+attack operations as soon as the aggressive profile was consumed with two combat units at frame ${current_telemetry_frame}."
    fi

    if [[ "${SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE}" == "1" && "${OPERATION_LIFECYCLE_PHASE}" == "active" ]] && operation_lifecycle_ready "active" "0"; then
      if ! operation_production_counts="$(operation_production_baseline)"; then
        echo "MicroMachine difficulty smoke could not capture operation production cancellation baselines." >&2
        continue
      fi
      IFS=$'\t' read -r \
        OPERATION_CANCEL_OWNER_RELEASE_COUNT_BASELINE \
        OPERATION_CANCEL_QUEUE_PURGE_COUNT_BASELINE \
        OPERATION_CANCEL_QUEUE_PURGE_FRAME_BASELINE \
        <<<"${operation_production_counts}"
      latest_cancel_frame="$(telemetry_frame || true)"
      if [[ "${latest_cancel_frame}" =~ ^[0-9]+$ ]] && (( latest_cancel_frame > current_telemetry_frame )); then
        current_telemetry_frame="${latest_cancel_frame}"
      fi
      OPERATION_CANCEL_ISSUED_FRAME="${current_telemetry_frame}"
      publish_parallel_operations "${OPERATION_CANCEL_ISSUED_FRAME}" "cancelled"
      OPERATION_LIFECYCLE_PHASE="cancel_pending"
      echo "MicroMachine difficulty smoke observed parallel operation assignment/submission/movement; cancellation upsert published at frame ${OPERATION_CANCEL_ISSUED_FRAME} with release/purge baselines ${OPERATION_CANCEL_OWNER_RELEASE_COUNT_BASELINE}/${OPERATION_CANCEL_QUEUE_PURGE_COUNT_BASELINE}."
    fi

    if [[ "${SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE}" == "1" && "${OPERATION_LIFECYCLE_PHASE}" == "cancel_pending" ]] && cancel_baseline="$(
      operation_lifecycle_ready \
        "cancelled_baseline" \
        "${OPERATION_CANCEL_ISSUED_FRAME}" \
        "${OPERATION_CANCEL_OWNER_RELEASE_COUNT_BASELINE}" \
        "${OPERATION_CANCEL_QUEUE_PURGE_COUNT_BASELINE}" \
        "${OPERATION_CANCEL_QUEUE_PURGE_FRAME_BASELINE}"
    )"; then
      IFS=$'\t' read -r \
        OPERATION_CANCEL_TELEMETRY_FRAME \
        OPERATION_CANCEL_MAIN_ATTACK_COMMAND_BASELINE \
        OPERATION_CANCEL_MAIN_ATTACK_ACTION_FRAME_BASELINE \
        OPERATION_CANCEL_MAIN_ATTACK_HOME_DISTANCE_BASELINE \
        OPERATION_CANCEL_MAIN_ATTACK_UNIT_SAMPLES \
        <<<"${cancel_baseline}"
      OPERATION_RESTORE_ISSUED_FRAME="${OPERATION_CANCEL_TELEMETRY_FRAME}"
      latest_restore_frame="$(telemetry_frame || true)"
      if [[ "${latest_restore_frame}" =~ ^[0-9]+$ ]] && (( latest_restore_frame > OPERATION_RESTORE_ISSUED_FRAME )); then
        OPERATION_RESTORE_ISSUED_FRAME="${latest_restore_frame}"
      fi
      if [[ -z "${SMOKE_RESTORE_UPDATE_ID}" ]]; then
        SMOKE_RESTORE_UPDATE_ID="smoke-${SMOKE_STRATEGY_PROFILE_NAME}-restore-${SMOKE_ATTEMPT_INDEX:-0}-${OPERATION_RESTORE_ISSUED_FRAME}-$$"
      fi
      publish_profile "${SMOKE_STRATEGY_PROFILE_NAME}" "${SMOKE_RESTORE_UPDATE_ID}" "${OPERATION_RESTORE_ISSUED_FRAME}"
      AGGRESSIVE_UPDATE_ID="${SMOKE_RESTORE_UPDATE_ID}"
      SMOKE_ACTIVE_STRATEGY_UPDATE_ID="${SMOKE_RESTORE_UPDATE_ID}"
      OPERATION_LIFECYCLE_PHASE="restore_pending"
      echo "MicroMachine difficulty smoke observed selective attack cancellation, retained scout ownership, and exact attack production-owner release at telemetry frame ${OPERATION_CANCEL_TELEMETRY_FRAME}; post-cancel restore profile published at frame ${OPERATION_RESTORE_ISSUED_FRAME} with MainAttack command baseline ${OPERATION_CANCEL_MAIN_ATTACK_COMMAND_BASELINE}."
    fi

    if [[ "${SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE}" == "1" && "${OPERATION_LIFECYCLE_PHASE}" == "restore_pending" ]] && operation_restore_ready "${SMOKE_RESTORE_UPDATE_ID}" "${OPERATION_RESTORE_ISSUED_FRAME}" "${OPERATION_CANCEL_MAIN_ATTACK_COMMAND_BASELINE}" "${OPERATION_CANCEL_MAIN_ATTACK_ACTION_FRAME_BASELINE}" "${OPERATION_CANCEL_MAIN_ATTACK_HOME_DISTANCE_BASELINE}" "${OPERATION_CANCEL_MAIN_ATTACK_UNIT_SAMPLES}"; then
      OPERATION_LIFECYCLE_PHASE="restored"
      echo "MicroMachine difficulty smoke observed a new post-cancel MainAttack command and live movement under restore update ${SMOKE_RESTORE_UPDATE_ID}."
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
        if [[ "${SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE}" != "1" || "${OPERATION_LIFECYCLE_PHASE}" == "restored" ]] && has_expected_strategy_profile_evidence "${SMOKE_STRATEGY_EVIDENCE_UPDATE_ID}"; then
          if [[ "${SMOKE_KEEP_RUNNING_AFTER_PASS:-0}" != "1" ]]; then
            cleanup_runtime
          fi
          break
        fi
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

if [[ "${SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE}" == "1" ]]; then
  if [[ "${OPERATION_LIFECYCLE_PHASE}" != "restored" ]]; then
    echo "MicroMachine difficulty smoke ended before OperationDirector lifecycle completed: phase=${OPERATION_LIFECYCLE_PHASE}" >&2
    print_bot_logs
    exit 1
  fi
  if ! operation_lifecycle_ready "active" "0"; then
    echo "MicroMachine difficulty smoke missing archived parallel operation assignment/submission/movement evidence." >&2
    print_bot_logs
    exit 1
  fi
  if ! operation_lifecycle_ready "cancelled" "${OPERATION_CANCEL_ISSUED_FRAME}"; then
    echo "MicroMachine difficulty smoke missing CANCELLED terminal ownership-release evidence." >&2
    print_bot_logs
    exit 1
  fi
  if ! operation_restore_ready "${SMOKE_RESTORE_UPDATE_ID}" "${OPERATION_RESTORE_ISSUED_FRAME}" "${OPERATION_CANCEL_MAIN_ATTACK_COMMAND_BASELINE}" "${OPERATION_CANCEL_MAIN_ATTACK_ACTION_FRAME_BASELINE}" "${OPERATION_CANCEL_MAIN_ATTACK_HOME_DISTANCE_BASELINE}" "${OPERATION_CANCEL_MAIN_ATTACK_UNIT_SAMPLES}"; then
    echo "MicroMachine difficulty smoke missing post-cancel MainAttack command and movement evidence." >&2
    print_bot_logs
    exit 1
  fi
fi

verify_runtime_identity_snapshot
print_bot_logs >/dev/null 2>&1

PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' "${BLACKBOARD_DIR}/latest_telemetry.json" "${MIN_TELEMETRY_FRAME}" "${BOT_LOG}" "${DEFENSIVE_UPDATE_ID}" "${AGGRESSIVE_UPDATE_ID}" "${SMOKE_STRATEGY_EVIDENCE_UPDATE_ID}" "${SMOKE_EXPECTED_STRATEGY_DOCTRINE}" "${SMOKE_EXPECTED_PRODUCTION_ACTIONS}" "${SMOKE_EXPECTED_PRODUCTION_ITEMS}" "${SMOKE_REQUIRE_AGGRESSIVE_COMBAT_EVIDENCE}" "${SMOKE_REQUIRE_SCOUT_MOVEMENT_EVIDENCE}" "${SMOKE_REQUIRE_SCOUT_MODULATION_EVIDENCE}" "${SMOKE_REQUIRE_SQUAD_MODULATION_EVIDENCE}" "${SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE}" "${OPERATION_CANCEL_MAIN_ATTACK_COMMAND_BASELINE}" "${OPERATION_CANCEL_MAIN_ATTACK_ACTION_FRAME_BASELINE}" "${OPERATION_CANCEL_MAIN_ATTACK_HOME_DISTANCE_BASELINE}" "${OPERATION_RESTORE_ISSUED_FRAME}" "${OPERATION_CANCEL_MAIN_ATTACK_UNIT_SAMPLES}" "${SMOKE_MODULATION_ARCHIVE_START_OFFSET}"
import json
import os
import re
import sys
import time
from pathlib import Path

from starcraft_commander.micromachine_production_evidence import (
    expected_production_pairs,
    find_causal_production_evidence,
)

telemetry = Path(sys.argv[1])
min_frame = int(sys.argv[2])
bot_log = Path(sys.argv[3])
defensive_update_id = sys.argv[4]
aggressive_update_id = sys.argv[5]
production_evidence_update_id = sys.argv[6]
expected_strategy_doctrine = sys.argv[7]
expected_production_actions = {item for item in sys.argv[8].split() if item}
expected_production_items = {item for item in sys.argv[9].split() if item}
require_aggressive_combat = sys.argv[10] == "1"
require_scout_movement = sys.argv[11] == "1"
require_scout_modulation = sys.argv[12] == "1"
require_squad_modulation = sys.argv[13] == "1"
require_operation_lifecycle = sys.argv[14] == "1"
post_cancel_command_baseline = int(sys.argv[15])
post_cancel_action_frame_baseline = int(sys.argv[16])
post_cancel_home_distance_baseline = float(sys.argv[17])
restore_issued_at_frame = int(sys.argv[18])
try:
    post_cancel_unit_samples_baseline = json.loads(sys.argv[19])
except (json.JSONDecodeError, TypeError, ValueError):
    raise SystemExit("invalid post-cancel MainAttack unit sample baseline")
modulation_archive_start_offset = int(sys.argv[20])
min_main_attack_home_distance = float(os.environ.get("SMOKE_MIN_MAIN_ATTACK_HOME_DISTANCE", "12.0"))
min_post_cancel_main_attack_displacement = float(
    os.environ.get(
        "SMOKE_MIN_POST_CANCEL_MAIN_ATTACK_DISPLACEMENT",
        "4.0",
    )
)
min_combat_scout_home_distance = float(os.environ.get("SMOKE_MIN_COMBAT_SCOUT_HOME_DISTANCE", "8.0"))
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

def unit_positions(samples):
    if not isinstance(samples, list):
        return {}
    positions = {}
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        try:
            tag = int(sample.get("tag", 0) or 0)
            x = float(sample.get("x", 0.0) or 0.0)
            y = float(sample.get("y", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if tag > 0:
            positions[tag] = (x, y)
    return positions

post_cancel_unit_positions = unit_positions(
    post_cancel_unit_samples_baseline
)

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

def verify_tech_gas_before_second_barracks():
    tech_gas_doctrines = {
        "bio_pressure",
        "tank_defensive_hold",
        "siege_contain",
        "contain_enemy_natural",
        "mech_transition",
        "drop_harassment",
        "worker_line_harassment",
        "anti_air_response",
    }
    if expected_strategy_doctrine not in tech_gas_doctrines:
        return

    completed_refinery_frames = []
    for entry in iter_telemetry_entries():
        managers_entry = entry.get("managers", {})
        worker_entry = (
            managers_entry.get("WorkerManager", {})
            if isinstance(managers_entry, dict)
            else {}
        )
        if (
            isinstance(worker_entry, dict)
            and int(worker_entry.get("completed_refinery_count", 0) or 0) > 0
        ):
            completed_refinery_frames.append(
                int(entry.get("frame", 0) or 0)
            )
    if not completed_refinery_frames:
        raise SystemExit(
            "missing completed Refinery telemetry for gas-dependent doctrine: "
            f"{expected_strategy_doctrine}"
        )
    first_completed_refinery_frame = min(completed_refinery_frames)

    barracks_command_frames = []
    refinery_command_frames = []
    frame_prefix = re.compile(r"^(\d+):")
    try:
        log_lines = bot_log.read_text(errors="replace").splitlines()
    except OSError as exc:
        raise SystemExit(
            f"could not read MicroMachine log for tech-gas ordering: {exc}"
        )
    for line in log_lines:
        match = frame_prefix.match(line)
        if match is None:
            continue
        frame = int(match.group(1))
        if (
            "recordVoiActualProductionCommand | "
            "voi actual production command kind=build_command item=Barracks "
            in line
        ):
            barracks_command_frames.append(frame)
        if (
            "constructAssignedBuildings | "
            "build command type=TERRAN_REFINERY"
            in line
        ):
            refinery_command_frames.append(frame)

    refinery_bootstrap_commands = sorted(
        frame
        for frame in refinery_command_frames
        if frame <= first_completed_refinery_frame
    )
    if len(refinery_bootstrap_commands) != 1:
        raise SystemExit(
            "expected exactly one Refinery build command before first "
            "completion; "
            f"commands={refinery_bootstrap_commands}, "
            f"completion_frame={first_completed_refinery_frame}"
        )

    unique_barracks_commands = sorted(set(barracks_command_frames))
    if (
        len(unique_barracks_commands) >= 2
        and refinery_bootstrap_commands[0] >= unique_barracks_commands[1]
    ):
        raise SystemExit(
            "second Barracks build command preceded tech-gas bootstrap; "
            f"barracks_commands={unique_barracks_commands}, "
            f"refinery_command={refinery_bootstrap_commands[0]}"
        )

verify_tech_gas_before_second_barracks()

def first_modulation_issued_at_frame(update_id):
    candidates = []
    modulation_archive = telemetry.with_name("modulation_updates.jsonl")
    if modulation_archive.exists():
        archive_size = modulation_archive.stat().st_size
        if modulation_archive_start_offset > archive_size:
            raise SystemExit(
                "modulation archive was truncated during smoke validation: "
                f"offset={modulation_archive_start_offset}, "
                f"size={archive_size}"
            )
        with modulation_archive.open("rb") as handle:
            handle.seek(modulation_archive_start_offset)
            for raw_line in handle:
                if not raw_line.strip():
                    continue
                try:
                    entry = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(entry, dict)
                    and entry.get("update_id") == update_id
                ):
                    candidates.append(entry)
    if not candidates:
        return None
    return min(int(entry.get("issued_at_frame", 0) or 0) for entry in candidates)

aggressive_first_issued_at_frame = first_modulation_issued_at_frame(
    aggressive_update_id
)
if require_aggressive_combat and aggressive_first_issued_at_frame is None:
    raise SystemExit(
        "missing first aggressive modulation issued_at_frame evidence: "
        f"update_id={aggressive_update_id}, telemetry={telemetry}"
    )
aggressive_first_issued_at_frame = int(aggressive_first_issued_at_frame or 0)

def first_policy_consumed_frame(update_id):
    frames = []
    for entry in iter_telemetry_entries():
        managers_entry = entry.get("managers", {})
        if not isinstance(managers_entry, dict):
            continue
        commander_entry = managers_entry.get("GameCommander", {})
        if (
            isinstance(commander_entry, dict)
            and commander_entry.get("policy_active") is True
            and commander_entry.get("update_id") == update_id
        ):
            frames.append(int(entry.get("frame", 0) or 0))
    return min(frames) if frames else None

aggressive_first_consumed_frame = first_policy_consumed_frame(
    aggressive_update_id
)
if require_aggressive_combat and aggressive_first_consumed_frame is None:
    raise SystemExit(
        "missing first aggressive policy-consumption telemetry: "
        f"update_id={aggressive_update_id}, telemetry={telemetry}"
    )
aggressive_first_consumed_frame = int(
    aggressive_first_consumed_frame or aggressive_first_issued_at_frame
)

def profile_main_attack_command_seen():
    best = {}
    restore_initial_positions = {}
    entries = sorted(
        iter_telemetry_entries(),
        key=lambda entry: int(entry.get("frame", 0) or 0),
    )
    for entry in entries:
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
        unit_count = int(combat_entry.get("main_attack_unit_count", 0) or 0)
        min_units = int(combat_entry.get("main_attack_scope_min_units", 1) or 1)
        max_home_distance = float(combat_entry.get("main_attack_max_home_distance", 0.0) or 0.0)
        current_home_distance = float(combat_entry.get("main_attack_home_distance", 0.0) or 0.0)
        current_unit_positions = unit_positions(
            combat_entry.get("main_attack_unit_samples", [])
        )
        if frame >= aggressive_first_consumed_frame:
            for tag, position in current_unit_positions.items():
                restore_initial_positions.setdefault(tag, position)
        reference_positions = dict(restore_initial_positions)
        reference_positions.update(post_cancel_unit_positions)
        same_tag_displacements = {
            tag: (
                (
                    current_unit_positions[tag][0]
                    - baseline_position[0]
                )
                ** 2
                + (
                    current_unit_positions[tag][1]
                    - baseline_position[1]
                )
                ** 2
            )
            ** 0.5
            for tag, baseline_position
            in reference_positions.items()
            if tag in current_unit_positions
        }
        post_cancel_displacement = max(
            same_tag_displacements.values(),
            default=0.0,
        )
        best = {
            "frame": frame,
            "main_attack_actual_command_issued_count": command_count,
            "main_attack_last_action_frame": command_frame,
            "main_attack_last_issued_action": command,
            "main_attack_order_status": status,
            "main_attack_unit_count": unit_count,
            "main_attack_scope_min_units": min_units,
            "main_attack_scope_threshold_met": combat_entry.get("main_attack_scope_threshold_met"),
            "main_attack_simulation_won": combat_entry.get("main_attack_simulation_won"),
            "main_attack_max_home_distance": max_home_distance,
            "main_attack_home_distance": current_home_distance,
            "post_cancel_main_attack_displacement": post_cancel_displacement,
            "post_cancel_matching_unit_tags": sorted(
                same_tag_displacements
            ),
            "required_post_cancel_main_attack_displacement": min_post_cancel_main_attack_displacement,
            "required_main_attack_home_distance": min_main_attack_home_distance,
            "aggressive_first_issued_at_frame": aggressive_first_issued_at_frame,
            "aggressive_first_consumed_frame": aggressive_first_consumed_frame,
        }
        issued_main_attack = "squad=MainAttack" in command
        if (
            issued_main_attack
            and command_count > 0
            and status == "Attack"
            and combat_entry.get("main_attack_scope_threshold_met") is True
            and combat_entry.get("main_attack_simulation_won") is True
            and unit_count >= min_units
            and command_frame > 0
            and command_frame >= aggressive_first_consumed_frame
            and frame >= command_frame
            and max_home_distance >= min_main_attack_home_distance
            and (
                not require_operation_lifecycle
                or (
                    command_count > post_cancel_command_baseline
                    and command_frame > post_cancel_action_frame_baseline
                    and command_frame >= aggressive_first_consumed_frame
                    and aggressive_first_consumed_frame >= restore_issued_at_frame
                    and current_home_distance >= min_main_attack_home_distance
                    and post_cancel_displacement >= min_post_cancel_main_attack_displacement
                )
            )
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
main_attack_seen = False
main_attack_evidence = {}
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
    if main_attack_command_frame < aggressive_first_issued_at_frame:
        raise SystemExit(
            "MainAttack command evidence predates the aggressive modulation update: "
            f"first_issued_at_frame={aggressive_first_issued_at_frame}, "
            f"combat={combat!r}"
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
    main_attack_seen, main_attack_evidence = profile_main_attack_command_seen()
    if not main_attack_seen:
        raise SystemExit(
            "MainAttack command did not produce live movement away from home; "
            "missing archived MainAttack command evidence for aggressive profile: "
            f"best={main_attack_evidence!r}, latest={combat!r}"
        )
    if (
        combat.get("scout_scope_status") == "Consumed"
        and int(combat.get("scout_scope_assigned_unit_count", 0) or 0) > 0
        and float(combat.get("scout_max_home_distance", 0.0) or 0.0) < min_combat_scout_home_distance
    ):
        raise SystemExit(
            "Combat scout squad was assigned but did not produce live movement away from home: "
            f"required_distance={min_combat_scout_home_distance}, combat={combat!r}"
        )
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
allowed_doctrine_evidence = {
    "queued",
    "queued_existing",
    "command_issued",
    "represented_satisfied",
}
if (
    production_contract_required
    and str(production.get("last_doctrine_evidence", "") or "")
        not in allowed_doctrine_evidence
):
    raise SystemExit(
        "ProductionManager doctrine action lacks live queue evidence: "
        f"{production!r}"
    )
expected_pairs = expected_production_pairs(
    expected_strategy_doctrine,
    expected_actions=expected_production_actions,
    expected_items=expected_production_items,
)

telemetry_entries = tuple(iter_telemetry_entries())
causal_production_evidence = find_causal_production_evidence(
    telemetry_entries,
    expected_doctrine=expected_strategy_doctrine,
    expected_update_id=production_evidence_update_id,
    expected_pairs=expected_pairs,
    allowed_doctrine_evidence=allowed_doctrine_evidence,
)
matching_production = causal_production_evidence.doctrine_entry
observed_production_actions = causal_production_evidence.observed_actions
observed_production_items = (
    causal_production_evidence.observed_doctrine_items
)
if (
    production_contract_required
    and matching_production is None
):
    raise SystemExit(
        "ProductionManager did not emit expected strategy action/item evidence; "
        f"expected_actions={sorted(expected_production_actions)}, "
        f"expected_items={sorted(expected_production_items)}, "
        f"observed_actions={sorted(observed_production_actions)}, "
        f"observed_items={sorted(observed_production_items)}, latest={production!r}"
    )
if (
    production_contract_required
    and matching_production is not None
    and int(matching_production.get("last_doctrine_frame", 0)) <= 0
):
    raise SystemExit(f"ProductionManager doctrine action frame is missing: {matching_production!r}")
matching_actual_production = (
    causal_production_evidence.actual_command_entry
)
observed_actual_items = causal_production_evidence.observed_actual_items
observed_actual_commands = (
    causal_production_evidence.observed_actual_commands
)
if (
    production_contract_required
    and matching_actual_production is None
):
    raise SystemExit(
        "ProductionManager queued the expected strategy action/item but did not "
        "issue its exact same-update command at or after the doctrine frame; "
        f"expected_pairs={sorted(causal_production_evidence.expected_pairs)}, "
        f"expected_actual_items={sorted(causal_production_evidence.expected_actual_items)}, "
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
last_trace_frame_value = workers.get("last_trace_frame")
if type(last_trace_frame_value) is not int:
    raise SystemExit(f"worker trace frame is not an integer: {workers!r}")
last_trace_frame = last_trace_frame_value
latest_payload_frame = int(payload.get("frame", 0) or 0)
if last_trace_frame < 0 or last_trace_frame > latest_payload_frame:
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
    worker_trace_frame_value = worker_entry.get("last_trace_frame")
    worker_trace_frame = (
        worker_trace_frame_value
        if type(worker_trace_frame_value) is int
        else -1
    )
    if int(worker_entry.get("trace_contract_version", 0)) != 1:
        worker_archive_violation = {
            "code": "invalid_worker_trace_contract",
            "frame": entry.get("frame"),
            "workers": worker_entry,
        }
        break
    if worker_entry_frame >= 512 and (
        int(worker_entry.get("trace_event_count", 0)) <= 0
        or worker_trace_frame < 0
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
