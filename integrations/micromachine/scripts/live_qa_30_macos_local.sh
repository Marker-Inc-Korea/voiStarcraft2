#!/usr/bin/env bash
set -euo pipefail

# Execute the 30-scenario manifest as real SC2 sessions. A scenario is never
# marked passed from process exit alone; the verifier requires telemetry/log
# evidence and preserves every attempt for later inspection.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MANIFEST="${QA30_MANIFEST:-${REPO_ROOT}/.local/qa-scenario-manifest-30.json}"
RUN_ROOT="${QA30_RUN_ROOT:-${REPO_ROOT}/.local/qa-30-orchestrator-$(date -u +%Y%m%dT%H%M%SZ)}"
SMOKE="${SCRIPT_DIR}/smoke_macos_local.sh"
MAX_ATTEMPTS_PER_SCENARIO="${QA30_MAX_ATTEMPTS_PER_SCENARIO:-2}"
TIMEOUT_SECONDS="${QA30_SCENARIO_TIMEOUT_SECONDS:-900}"
SC2_ROOT="${SC2_ROOT:-${HOME}/Desktop/Starcraft2/Starcraft II}"
SC2_EXECUTABLE="${SC2_EXECUTABLE:-${SC2_ROOT}/Versions/Base97364/SC2.app/Contents/MacOS/SC2}"

MANIFEST="$(cd "$(dirname "${MANIFEST}")" && pwd -P)/$(basename "${MANIFEST}")"
RUN_ROOT="$(mkdir -p "${RUN_ROOT}" && cd "${RUN_ROOT}" && pwd -P)"

mkdir -p "${RUN_ROOT}"

python3 - "${MANIFEST}" "${RUN_ROOT}/results.json" <<'PY'
import json, sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text())
out = Path(sys.argv[2])
if out.exists():
    raise SystemExit(0)
out.write_text(json.dumps({
    "schema_version": 1,
    "manifest": str(Path(sys.argv[1]).resolve()),
    "status": "running",
    "scenarios": [
        {"id": item["id"], "goal": item["goal"], "status": "not_executed",
         "attempts": [], "evidence": []}
        for item in manifest["scenarios"]
    ],
}, indent=2) + "\n")
PY

update_result() {
  local scenario_id="$1" status="$2" attempt_dir="$3" reason="$4"
  python3 - "${RUN_ROOT}/results.json" "${scenario_id}" "${status}" "${attempt_dir}" "${reason}" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text())
for item in d["scenarios"]:
    if item["id"] == sys.argv[2]:
        item["status"] = sys.argv[3]
        item["attempts"].append({"dir": sys.argv[4], "reason": sys.argv[5]})
        item["evidence"] = [sys.argv[4] + "/latest_telemetry.json",
                            sys.argv[4] + "/telemetry.jsonl",
                            sys.argv[4] + "/micromachine.log"]
        break
p.write_text(json.dumps(d, indent=2) + "\n")
PY
}

verify_attempt() {
  local scenario_id="$1" attempt_dir="$2"
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - "${scenario_id}" "${attempt_dir}" "${MANIFEST}" <<'PY'
import json, sys
from pathlib import Path
from starcraft_commander.live_qa_audit import audit_attempt

scenario, root = sys.argv[1], Path(sys.argv[2])
if not (root / "latest_telemetry.json").exists():
    print("environment_failure:no_latest_telemetry")
    raise SystemExit(2)
definition = next(item for item in json.loads(Path(sys.argv[3]).read_text())["scenarios"]
                  if item["id"] == scenario)
report = audit_attempt(root, definition)
(root / "scenario_audit.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"status": report["status"], "missing_evidence": report["missing_evidence"],
                  "errors": report["errors"]}, sort_keys=True))
raise SystemExit(0 if report["status"] == "passed" else 1)
PY
}

publish_scenario_command() {
  local scenario_id="$1" attempt_dir="$2" frame="$3" phase="${4:-initial}"
  PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - \
    "${scenario_id}" "${attempt_dir}" "${frame}" "${phase}" "${SCRIPT_DIR}/qa30_scenario_command.py" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

from starcraft_commander.micromachine_bridge import MicroMachineBlackboardUpdate
from starcraft_commander.micromachine_runtime import MicroMachineFilesystemBlackboard
from starcraft_commander.policy_modulation import PolicyModulationVector

scenario_id, directory_text, frame_text, phase, generator = sys.argv[1:]
directory = Path(directory_text)
payload = json.loads(subprocess.check_output([sys.executable, generator, scenario_id, phase]))
vector = PolicyModulationVector.from_mapping(payload)
update_id = f"qa30-{scenario_id}-{frame_text}"
update = MicroMachineBlackboardUpdate(
    update_id=update_id,
    vector=vector,
    issued_at_frame=int(frame_text),
)
MicroMachineFilesystemBlackboard(directory).publish_update(
    update, current_frame=int(frame_text)
)
(directory / ("qa30_command_publish.json" if phase == "initial"
              else f"qa30_command_publish_{phase}.json")).write_text(
    json.dumps({"scenario_id": scenario_id, "update_id": update_id,
                "issued_at_frame": int(frame_text), "payload": payload}, indent=2) + "\n"
)
print(update_id)
PY
}

scenario_profile() {
  case "$1" in
    01-*|02-*|03-*|04-*|05-*|06-*|12-*|13-*|14-*|15-*|16-*|20-*|21-*|22-*|24-*|28-*|29-*) echo marine_rush ;;
    27-*) echo tank_defensive_hold ;;
    07-*|08-*|09-*|10-*|11-*|23-*|25-*|26-*) echo tank_defensive_hold ;;
    17-*|18-*|19-*) echo expand_macro ;;
    30-*) echo bio_pressure ;;
    *) echo marine_rush ;;
  esac
}

command_ready_frame() {
  case "$1" in
    27-prerequisite-wait) echo 1800 ;;
    # Tech-transition scenarios must publish before the opening strategy
    # stalls on Factory placement; the explicit policy then owns the
    # Factory/TechLab/SiegeTank prerequisites.
    07-*|08-*|09-*|10-*|11-*|23-*|25-*|26-*) echo 1800 ;;
    17-*|18-*|19-*) echo 4200 ;;
    20-*|21-*|22-*|24-*|29-*) echo 3600 ;;
    30-*) echo 4200 ;;
    12-*|13-*|14-*|15-*|16-*|28-*) echo 3400 ;;
    *) echo 2200 ;;
  esac
}

SCENARIOS=()
while IFS= read -r scenario_id; do
  SCENARIOS[${#SCENARIOS[@]}]="${scenario_id}"
done < <(python3 - "${MANIFEST}" <<'PY'
import json, sys
for item in json.load(open(sys.argv[1]))["scenarios"]:
    print(item["id"])
PY
)

for scenario_id in "${SCENARIOS[@]}"; do
  if [[ -n "${QA30_SCENARIO_IDS:-}" && ",${QA30_SCENARIO_IDS}," != *",${scenario_id},"* ]]; then
    continue
  fi
  scenario_dir="${RUN_ROOT}/${scenario_id}"
  mkdir -p "${scenario_dir}"
  profile="$(scenario_profile "${scenario_id}")"
  ready_frame="$(command_ready_frame "${scenario_id}")"
  final_status="blocked"
  for attempt in $(seq 1 "${MAX_ATTEMPTS_PER_SCENARIO}"); do
    attempt_dir="$(python3 - "${scenario_dir}" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
number = 1
while True:
    candidate = root / f"attempt-{number}"
    try:
        candidate.mkdir()
    except FileExistsError:
        number += 1
        continue
    print(candidate)
    break
PY
)"
    echo "[qa30] ${scenario_id} attempt ${attempt}/${MAX_ATTEMPTS_PER_SCENARIO} profile=${profile}" >&2
    # The scenario command is the authoritative live input. The smoke
    # harness must not publish a competing profile into the same blackboard.
    env \
      SC2_ROOT="${SC2_ROOT}" SC2_EXECUTABLE="${SC2_EXECUTABLE}" \
      SC2_LAUNCH_MODE=direct SC2_PORTS=8167 \
      SC2_CLEAN_PORTS_BEFORE_LAUNCH="${SC2_CLEAN_PORTS_BEFORE_LAUNCH:-0}" \
      SC2_POST_CLEAN_SETTLE_SECONDS="${QA30_SC2_SETTLE_SECONDS:-2}" \
      VOI_SC2_BOOTSTRAP_SELF_UNITS=1 \
      VOI_SC2_CREATEGAME_MAP_DATA=1 \
      VOI_QA_RAW_OBSERVATION=1 \
      VOI_QA_FORCE_GAME_END_FRAME="${QA30_FORCE_GAME_END_FRAME:-${VOI_QA_FORCE_GAME_END_FRAME:-12000}}" \
      SMOKE_ENEMY_DIFFICULTY="$([[ "${scenario_id}" == "30-game-end" ]] && echo "${QA30_GAME_END_ENEMY_DIFFICULTY:-1}" || echo "${SMOKE_ENEMY_DIFFICULTY:-5}")" \
      SMOKE_MANUAL_LIVE_MODE=1 SMOKE_AUTO_AGGRESSIVE_PROFILE=0 \
      SMOKE_SKIP_LIVE_PREFLIGHT=1 \
      SMOKE_TIMEOUT_SECONDS="${QA30_SMOKE_TIMEOUT_SECONDS:-600}" \
      SMOKE_MAX_ATTEMPTS=1 SMOKE_KEEP_RUNNING_AFTER_PASS=0 \
      SMOKE_STRATEGY_PROFILE_NAME="$([[ "${scenario_id}" == "30-game-end" ]] && echo aggressive_pressure || echo "${profile}")" \
      BLACKBOARD_DIR="${attempt_dir}" \
      "${SMOKE}" --fresh-live-session >"${attempt_dir}/launcher.log" 2>&1 &
    smoke_pid=$!
    smoke_rc=0
    command_published=0
    deadline=$((SECONDS + TIMEOUT_SECONDS))
    while (( SECONDS < deadline )); do
      if [[ -f "${attempt_dir}/latest_telemetry.json" ]]; then
        frame="$(python3 - "${attempt_dir}/latest_telemetry.json" <<'PY'
import json, sys
try:
    print(int(json.load(open(sys.argv[1])).get("frame", 0)))
except Exception:
    print(0)
PY
)"
        # Let the SC2 bootstrap produce the requested composition before the
        # operation command competes for ownership of those units.
      if (( command_published == 0 && frame >= ready_frame )); then
          if publish_scenario_command "${scenario_id}" "${attempt_dir}" "${frame}" \
              >"${attempt_dir}/qa30_command_publish.log" 2>&1; then
            command_published=1
          fi
        fi
        lifecycle_phase=""
        lifecycle_ready=0
        if [[ -f "${attempt_dir}/latest_telemetry.json" ]]; then
          lifecycle_ready="$(python3 - "${attempt_dir}/latest_telemetry.json" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    operations = data.get("managers", {}).get("OperationDirector", {}).get("operations", [])
    print(1 if any(op.get("assigned_unit_tags") for op in operations) else 0)
except Exception:
    print(0)
PY
)"
        fi
        # A fast operation can reach terminal state before the latest snapshot
        # is read, clearing assigned_unit_tags. The archive is authoritative
        # for whether this lifecycle ever had an assigned unit.
        if [[ "${scenario_id}" == "29-heal-regroup" && "${command_published}" == "1" \
              && "${frame}" -ge "$((ready_frame + 100))" \
              && -f "${attempt_dir}/telemetry.jsonl" ]]; then
          if rg -q '"assigned_unit_tags":\[[^]]' "${attempt_dir}/telemetry.jsonl"; then
            lifecycle_ready=1
          fi
        fi
        case "${scenario_id}" in
          20-retreat) (( command_published == 1 && lifecycle_ready == 1 )) && lifecycle_phase=retreat ;;
          21-cancel-attack)
            if (( command_published == 1 && lifecycle_ready == 1 )) \
              && [[ -f "${attempt_dir}/telemetry.jsonl" ]]; then
              if python3 - "${attempt_dir}/telemetry.jsonl" <<'PY'
import json
import sys

for line in open(sys.argv[1]):
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        continue
    production = data.get("managers", {}).get("ProductionManager", {})
    if int(production.get("operation_owned_queue_item_count", 0) or 0) <= 0:
        continue
    for item in production.get("operation_owned_queue_items", []):
        owners = item.get("owners", [])
        if any(
            owner.get("operation_id") == "21-cancel-attack"
            and int(owner.get("generation", 0) or 0) == 1
            for owner in owners
        ):
            raise SystemExit(0)
raise SystemExit(1)
PY
              then
                lifecycle_phase=cancel
              fi
            fi
            ;;
          22-reassign-marines|23-reassign-tanks)
            if (( command_published == 1 )) && [[ -f "${attempt_dir}/telemetry.jsonl" ]]; then
              if python3 - "${attempt_dir}/telemetry.jsonl" <<'PY'
import json
import sys

operations = {}
for line in open(sys.argv[1]):
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        continue
    for operation in data.get("managers", {}).get("OperationDirector", {}).get("operations", []):
        operation_id = operation.get("operation_id", "")
        if operation_id.endswith("-source") or operation_id.endswith("-destination"):
            operations[operation_id] = operation

ready = (
    len(operations) == 2
    and all(
        operation.get("status") not in {"COMPLETED", "CANCELLED", "EXPIRED"}
        and len(operation.get("assigned_unit_tags", [])) >= (
            2 if operation.get("operation_id", "").endswith("-source") else 1
        )
        for operation in operations.values()
    )
)
raise SystemExit(0 if ready else 1)
PY
              then
                lifecycle_phase=reassign
              fi
            fi
            ;;
          24-retarget) (( command_published == 1 && lifecycle_ready == 1 )) && lifecycle_phase=retarget ;;
          25-long-defense)
            if (( command_published == 1 )) \
              && [[ -f "${attempt_dir}/latest_telemetry.json" ]]; then
              if python3 - "${attempt_dir}/latest_telemetry.json" \
                  "${attempt_dir}/telemetry.jsonl" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
frame = int(data.get("frame", 0) or 0)
production = data.get("managers", {}).get("ProductionManager", {})
lease = int(production.get("policy_expires_at_frame", 0) or 0)
operations = data.get("managers", {}).get("OperationDirector", {}).get("operations", [])
alive = False
if lease > 0 and frame > lease:
    for line in open(sys.argv[2]):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if int(row.get("frame", 0) or 0) <= lease:
            continue
        for operation in row.get("managers", {}).get("OperationDirector", {}).get("operations", []):
            if (
                operation.get("operation_id") == "25-long-defense"
                and operation.get("status") not in {"COMPLETED", "CANCELLED", "EXPIRED"}
                and operation.get("assigned_unit_tags")
                and int(operation.get("assigned_count", 0) or 0) > 0
            ):
                alive = True
                break
        if alive:
            break
raise SystemExit(0 if lease > 0 and frame > lease and alive else 1)
PY
              then
                lifecycle_phase=cancel
              fi
            fi
            ;;
          29-heal-regroup) (( command_published == 1 && lifecycle_ready == 1 )) && lifecycle_phase=regroup ;;
        esac
        if [[ -n "${lifecycle_phase}" && ! -f "${attempt_dir}/qa30_command_publish_${lifecycle_phase}.json" ]]; then
          publish_scenario_command "${scenario_id}" "${attempt_dir}" "${frame}" "${lifecycle_phase}" \
            >"${attempt_dir}/qa30_command_publish_${lifecycle_phase}.log" 2>&1 || true
        fi
        # Stop a live session as soon as the independent telemetry auditor
        # proves the scenario. This avoids holding a completed game until the
        # global smoke timeout while preserving all evidence files.
        if (( command_published == 1 )); then
          if PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - \
              "${scenario_id}" "${attempt_dir}" "${MANIFEST}" <<'PY'
import json
import sys
from pathlib import Path
from starcraft_commander.live_qa_audit import audit_attempt

scenario_id, attempt_text, manifest_text = sys.argv[1:]
root = Path(attempt_text)
manifest = json.loads(Path(manifest_text).read_text())
scenario = next(item for item in manifest["scenarios"] if item["id"] == scenario_id)
if audit_attempt(root, scenario)["status"] == "passed":
    raise SystemExit(0)
raise SystemExit(1)
PY
          then
            echo "[qa30] ${scenario_id} independently passed; stopping live session." >&2
            break
          fi
        fi
        # Completion is decided from the complete archive, never process exit.
      fi
      if ! kill -0 "${smoke_pid}" 2>/dev/null; then
        break
      fi
      sleep 2
    done
    if kill -0 "${smoke_pid}" 2>/dev/null; then
      kill "${smoke_pid}" 2>/dev/null || true
      wait "${smoke_pid}" 2>/dev/null || smoke_rc=$?
    else
      wait "${smoke_pid}" 2>/dev/null || smoke_rc=$?
    fi
    if verify_output="$(verify_attempt "${scenario_id}" "${attempt_dir}" 2>&1)"; then
      update_result "${scenario_id}" passed "${attempt_dir}" "${verify_output}"
      final_status=passed
      break
    else
      verify_rc=$?
      reason="smoke_rc=${smoke_rc}; ${verify_output}"
      update_result "${scenario_id}" blocked "${attempt_dir}" "${reason}"
      if (( verify_rc == 2 )); then
        echo "[qa30] Shared environment failure; stopping before launching further games." >&2
        break 2
      fi
    fi
  done
  [[ "${final_status}" == passed ]] || true
done

python3 - "${RUN_ROOT}/results.json" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1]); d = json.loads(p.read_text())
statuses = [x["status"] for x in d["scenarios"]]
d["status"] = "passed" if statuses and all(x == "passed" for x in statuses) else "incomplete"
d["summary"] = {s: statuses.count(s) for s in sorted(set(statuses))}
p.write_text(json.dumps(d, indent=2) + "\n")
print(json.dumps({"status": d["status"], "summary": d["summary"]}, sort_keys=True))
PY
