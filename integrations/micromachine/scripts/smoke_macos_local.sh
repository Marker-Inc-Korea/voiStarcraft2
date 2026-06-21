#!/usr/bin/env bash
set -euo pipefail

MICROMACHINE_DIR="${MICROMACHINE_DIR:-/private/tmp/voi-micromachine-runtime/MicroMachine}"
MICROMACHINE_BUILD_DIR="${MICROMACHINE_BUILD_DIR:-${MICROMACHINE_DIR}/build-latest-api}"
SC2_ROOT="${SC2_ROOT:-/Users/jinminseong/Desktop/StarCraft2/StarCraft II}"
SC2_EXECUTABLE="${SC2_EXECUTABLE:-${SC2_ROOT}/Versions/Base96883/SC2.app/Contents/MacOS/SC2}"
BLACKBOARD_DIR="${BLACKBOARD_DIR:-/private/tmp/voi-mm-smoke}"
MAP_FILE="${MAP_FILE:-AcropolisLE.SC2Map}"
MIN_TELEMETRY_FRAME="${MIN_TELEMETRY_FRAME:-32}"
SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-120}"
BOT_LOG="${BLACKBOARD_DIR}/micromachine.log"
SC2_NET_ADDRESS="${SC2_NET_ADDRESS:-127.0.0.1}"
SC2_PORTS=(${SC2_PORTS:-8167 8168})
BOT_PID=""

REQUIRED_MACRO_EVIDENCE=(
  "build command type=TERRAN_SUPPLYDEPOT"
  "TERRAN_SUPPLYDEPOT UnderConstruction"
  "create direct end item=Barracks result=1"
  "build command type=TERRAN_BARRACKS"
  "TERRAN_BARRACKS UnderConstruction"
)

FORBIDDEN_MACRO_FAILURES=(
  "Failed to place Barracks"
  "Cancel building TERRAN_SUPPLYDEPOT"
  "Cancel building TERRAN_BARRACKS"
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
  return 0
}

print_missing_macro_evidence() {
  local term
  for term in "${REQUIRED_MACRO_EVIDENCE[@]}"; do
    if ! has_log_term "${term}"; then
      echo "missing macro evidence: ${term}" >&2
    fi
  done
}

if [[ "${MAP_FILE}" != /* && -f "${SC2_ROOT}/Maps/${MAP_FILE}" ]]; then
  MAP_FILE="${SC2_ROOT}/Maps/${MAP_FILE}"
fi

mkdir -p "${BLACKBOARD_DIR}"
rm -f "${BLACKBOARD_DIR}/latest_telemetry.json" "${BLACKBOARD_DIR}/telemetry.jsonl" "${BOT_LOG}"
cat > "${BLACKBOARD_DIR}/latest_modulation.kv" <<EOF
protocol_version=voi-mm-bridge/v1
update_id=smoke-001
expires_at_frame=10000
combat.defend_bias=0.75
combat.aggression=-0.2
emergency.force_retreat=false
EOF

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
    if python3 - "${BLACKBOARD_DIR}/latest_telemetry.json" "${MIN_TELEMETRY_FRAME}" <<'PY'
import json
import sys
from pathlib import Path

min_frame = int(sys.argv[2])
try:
    payload = json.loads(Path(sys.argv[1]).read_text())
except json.JSONDecodeError:
    raise SystemExit(1)
if payload.get("frame", 0) >= min_frame:
    commander = payload.get("managers", {}).get("GameCommander", {})
    if commander.get("policy_active") is not True:
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

python3 - <<'PY' "${BLACKBOARD_DIR}/latest_telemetry.json" "${MIN_TELEMETRY_FRAME}" "${BOT_LOG}"
import json
import sys
from pathlib import Path

telemetry = Path(sys.argv[1])
min_frame = int(sys.argv[2])
bot_log = Path(sys.argv[3])
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
if commander.get("update_id") != "smoke-001":
    raise SystemExit(f"unexpected GameCommander update id: {commander!r}")
print(json.dumps(payload, sort_keys=True))
PY
