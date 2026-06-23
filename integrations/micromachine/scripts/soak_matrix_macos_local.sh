#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOAK_SCRIPT="${SOAK_SCRIPT:-${SCRIPT_DIR}/soak_macos_local.sh}"
SOAK_MATRIX_RUN_ID="${SOAK_MATRIX_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
SOAK_MATRIX_ARTIFACT_ROOT="${SOAK_MATRIX_ARTIFACT_ROOT:-/private/tmp/voi-mm-soak-matrix}"
SOAK_MATRIX_RUN_DIR="${SOAK_MATRIX_RUN_DIR:-${SOAK_MATRIX_ARTIFACT_ROOT}/${SOAK_MATRIX_RUN_ID}}"
SOAK_MATRIX_REPORT="${SOAK_MATRIX_REPORT:-${SOAK_MATRIX_RUN_DIR}/matrix_report.json}"
SOAK_MATRIX_QUALIFICATION_TIER="${SOAK_MATRIX_QUALIFICATION_TIER:-production}"
SOAK_MATRIX_MAP_POOL_MANIFEST="${SOAK_MATRIX_MAP_POOL_MANIFEST:-${SCRIPT_DIR}/../MICROMACHINE_MAP_POOL.json}"
SOAK_MATRIX_MAP_ROOTS="${SOAK_MATRIX_MAP_ROOTS:-}"
if [[ -z "${SOAK_MATRIX_MAP_FILES:-}" ]]; then
  SOAK_MATRIX_MAP_FILES="$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_QUALIFICATION_TIER}" --field map_files)"
fi
if [[ -z "${SOAK_MATRIX_ENEMY_RACES:-}" ]]; then
  SOAK_MATRIX_ENEMY_RACES="$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_QUALIFICATION_TIER}" --field enemy_races)"
fi
if [[ -z "${SOAK_MATRIX_ENEMY_DIFFICULTIES:-}" ]]; then
  SOAK_MATRIX_ENEMY_DIFFICULTIES="$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_QUALIFICATION_TIER}" --field enemy_difficulties)"
fi
if [[ -z "${SOAK_MATRIX_TARGET_FRAME:-}" ]]; then
  SOAK_MATRIX_TARGET_FRAME="${SOAK_TARGET_FRAME:-$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_QUALIFICATION_TIER}" --field target_frame)}"
fi
if [[ -z "${SOAK_MATRIX_TIMEOUT_SECONDS:-}" ]]; then
  SOAK_MATRIX_TIMEOUT_SECONDS="${SOAK_TIMEOUT_SECONDS:-$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_QUALIFICATION_TIER}" --field timeout_seconds)}"
fi
if [[ -z "${SOAK_MATRIX_STRATEGY_PROFILES:-}" ]]; then
  SOAK_MATRIX_STRATEGY_PROFILES="$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_QUALIFICATION_TIER}" --field strategy_profiles)"
fi
SOAK_MATRIX_DEFAULT_BUILD_DIR="${MICROMACHINE_BUILD_DIR:-/private/tmp/MicroMachine/build-latest-api}"
SOAK_MATRIX_BUILD_IDENTITY_REPORT="${SOAK_MATRIX_BUILD_IDENTITY_REPORT:-${MICROMACHINE_BUILD_IDENTITY_REPORT:-${SOAK_MATRIX_DEFAULT_BUILD_DIR}/voi_build_identity.json}}"
SOAK_MATRIX_BUILD_IDENTITY_OK="${SOAK_MATRIX_BUILD_IDENTITY_OK:-0}"
SOAK_MATRIX_BUILD_IDENTITY_FAILURE_CODES="${SOAK_MATRIX_BUILD_IDENTITY_FAILURE_CODES:-missing_build_identity_report}"
if [[ -z "${SOAK_MATRIX_BUILD_IDENTITY:-}" && -f "${SOAK_MATRIX_BUILD_IDENTITY_REPORT}" ]]; then
  SOAK_MATRIX_BUILD_IDENTITY="$(python3 -m starcraft_commander.micromachine_build_identity \
    --read-report "${SOAK_MATRIX_BUILD_IDENTITY_REPORT}" \
    --field identity)"
  SOAK_MATRIX_BUILD_IDENTITY_OK="$(python3 -m starcraft_commander.micromachine_build_identity \
    --read-report "${SOAK_MATRIX_BUILD_IDENTITY_REPORT}" \
    --field ok)"
  SOAK_MATRIX_BUILD_IDENTITY_FAILURE_CODES="$(python3 -m starcraft_commander.micromachine_build_identity \
    --read-report "${SOAK_MATRIX_BUILD_IDENTITY_REPORT}" \
    --field failure-codes)"
fi
SOAK_MATRIX_BUILD_IDENTITY="${SOAK_MATRIX_BUILD_IDENTITY:-${MICROMACHINE_BUILD_ID:-unrecorded}}"
SOAK_MATRIX_SIGNOFF_TIER="${SOAK_MATRIX_SIGNOFF_TIER:-production}"
if [[ -z "${SOAK_MATRIX_SIGNOFF_MAP_FILES:-}" ]]; then
  SOAK_MATRIX_SIGNOFF_MAP_FILES="$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_SIGNOFF_TIER}" --field map_files)"
fi
if [[ -z "${SOAK_MATRIX_SIGNOFF_ENEMY_RACES:-}" ]]; then
  SOAK_MATRIX_SIGNOFF_ENEMY_RACES="$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_SIGNOFF_TIER}" --field enemy_races)"
fi
if [[ -z "${SOAK_MATRIX_SIGNOFF_ENEMY_DIFFICULTIES:-}" ]]; then
  SOAK_MATRIX_SIGNOFF_ENEMY_DIFFICULTIES="$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_SIGNOFF_TIER}" --field enemy_difficulties)"
fi
if [[ -z "${SOAK_MATRIX_SIGNOFF_STRATEGY_PROFILES:-}" ]]; then
  SOAK_MATRIX_SIGNOFF_STRATEGY_PROFILES="$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_SIGNOFF_TIER}" --field strategy_profiles)"
fi
SOAK_MATRIX_SIGNOFF_REQUIRED_BUILD_IDENTITY="${SOAK_MATRIX_SIGNOFF_REQUIRED_BUILD_IDENTITY:-${SOAK_MATRIX_BUILD_IDENTITY}}"
SOAK_MATRIX_STOP_ON_FAILURE="${SOAK_MATRIX_STOP_ON_FAILURE:-0}"
if [[ -z "${SOAK_MATRIX_ALLOW_FAILURES:-}" ]]; then
  SOAK_MATRIX_ALLOW_FAILURES="$(python3 -m starcraft_commander.micromachine_map_pool --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}" --tier "${SOAK_MATRIX_QUALIFICATION_TIER}" --field allow_failures)"
fi
if [[ ! "${SOAK_MATRIX_QUALIFICATION_TIER}" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "MicroMachine matrix rejected: invalid SOAK_MATRIX_QUALIFICATION_TIER=${SOAK_MATRIX_QUALIFICATION_TIER}." >&2
  exit 2
fi
if [[ ! "${SOAK_MATRIX_ALLOW_FAILURES}" =~ ^[01]$ ]]; then
  echo "MicroMachine matrix rejected: SOAK_MATRIX_ALLOW_FAILURES must be 0 or 1." >&2
  exit 2
fi
SOAK_MATRIX_AGGREGATE_ONLY="${SOAK_MATRIX_AGGREGATE_ONLY:-0}"
SOAK_MATRIX_MIN_PASSES="${SOAK_MATRIX_MIN_PASSES:-1}"
SOAK_MATRIX_ENABLED="${SOAK_MATRIX_ENABLED:-1}"
SOAK_MATRIX_HISTORY_JSON="${SOAK_MATRIX_HISTORY_JSON:-${SOAK_MATRIX_RUN_DIR}/soak_history_dashboard.json}"
SOAK_MATRIX_HISTORY_MD="${SOAK_MATRIX_HISTORY_MD:-${SOAK_MATRIX_RUN_DIR}/soak_history_dashboard.md}"
SOAK_MATRIX_TRIAGE_JSON="${SOAK_MATRIX_TRIAGE_JSON:-${SOAK_MATRIX_RUN_DIR}/triage_report.json}"
SOAK_MATRIX_TRIAGE_MD="${SOAK_MATRIX_TRIAGE_MD:-${SOAK_MATRIX_RUN_DIR}/triage_report.md}"

if [[ "${SOAK_MATRIX_QUALIFICATION_TIER}" =~ ^(production|extended)$ && "${SOAK_MATRIX_ALLOW_FAILURES}" == "1" ]]; then
  echo "MicroMachine matrix rejected: ${SOAK_MATRIX_QUALIFICATION_TIER} tier cannot set SOAK_MATRIX_ALLOW_FAILURES=1." >&2
  exit 2
fi

# The Python aggregator preserves per-case failure_codes, attempts, and
# artifact_manifest fields in matrix_report.json and the history dashboard.
mkdir -p "${SOAK_MATRIX_RUN_DIR}"

if [[ "${SOAK_MATRIX_ENABLED}" != "1" ]]; then
  python3 - <<'PY' "${SOAK_MATRIX_REPORT}" "${SOAK_MATRIX_HISTORY_JSON}" "${SOAK_MATRIX_HISTORY_MD}" "${SOAK_MATRIX_QUALIFICATION_TIER}" "${SOAK_MATRIX_ALLOW_FAILURES}" "${SOAK_MATRIX_STRATEGY_PROFILES}" "${SOAK_MATRIX_BUILD_IDENTITY}" "${SOAK_MATRIX_SIGNOFF_TIER}"
import json
import sys
from pathlib import Path

report = Path(sys.argv[1])
history_json = Path(sys.argv[2])
history_md = Path(sys.argv[3])
qualification_tier = sys.argv[4]
allow_failures = sys.argv[5] == "1"
strategy_profiles = [item for item in sys.argv[6].split() if item]
build_identity = sys.argv[7]
signoff_tier = sys.argv[8]
payload = {
    "status": "disabled",
    "ok": False,
    "enabled": False,
    "qualification_tier": qualification_tier,
    "allow_failures": allow_failures,
    "strategy_profiles": strategy_profiles,
    "build_identity": build_identity,
    "build_identity_ok": False,
    "build_identity_failure_codes": ["disabled"],
    "case_count": 0,
    "passed": 0,
    "failed": 0,
    "cases": [],
}
dashboard = {
    "status": "disabled",
    "ok": False,
    "run_count": 1,
    "passed_runs": 0,
    "failed_runs": 1,
    "case_count": 0,
    "passed_cases": 0,
    "failed_cases": 0,
    "failure_codes": [],
    "maps": [],
    "enemy_races": [],
    "enemy_difficulties": [],
    "target_frames": [],
    "streaks": {"current_status": "disabled", "current_pass_streak": 0, "current_fail_streak": 1},
    "production_signoff": {
        "status": "blocked",
        "ok": False,
        "signoff_tier": signoff_tier,
        "window_size": 0,
        "eligible_run_count": 0,
        "excluded_run_count": 1,
        "eligible_runs": [],
        "excluded_runs": [{"run_id": report.parent.name, "reason": "disabled"}],
        "coverage": {"required_count": 0, "observed_count": 0, "missing_count": 0, "missing": []},
        "blockers": [{"code": "no_eligible_production_runs"}],
    },
    "runs": [
        {
            "run_id": report.parent.name,
            "report": str(report),
            "artifact_dir": str(report.parent),
            "ok": False,
            "status": "disabled",
            "enabled": False,
            "case_count": 0,
            "passed": 0,
            "failed": 0,
            "qualification_tier": qualification_tier,
            "allow_failures": allow_failures,
            "strategy_profiles": strategy_profiles,
            "build_identity": build_identity,
            "build_identity_ok": False,
            "build_identity_failure_codes": ["disabled"],
            "failure_codes": [],
        }
    ],
}
report.parent.mkdir(parents=True, exist_ok=True)
history_json.parent.mkdir(parents=True, exist_ok=True)
report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
history_json.write_text(json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
history_md.write_text(
    "# MicroMachine Soak History\n\n"
    "- Status: `disabled`\n"
    "- Soak execution was skipped because `SOAK_MATRIX_ENABLED` was not `1`.\n"
    "- Production signoff: `blocked` (disabled run excluded).\n"
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
        preflight_args=(
          --map-file "${map_file}"
          --qualification-tier "${SOAK_MATRIX_QUALIFICATION_TIER}"
          --manifest "${SOAK_MATRIX_MAP_POOL_MANIFEST}"
          --output "${case_dir}/preflight_report.json"
          --write-soak-report "${case_dir}/soak_report.json"
          --enemy-race "${enemy_race}"
          --enemy-difficulty "${enemy_difficulty}"
          --target-frame "${SOAK_MATRIX_TARGET_FRAME}"
          --timeout-seconds "${SOAK_MATRIX_TIMEOUT_SECONDS}"
        )
        if [[ -n "${SOAK_MATRIX_MAP_ROOTS}" ]]; then
          IFS=':' read -r -a map_roots <<< "${SOAK_MATRIX_MAP_ROOTS}"
          for map_root in "${map_roots[@]}"; do
            if [[ -n "${map_root}" ]]; then
              preflight_args+=(--map-root "${map_root}")
            fi
          done
        fi
        set +e
        python3 -m starcraft_commander.micromachine_preflight "${preflight_args[@]}"
        preflight_exit="$?"
        set -e
        if [[ "${preflight_exit}" -ne 0 ]]; then
          echo "MicroMachine matrix preflight failed for ${case_id}; runtime skipped"
          if [[ "${SOAK_MATRIX_STOP_ON_FAILURE}" == "1" ]]; then
            break 3
          fi
          continue
        fi
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

matrix_report_args=(
  matrix-report
  --run-dir "${SOAK_MATRIX_RUN_DIR}"
  --output "${SOAK_MATRIX_REPORT}"
  --target-frame "${SOAK_MATRIX_TARGET_FRAME}"
  --timeout-seconds "${SOAK_MATRIX_TIMEOUT_SECONDS}"
  --qualification-tier "${SOAK_MATRIX_QUALIFICATION_TIER}"
  --strategy-profiles "${SOAK_MATRIX_STRATEGY_PROFILES}"
  --build-identity "${SOAK_MATRIX_BUILD_IDENTITY}"
  --build-identity-ok "${SOAK_MATRIX_BUILD_IDENTITY_OK}"
  --build-identity-failure-codes "${SOAK_MATRIX_BUILD_IDENTITY_FAILURE_CODES}"
)
if [[ "${SOAK_MATRIX_ALLOW_FAILURES}" == "1" ]]; then
  matrix_report_args+=(--allow-failures)
fi
python3 -m starcraft_commander.micromachine_soak_history "${matrix_report_args[@]}"

python3 -m starcraft_commander.micromachine_soak_history history-dashboard \
  --root "${SOAK_MATRIX_ARTIFACT_ROOT}" \
  --output-json "${SOAK_MATRIX_HISTORY_JSON}" \
  --output-markdown "${SOAK_MATRIX_HISTORY_MD}" \
  --signoff-tier "${SOAK_MATRIX_SIGNOFF_TIER}" \
  --required-map-files "${SOAK_MATRIX_SIGNOFF_MAP_FILES}" \
  --required-enemy-races "${SOAK_MATRIX_SIGNOFF_ENEMY_RACES}" \
  --required-enemy-difficulties "${SOAK_MATRIX_SIGNOFF_ENEMY_DIFFICULTIES}" \
  --required-strategy-profiles "${SOAK_MATRIX_SIGNOFF_STRATEGY_PROFILES}" \
  --required-build-identity "${SOAK_MATRIX_SIGNOFF_REQUIRED_BUILD_IDENTITY}"

python3 -m starcraft_commander.micromachine_triage \
  --matrix-report "${SOAK_MATRIX_REPORT}" \
  --output-json "${SOAK_MATRIX_TRIAGE_JSON}" \
  --output-markdown "${SOAK_MATRIX_TRIAGE_MD}" >/dev/null

set +e
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
    if payload.get("qualification_tier") in {"production", "extended"}:
        print(
            "MicroMachine matrix rejected: "
            f"{payload.get('qualification_tier')} tier requires failed=0."
        )
        raise SystemExit(1)
    raise SystemExit(0)
if allow_failures:
    print(
        "MicroMachine matrix rejected: "
        f"SOAK_MATRIX_ALLOW_FAILURES still requires at least {min_passes} passing case(s)."
    )
raise SystemExit(1)
PY
matrix_exit="$?"
set -e
if [[ "${matrix_exit}" -ne 0 && -f "${SOAK_MATRIX_TRIAGE_MD}" ]]; then
  echo "MicroMachine matrix triage summary: ${SOAK_MATRIX_TRIAGE_MD}" >&2
  sed -n '1,120p' "${SOAK_MATRIX_TRIAGE_MD}" >&2 || true
fi
exit "${matrix_exit}"
