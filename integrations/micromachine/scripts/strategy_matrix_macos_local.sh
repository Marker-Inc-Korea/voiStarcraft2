#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BLACKBOARD_ROOT="${BLACKBOARD_ROOT:-/private/tmp/voi-mm-strategy-matrix}"
PROFILES=(${SMOKE_STRATEGY_MATRIX_PROFILES:-bio_pressure tank_defensive_hold mech_transition drop_harassment expand_macro})
MIN_TELEMETRY_FRAME="${MIN_TELEMETRY_FRAME:-5200}"
SMOKE_MAX_ATTEMPTS="${SMOKE_MAX_ATTEMPTS:-1}"
SMOKE_FORCE_STEP_MODE="${SMOKE_FORCE_STEP_MODE:-1}"
MATRIX_RUN_ID="${MATRIX_RUN_ID:-$(date +%Y%m%d%H%M%S)-$$}"
MATRIX_RUN_ROOT="${BLACKBOARD_ROOT}/runs/${MATRIX_RUN_ID}"

mkdir -p "${MATRIX_RUN_ROOT}"

summary="${MATRIX_RUN_ROOT}/strategy_matrix_summary.jsonl"
: > "${summary}"

for profile in "${PROFILES[@]}"; do
  case "${profile}" in
    bio_pressure|marine_rush|tank_defensive_hold|siege_contain|contain_enemy_natural|mech_transition|drop_harassment|worker_line_harassment|expand_macro|anti_air_response)
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
    python3 - <<'PY' "${summary}" "${profile}" "${run_dir}"
import json
import sys
import time
from pathlib import Path

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
expected_contracts = {
    "marine_rush": ({"marine_rush"}, {("marine_pressure", "Marine"), ("bio_facility", "Barracks")}),
    "bio_pressure": ({"bio_pressure"}, {("bio_marauder_techlab", "BarracksTechLab"), ("bio_marauder_support", "Marauder"), ("starport_transition", "Starport"), ("medivac_drop_support", "Medivac")}),
    "tank_defensive_hold": ({"tank_defensive_hold"}, {("factory_transition", "Factory"), ("factory_techlab", "FactoryTechLab"), ("siege_tank_composition", "SiegeTank")}),
    "siege_contain": ({"siege_contain"}, {("factory_transition", "Factory"), ("factory_techlab", "FactoryTechLab"), ("siege_tank_composition", "SiegeTank")}),
    "contain_enemy_natural": ({"contain_enemy_natural"}, {("factory_transition", "Factory"), ("factory_techlab", "FactoryTechLab"), ("siege_tank_composition", "SiegeTank")}),
    "mech_transition": ({"mech_transition"}, {("factory_transition", "Factory"), ("factory_techlab", "FactoryTechLab"), ("hellion_harassment", "Hellion"), ("cyclone_mech", "Cyclone"), ("siege_tank_composition", "SiegeTank"), ("thor_mech", "Thor")}),
    "drop_harassment": ({"drop_harassment"}, {("starport_transition", "Starport"), ("drop_reactor", "StarportReactor"), ("medivac_drop_support", "Medivac"), ("factory_transition", "Factory"), ("hellion_harassment", "Hellion"), ("reaper_harassment", "Reaper")}),
    "worker_line_harassment": ({"worker_line_harassment"}, {("starport_transition", "Starport"), ("drop_reactor", "StarportReactor"), ("medivac_drop_support", "Medivac"), ("factory_transition", "Factory"), ("hellion_harassment", "Hellion"), ("reaper_harassment", "Reaper")}),
    "expand_macro": ({"expand_macro"}, {("expand_macro", "CommandCenter")}),
    "anti_air_response": ({"anti_air_response"}, {("starport_transition", "Starport"), ("anti_air_detection_support", "EngineeringBay"), ("anti_air_viking", "Viking")}),
}
allowed_evidence = {"queued"}

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
    expected_doctrines, expected_pairs = expected_contracts.get(
        profile,
        (set(), set()),
    )
    expected_update_id = str(production.get("policy_update_id", "") or "")
    expected_issued_frame = int(production.get("policy_issued_at_frame", 0) or 0)
    best = None
    for entry in production_entries():
        candidate = entry.get("managers", {}).get("ProductionManager", {})
        if not isinstance(candidate, dict):
            continue
        doctrine = str(candidate.get("strategy_doctrine", "") or "")
        last_doctrine = str(candidate.get("last_doctrine", "") or "")
        action = str(candidate.get("last_doctrine_action", "") or "")
        item = str(candidate.get("last_doctrine_queue_item", "") or "")
        evidence = str(candidate.get("last_doctrine_evidence", "") or "")
        update_id = str(candidate.get("policy_update_id", "") or "")
        last_update_id = str(candidate.get("last_doctrine_update_id", "") or "")
        if expected_doctrines and (
            doctrine not in expected_doctrines or last_doctrine not in expected_doctrines
        ):
            continue
        if expected_pairs and (action, item) not in expected_pairs:
            continue
        if evidence not in allowed_evidence:
            continue
        if not expected_update_id or update_id != expected_update_id or last_update_id != expected_update_id:
            continue
        if candidate.get("last_doctrine_fresh") is not True:
            continue
        if int(candidate.get("last_doctrine_frame", 0) or 0) < expected_issued_frame:
            continue
        best = candidate
    return best or production

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
            "summary_evidence_source": "expected_archive_match" if summary_production is not production else "latest",
            "latest_doctrine_action": production.get("last_doctrine_action"),
            "latest_doctrine_queue_item": production.get("last_doctrine_queue_item"),
            "latest_doctrine_evidence": production.get("last_doctrine_evidence"),
            "worker_trace_status": workers.get("last_trace_status"),
            "worker_self_position_blocks": workers.get("self_position_command_block_count"),
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
