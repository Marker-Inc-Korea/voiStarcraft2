#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
BLACKBOARD_ROOT="${BLACKBOARD_ROOT:-/private/tmp/voi-mm-strategy-matrix}"
PROFILES=(${SMOKE_STRATEGY_MATRIX_PROFILES:-bio_pressure tank_defensive_hold mech_transition drop_harassment scouting_map_control expand_macro})
MIN_TELEMETRY_FRAME="${MIN_TELEMETRY_FRAME:-5200}"
SMOKE_MAX_ATTEMPTS="${SMOKE_MAX_ATTEMPTS:-1}"
SMOKE_FORCE_STEP_MODE="${SMOKE_FORCE_STEP_MODE:-1}"
MATRIX_RUN_ID="${MATRIX_RUN_ID:-$(date +%Y%m%d%H%M%S)-$$}"
MATRIX_RUN_ROOT="${BLACKBOARD_ROOT}/runs/${MATRIX_RUN_ID}"
MATRIX_RETAIN_RUNS="${MATRIX_RETAIN_RUNS:-3}"

mkdir -p "${MATRIX_RUN_ROOT}"

prune_old_matrix_runs() {
  local runs_root="${BLACKBOARD_ROOT}/runs"
  local retain="${MATRIX_RETAIN_RUNS}"
  if [[ "${retain}" == "all" || "${retain}" == "0" ]]; then
    return
  fi
  if ! [[ "${retain}" =~ ^[0-9]+$ ]]; then
    echo "MicroMachine strategy matrix rejected MATRIX_RETAIN_RUNS=${retain}; use an integer, 0, or all." >&2
    exit 2
  fi
  local all_runs
  all_runs="$(find "${runs_root}" -mindepth 1 -maxdepth 1 -type d ! -name "${MATRIX_RUN_ID}" | sort)"
  local total_count
  total_count="$(printf '%s\n' "${all_runs}" | sed '/^$/d' | wc -l | tr -d ' ')"
  local prune_count=$(( total_count - retain ))
  if (( prune_count <= 0 )); then
    return
  fi
  printf '%s\n' "${all_runs}" | sed '/^$/d' | head -n "${prune_count}" | while IFS= read -r old_run; do
    rm -rf "${old_run}"
  done
}

prune_old_matrix_runs

summary="${MATRIX_RUN_ROOT}/strategy_matrix_summary.jsonl"
: > "${summary}"

for profile in "${PROFILES[@]}"; do
  case "${profile}" in
    bio_pressure|marine_rush|tank_defensive_hold|siege_contain|contain_enemy_natural|mech_transition|drop_harassment|worker_line_harassment|scouting_map_control|expand_macro|anti_air_response)
      ;;
    *)
      echo "MicroMachine strategy matrix rejected unsupported profile: ${profile}" >&2
      exit 2
      ;;
  esac

  run_dir="${MATRIX_RUN_ROOT}/${profile}"
  echo "Starting MicroMachine strategy matrix profile=${profile} blackboard=${run_dir}"
  if BLACKBOARD_DIR="${run_dir}" \
    SMOKE_STRATEGY_PROFILE_NAME="${profile}" \
    MIN_TELEMETRY_FRAME="${MIN_TELEMETRY_FRAME}" \
    SMOKE_MAX_ATTEMPTS="${SMOKE_MAX_ATTEMPTS}" \
    SMOKE_FORCE_STEP_MODE="${SMOKE_FORCE_STEP_MODE}" \
    "${SCRIPT_DIR}/smoke_macos_local.sh"; then
    PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python3 - <<'PY' "${summary}" "${profile}" "${run_dir}"
import json
import sys
import time
from pathlib import Path

from starcraft_commander.micromachine_production_evidence import (
    expected_production_pairs,
    find_causal_production_evidence,
)

summary = Path(sys.argv[1])
profile = sys.argv[2]
root = Path(sys.argv[3])
telemetry_path = root / "latest_telemetry.json"

def load_json_retry(path):
    last_error = None
    for _ in range(8):
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            last_error = exc
            time.sleep(0.05)
    raise SystemExit(f"could not read stable JSON from {path}: {last_error}")

def load_latest_or_archive(root):
    try:
        return load_json_retry(root / "latest_telemetry.json")
    except SystemExit as latest_error:
        archive = root / "telemetry.jsonl"
        last_valid = None
        if archive.exists():
            for line in archive.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    last_valid = json.loads(line)
                except json.JSONDecodeError:
                    continue
        if last_valid is not None:
            return last_valid
        raise latest_error

payload = load_latest_or_archive(root)
production = payload.get("managers", {}).get("ProductionManager", {})
workers = payload.get("managers", {}).get("WorkerManager", {})

def production_entries():
    archive = root / "telemetry.jsonl"
    if archive.exists():
        for line in archive.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield entry
    yield payload

def choose_summary_production():
    expected_doctrine = profile
    expected_pairs = expected_production_pairs(
        expected_doctrine,
    )
    expected_update_id = str(production.get("policy_update_id", "") or "")
    expected_issued_frame = int(production.get("policy_issued_at_frame", 0) or 0)
    if not expected_pairs:
        return production
    causal = find_causal_production_evidence(
        tuple(production_entries()),
        expected_doctrine=expected_doctrine,
        expected_update_id=expected_update_id,
        expected_pairs=expected_pairs,
        min_doctrine_frame=expected_issued_frame,
    )
    if not causal.matched:
        raise SystemExit(
            "strategy matrix summary missing causal production evidence: "
            f"profile={profile}, expected_pairs={sorted(expected_pairs)}, "
            f"observed_actions={sorted(causal.observed_actions)}, "
            f"observed_actual_commands={sorted(causal.observed_actual_commands)}"
        )
    summary_candidate = dict(causal.doctrine_entry or {})
    actual_candidate = causal.actual_command_entry or {}
    for key in (
        "actual_production_command_issued_count",
        "last_actual_production_command",
        "last_actual_production_command_kind",
        "last_actual_production_command_item",
        "last_actual_production_command_update_id",
        "last_actual_production_command_frame",
    ):
        summary_candidate[key] = actual_candidate.get(key)
    return summary_candidate

summary_production = choose_summary_production()
summary.write_text(
    summary.read_text()
    + json.dumps(
        {
            "profile": profile,
            "status": "passed",
            "frame": payload.get("frame", 0),
            "strategy_doctrine": summary_production.get("strategy_doctrine"),
            "last_doctrine_action": summary_production.get("last_doctrine_action"),
            "last_doctrine_queue_item": summary_production.get("last_doctrine_queue_item"),
            "last_doctrine_evidence": summary_production.get("last_doctrine_evidence"),
            "actual_production_command_issued_count": summary_production.get("actual_production_command_issued_count"),
            "last_actual_production_command": summary_production.get("last_actual_production_command"),
            "last_actual_production_command_frame": summary_production.get("last_actual_production_command_frame"),
            "combat_actual_command_issued_count": payload.get("managers", {}).get("CombatCommander", {}).get("actual_command_issued_count"),
            "combat_last_issued_action": payload.get("managers", {}).get("CombatCommander", {}).get("last_issued_action"),
            "scout_actual_command_issued_count": payload.get("managers", {}).get("ScoutManager", {}).get("actual_command_issued_count"),
            "scout_last_actual_command": payload.get("managers", {}).get("ScoutManager", {}).get("last_actual_command"),
            "summary_evidence_source": "expected_archive_match" if summary_production is not production else "latest",
            "latest_doctrine_action": production.get("last_doctrine_action"),
            "latest_doctrine_queue_item": production.get("last_doctrine_queue_item"),
            "latest_doctrine_evidence": production.get("last_doctrine_evidence"),
            "latest_actual_production_command": production.get("last_actual_production_command"),
            "worker_trace_status": workers.get("last_trace_status"),
            "worker_self_position_blocks": workers.get("self_position_command_block_count"),
            "worker_repeat_order_suppressions": workers.get("repeat_order_suppressed_count"),
            "worker_root_cause_status": workers.get("root_cause_status"),
            "worker_root_cause_reason": workers.get("root_cause_reason"),
            "blackboard_dir": str(root),
        },
        sort_keys=True,
    )
    + "\n"
)
PY
  else
    python3 - <<'PY' "${summary}" "${profile}" "${run_dir}"
import json
import sys
from pathlib import Path

summary = Path(sys.argv[1])
profile = sys.argv[2]
root = Path(sys.argv[3])
frame = 0
telemetry_path = root / "latest_telemetry.json"
if telemetry_path.exists():
    try:
        frame = int(json.loads(telemetry_path.read_text()).get("frame") or 0)
    except Exception:
        archive = root / "telemetry.jsonl"
        if archive.exists():
            for line in archive.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    frame = int(json.loads(line).get("frame") or frame)
                except Exception:
                    continue
summary.write_text(
    summary.read_text()
    + json.dumps(
        {
            "profile": profile,
            "status": "failed",
            "frame": frame,
            "blackboard_dir": str(root),
        },
        sort_keys=True,
    )
    + "\n"
)
PY
    echo "MicroMachine strategy matrix failed for profile=${profile}; summary=${summary}" >&2
    exit 1
  fi
done

echo "MicroMachine strategy matrix passed: ${summary}"
