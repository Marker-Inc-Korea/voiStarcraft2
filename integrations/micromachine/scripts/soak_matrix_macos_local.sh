#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOAK_SCRIPT="${SOAK_SCRIPT:-${SCRIPT_DIR}/soak_macos_local.sh}"
SOAK_MATRIX_RUN_ID="${SOAK_MATRIX_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
SOAK_MATRIX_ARTIFACT_ROOT="${SOAK_MATRIX_ARTIFACT_ROOT:-/private/tmp/voi-mm-soak-matrix}"
SOAK_MATRIX_RUN_DIR="${SOAK_MATRIX_RUN_DIR:-${SOAK_MATRIX_ARTIFACT_ROOT}/${SOAK_MATRIX_RUN_ID}}"
SOAK_MATRIX_REPORT="${SOAK_MATRIX_REPORT:-${SOAK_MATRIX_RUN_DIR}/matrix_report.json}"
SOAK_MATRIX_MAP_FILES="${SOAK_MATRIX_MAP_FILES:-AcropolisLE.SC2Map}"
SOAK_MATRIX_ENEMY_RACES="${SOAK_MATRIX_ENEMY_RACES:-Zerg}"
SOAK_MATRIX_ENEMY_DIFFICULTIES="${SOAK_MATRIX_ENEMY_DIFFICULTIES:-1}"
SOAK_MATRIX_TARGET_FRAME="${SOAK_MATRIX_TARGET_FRAME:-${SOAK_TARGET_FRAME:-12000}}"
SOAK_MATRIX_TIMEOUT_SECONDS="${SOAK_MATRIX_TIMEOUT_SECONDS:-${SOAK_TIMEOUT_SECONDS:-1200}}"
SOAK_MATRIX_STOP_ON_FAILURE="${SOAK_MATRIX_STOP_ON_FAILURE:-0}"
SOAK_MATRIX_ALLOW_FAILURES="${SOAK_MATRIX_ALLOW_FAILURES:-0}"
SOAK_MATRIX_AGGREGATE_ONLY="${SOAK_MATRIX_AGGREGATE_ONLY:-0}"
SOAK_MATRIX_MIN_PASSES="${SOAK_MATRIX_MIN_PASSES:-1}"
SOAK_MATRIX_ENABLED="${SOAK_MATRIX_ENABLED:-1}"
SOAK_MATRIX_HISTORY_JSON="${SOAK_MATRIX_HISTORY_JSON:-${SOAK_MATRIX_RUN_DIR}/soak_history_dashboard.json}"
SOAK_MATRIX_HISTORY_MD="${SOAK_MATRIX_HISTORY_MD:-${SOAK_MATRIX_RUN_DIR}/soak_history_dashboard.md}"

# The Python aggregator preserves per-case failure_codes, attempts, and
# artifact_manifest fields in matrix_report.json and the history dashboard.
mkdir -p "${SOAK_MATRIX_RUN_DIR}"

if [[ "${SOAK_MATRIX_ENABLED}" != "1" ]]; then
  python3 - <<'PY' "${SOAK_MATRIX_REPORT}" "${SOAK_MATRIX_HISTORY_JSON}" "${SOAK_MATRIX_HISTORY_MD}"
import json
import sys
from pathlib import Path

report = Path(sys.argv[1])
history_json = Path(sys.argv[2])
history_md = Path(sys.argv[3])
payload = {
    "status": "disabled",
    "ok": False,
    "enabled": False,
    "case_count": 0,
    "passed": 0,
    "failed": 0,
    "cases": [],
}
dashboard = {
    "status": "disabled",
    "ok": False,
    "run_count": 0,
    "passed_runs": 0,
    "failed_runs": 0,
    "case_count": 0,
    "passed_cases": 0,
    "failed_cases": 0,
    "failure_codes": [],
    "maps": [],
    "enemy_races": [],
    "enemy_difficulties": [],
    "target_frames": [],
    "runs": [],
}
report.parent.mkdir(parents=True, exist_ok=True)
history_json.parent.mkdir(parents=True, exist_ok=True)
report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
history_json.write_text(json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
history_md.write_text(
    "# MicroMachine Soak History\n\n"
    "- Status: `disabled`\n"
    "- Soak execution was skipped because `SOAK_MATRIX_ENABLED` was not `1`.\n"
)
print(f"MicroMachine matrix disabled: {report}")
PY
  exit 0
fi

if [[ "${SOAK_MATRIX_AGGREGATE_ONLY}" != "1" ]]; then
  run_index=0
  for map_file in ${SOAK_MATRIX_MAP_FILES}; do
    for enemy_race in ${SOAK_MATRIX_ENEMY_RACES}; do
      for enemy_difficulty in ${SOAK_MATRIX_ENEMY_DIFFICULTIES}; do
        run_index=$((run_index + 1))
        case_id="$(printf '%02d' "${run_index}")-$(echo "${map_file}" | tr '/ .' '---')-${enemy_race}-d${enemy_difficulty}"
        case_dir="${SOAK_MATRIX_RUN_DIR}/${case_id}"
        echo "Starting MicroMachine matrix case ${case_id}"
        set +e
        SOAK_RUN_ID="${case_id}" \
          SOAK_RUN_DIR="${case_dir}" \
          BLACKBOARD_DIR="${case_dir}" \
          MAP_FILE="${map_file}" \
          SOAK_ENEMY_RACE="${enemy_race}" \
          SOAK_ENEMY_DIFFICULTY="${enemy_difficulty}" \
          SOAK_TARGET_FRAME="${SOAK_MATRIX_TARGET_FRAME}" \
          SOAK_TIMEOUT_SECONDS="${SOAK_MATRIX_TIMEOUT_SECONDS}" \
          "${SOAK_SCRIPT}"
        exit_code="$?"
        set -e
        if [[ "${exit_code}" -ne 0 && "${SOAK_MATRIX_STOP_ON_FAILURE}" == "1" ]]; then
          break 3
        fi
      done
    done
  done
fi

python3 -m starcraft_commander.micromachine_soak_history matrix-report \
  --run-dir "${SOAK_MATRIX_RUN_DIR}" \
  --output "${SOAK_MATRIX_REPORT}" \
  --target-frame "${SOAK_MATRIX_TARGET_FRAME}" \
  --timeout-seconds "${SOAK_MATRIX_TIMEOUT_SECONDS}"

python3 -m starcraft_commander.micromachine_soak_history history-dashboard \
  --root "${SOAK_MATRIX_ARTIFACT_ROOT}" \
  --output-json "${SOAK_MATRIX_HISTORY_JSON}" \
  --output-markdown "${SOAK_MATRIX_HISTORY_MD}"

python3 - <<'PY' "${SOAK_MATRIX_REPORT}" "${SOAK_MATRIX_ALLOW_FAILURES}" "${SOAK_MATRIX_MIN_PASSES}"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
allow_failures = sys.argv[2] == "1"
min_passes = int(sys.argv[3])
print(
    "MicroMachine matrix completed: "
    f"passed={payload['passed']} failed={payload['failed']} cases={payload['case_count']}"
)
if payload["ok"]:
    raise SystemExit(0)
if allow_failures and payload["case_count"] > 0 and payload["passed"] >= min_passes:
    raise SystemExit(0)
if allow_failures:
    print(
        "MicroMachine matrix rejected: "
        f"SOAK_MATRIX_ALLOW_FAILURES still requires at least {min_passes} passing case(s)."
    )
raise SystemExit(1)
PY
