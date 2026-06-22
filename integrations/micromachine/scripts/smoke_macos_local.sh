#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MICROMACHINE_DIR="${MICROMACHINE_DIR:-/private/tmp/voi-micromachine-runtime/MicroMachine}"
MICROMACHINE_BUILD_DIR="${MICROMACHINE_BUILD_DIR:-${MICROMACHINE_DIR}/build-latest-api}"
SC2_ROOT="${SC2_ROOT:-/Users/jinminseong/Desktop/StarCraft2/StarCraft II}"
SC2_EXECUTABLE="${SC2_EXECUTABLE:-${SC2_ROOT}/Versions/Base96883/SC2.app/Contents/MacOS/SC2}"
BLACKBOARD_DIR="${BLACKBOARD_DIR:-/private/tmp/voi-mm-smoke}"
MAP_FILE="${MAP_FILE:-AcropolisLE.SC2Map}"
MIN_TELEMETRY_FRAME="${MIN_TELEMETRY_FRAME:-5200}"
AGGRESSIVE_PROFILE_FRAME="${AGGRESSIVE_PROFILE_FRAME:-2600}"
SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-600}"
BOT_LOG="${BLACKBOARD_DIR}/micromachine.log"
SC2_NET_ADDRESS="${SC2_NET_ADDRESS:-127.0.0.1}"
SC2_PORTS=(${SC2_PORTS:-8167 8168})
BOT_PID=""
DEFENSIVE_UPDATE_ID="${DEFENSIVE_UPDATE_ID:-smoke-defensive-hold}"
AGGRESSIVE_UPDATE_ID="${AGGRESSIVE_UPDATE_ID:-smoke-aggressive-pressure}"
AGGRESSIVE_PROFILE_PUBLISHED=0

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

cleanup_runtime() {
  if [[ -n "${BOT_PID}" ]] && kill -0 "${BOT_PID}" 2>/dev/null; then
    kill "${BOT_PID}" 2>/dev/null || true
    wait "${BOT_PID}" 2>/dev/null || true
  fi

  local port
  for port in "${SC2_PORTS[@]}"; do
    while IFS= read -r pid; do
      [[ -n "${pid}" ]] || continue
      kill "${pid}" 2>/dev/null || true
    done < <(pgrep -f "${SC2_EXECUTABLE} -listen ${SC2_NET_ADDRESS} -port ${port}" || true)
  done
}

trap cleanup_runtime EXIT

has_log_term() {
  local term="$1"
  [[ -f "${BOT_LOG}" ]] && grep -Fq "${term}" "${BOT_LOG}"
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
  [[ -f "${BOT_LOG}" ]] || return 1
  awk '
    /Gas income:/ {
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^[0-9]+$/ && $i > 0) {
          found = 1
        }
      }
    }
    END { exit(found ? 0 : 1) }
  ' "${BOT_LOG}"
}

has_positive_mineral_income() {
  [[ -f "${BOT_LOG}" ]] || return 1
  awk '
    /Mineral income:/ {
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^[0-9]+$/ && $i > 0) {
          found = 1
        }
      }
    }
    END { exit(found ? 0 : 1) }
  ' "${BOT_LOG}"
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
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' "${BLACKBOARD_DIR}" "${profile}" "${update_id}" "${frame}"
import sys

from starcraft_commander.micromachine_runtime import (
    MicroMachineFilesystemBlackboard,
    build_aggressive_pressure_profile,
    build_defensive_hold_profile,
)

directory, profile_name, update_id, frame_text = sys.argv[1:5]
backend = MicroMachineFilesystemBlackboard(directory)
if profile_name == "defensive_hold":
    vector = build_defensive_hold_profile()
elif profile_name == "aggressive_pressure":
    vector = build_aggressive_pressure_profile()
else:
    raise SystemExit(f"unknown MicroMachine profile: {profile_name}")
backend.publish_vector(vector, current_frame=int(frame_text), update_id=update_id)
PY
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

if [[ "${MAP_FILE}" != /* && -f "${SC2_ROOT}/Maps/${MAP_FILE}" ]]; then
  MAP_FILE="${SC2_ROOT}/Maps/${MAP_FILE}"
fi

mkdir -p "${BLACKBOARD_DIR}"
rm -f "${BLACKBOARD_DIR}/latest_telemetry.json" "${BLACKBOARD_DIR}/telemetry.jsonl" "${BOT_LOG}"
publish_profile "defensive_hold" "${DEFENSIVE_UPDATE_ID}" "0"

python3 - <<'PY' "${MICROMACHINE_DIR}/bin/BotConfig.txt" "${MAP_FILE}"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
map_file = sys.argv[2]
config = json.loads(path.read_text())
config["SC2API"]["PlayAsHuman"] = False
config["SC2API"]["ForceStepMode"] = True
config["SC2API"]["MapFile"] = map_file
config["SC2API"]["PlayVsItSelf"] = bool(int(__import__("os").environ.get("SMOKE_PLAY_VS_SELF", "0")))
config["SC2API"]["EnemyDifficulty"] = 1
config["SC2API"]["EnemyRace"] = "Zerg"
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
    VOI_SC2_BOOTSTRAP_SELF_UNITS="${VOI_SC2_BOOTSTRAP_SELF_UNITS:-${VOI_SC2_CONNECT_PORT:+1}}" \
    "${MICROMACHINE_BUILD_DIR}/bin/MicroMachine" \
    -e "${SC2_EXECUTABLE}"
) >"${BOT_LOG}" 2>&1 &
BOT_PID=$!

deadline=$((SECONDS + SMOKE_TIMEOUT_SECONDS))
while kill -0 "${BOT_PID}" 2>/dev/null; do
  if has_forbidden_macro_failure; then
    cleanup_runtime
    tail -200 "${BOT_LOG}" >&2 || true
    exit 1
  fi

  if [[ -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]]; then
    current_telemetry_frame="$(telemetry_frame || true)"
    if [[ "${AGGRESSIVE_PROFILE_PUBLISHED}" -eq 0 && -n "${current_telemetry_frame}" && "${current_telemetry_frame}" -ge "${AGGRESSIVE_PROFILE_FRAME}" ]] && has_required_macro_evidence; then
      publish_profile "aggressive_pressure" "${AGGRESSIVE_UPDATE_ID}" "${current_telemetry_frame}"
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
        cleanup_runtime
        break
      fi
    fi
  fi

  if (( SECONDS >= deadline )); then
    cleanup_runtime
    echo "MicroMachine smoke timed out after ${SMOKE_TIMEOUT_SECONDS}s" >&2
    print_missing_macro_evidence
    tail -200 "${BOT_LOG}" >&2 || true
    exit 1
  fi
  sleep 2
done

if has_forbidden_macro_failure; then
  tail -200 "${BOT_LOG}" >&2 || true
  exit 1
fi

if [[ ! -f "${BLACKBOARD_DIR}/latest_telemetry.json" ]]; then
  wait "${BOT_PID}" 2>/dev/null || true
  echo "MicroMachine did not emit telemetry" >&2
  tail -200 "${BOT_LOG}" >&2 || true
  exit 1
fi

if ! has_required_macro_evidence; then
  echo "MicroMachine reached SC2 API but did not execute the required macro opening" >&2
  print_missing_macro_evidence
  tail -200 "${BOT_LOG}" >&2 || true
  exit 1
fi

python3 - <<'PY' "${BLACKBOARD_DIR}/latest_telemetry.json" "${MIN_TELEMETRY_FRAME}" "${BOT_LOG}" "${DEFENSIVE_UPDATE_ID}" "${AGGRESSIVE_UPDATE_ID}"
import json
import sys
from pathlib import Path

telemetry = Path(sys.argv[1])
min_frame = int(sys.argv[2])
bot_log = Path(sys.argv[3])
defensive_update_id = sys.argv[4]
aggressive_update_id = sys.argv[5]
payload = json.loads(telemetry.read_text())
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
if combat.get("bounded_intervention") is not True or combat.get("aggression", 0) <= 0:
    raise SystemExit(f"missing aggressive CombatCommander modulation evidence: {combat!r}")
scout = managers.get("ScoutManager")
if not scout or scout.get("active") is not True:
    raise SystemExit(f"missing ScoutManager activity evidence: {managers!r}")
if scout.get("bounded_intervention") is not True:
    raise SystemExit(f"missing ScoutManager modulation evidence: {scout!r}")
if scout.get("has_worker_scout") is not True and int(scout.get("scout_unit_count", 0)) <= 0:
    raise SystemExit(f"no scout movement evidence: {scout!r}")
if scout.get("status") in (None, "", "None"):
    raise SystemExit(f"no scout status evidence: {scout!r}")
archive = telemetry.with_name("telemetry.jsonl")
if not archive.exists():
    raise SystemExit(f"missing telemetry archive: {archive}")
updates = []
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
for expected in (defensive_update_id, aggressive_update_id):
    if expected not in updates:
        raise SystemExit(f"stale modulation or missing profile transition: {expected} not in {archive}")
print(json.dumps(payload, sort_keys=True))
PY
