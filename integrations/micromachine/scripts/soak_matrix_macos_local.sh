#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOAK_SCRIPT="${SOAK_SCRIPT:-${SCRIPT_DIR}/soak_macos_local.sh}"
SOAK_MATRIX_RUN_ID="${SOAK_MATRIX_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
SOAK_MATRIX_ARTIFACT_ROOT="${SOAK_MATRIX_ARTIFACT_ROOT:-/private/tmp/voi-mm-soak-matrix}"
SOAK_MATRIX_RUN_DIR="${SOAK_MATRIX_RUN_DIR:-${SOAK_MATRIX_ARTIFACT_ROOT}/${SOAK_MATRIX_RUN_ID}}"
SOAK_MATRIX_REPORT="${SOAK_MATRIX_REPORT:-${SOAK_MATRIX_RUN_DIR}/matrix_report.json}"
SOAK_MATRIX_MAP_FILES="${SOAK_MATRIX_MAP_FILES:-AcropolisLE.SC2Map Ladder2019Season3/ThunderbirdLE.SC2Map}"
SOAK_MATRIX_ENEMY_RACES="${SOAK_MATRIX_ENEMY_RACES:-Zerg}"
SOAK_MATRIX_ENEMY_DIFFICULTIES="${SOAK_MATRIX_ENEMY_DIFFICULTIES:-1}"
SOAK_MATRIX_TARGET_FRAME="${SOAK_MATRIX_TARGET_FRAME:-${SOAK_TARGET_FRAME:-12000}}"
SOAK_MATRIX_TIMEOUT_SECONDS="${SOAK_MATRIX_TIMEOUT_SECONDS:-${SOAK_TIMEOUT_SECONDS:-1200}}"
SOAK_MATRIX_STOP_ON_FAILURE="${SOAK_MATRIX_STOP_ON_FAILURE:-0}"
SOAK_MATRIX_ALLOW_FAILURES="${SOAK_MATRIX_ALLOW_FAILURES:-0}"
SOAK_MATRIX_AGGREGATE_ONLY="${SOAK_MATRIX_AGGREGATE_ONLY:-0}"
SOAK_MATRIX_MIN_PASSES="${SOAK_MATRIX_MIN_PASSES:-1}"

mkdir -p "${SOAK_MATRIX_RUN_DIR}"

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

python3 - <<'PY' "${SOAK_MATRIX_RUN_DIR}" "${SOAK_MATRIX_REPORT}" "${SOAK_MATRIX_TARGET_FRAME}" "${SOAK_MATRIX_TIMEOUT_SECONDS}"
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
target = Path(sys.argv[2])
target_frame = int(sys.argv[3])
timeout_seconds = int(sys.argv[4])
cases = []
passed = 0
failed = 0
for case_dir in sorted(path for path in root.iterdir() if path.is_dir()):
    report_path = case_dir / "soak_report.json"
    case = {
        "case_id": case_dir.name,
        "case_dir": str(case_dir),
        "report": str(report_path),
    }
    if not report_path.exists():
        case.update({"status": "missing_report", "ok": False, "failures": []})
        failed += 1
        cases.append(case)
        continue
    payload = json.loads(report_path.read_text())
    ok = payload.get("ok") is True
    attempts = payload.get("attempts", [])
    direct_failures = payload.get("failures", [])
    flattened_failures = list(direct_failures) if isinstance(direct_failures, list) else []
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            for failure in attempt.get("failures", []):
                if isinstance(failure, dict):
                    flattened_failures.append(
                        {
                            **failure,
                            "attempt": attempt.get("attempt"),
                            "attempt_status": attempt.get("status"),
                        }
                    )
    failure_codes = sorted(
        {
            failure.get("code")
            for failure in flattened_failures
            if isinstance(failure, dict) and failure.get("code")
        }
    )
    case.update(
        {
            "status": payload.get("status"),
            "ok": ok,
            "latest_frame": payload.get("latest_frame"),
            "macro_evidence_ok": payload.get("macro_evidence_ok"),
            "manager_intervention_ok": payload.get("manager_intervention_ok"),
            "failures": flattened_failures,
            "failure_codes": failure_codes,
            "attempts": attempts if isinstance(attempts, list) else [],
            "selected_attempt": payload.get("selected_attempt"),
            "artifact_manifest": payload.get("artifact_manifest", {}),
        }
    )
    if ok:
        passed += 1
    else:
        failed += 1
    cases.append(case)

target.write_text(
    json.dumps(
        {
            "status": "passed" if failed == 0 and cases else "failed",
            "ok": failed == 0 and bool(cases),
            "target_frame": target_frame,
            "timeout_seconds": timeout_seconds,
            "case_count": len(cases),
            "passed": passed,
            "failed": failed,
            "cases": cases,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
print(f"MicroMachine matrix report: {target}")
PY

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
