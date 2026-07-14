#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/private/tmp/voi-micromachine-runtime}"
S2CLIENT_DIR="${S2CLIENT_DIR:-${ROOT_DIR}/s2client-api}"
MICROMACHINE_DIR="${MICROMACHINE_DIR:-${ROOT_DIR}/MicroMachine}"
S2CLIENT_BUILD_DIR="${S2CLIENT_BUILD_DIR:-${S2CLIENT_DIR}/build-latest}"
MICROMACHINE_BUILD_DIR="${MICROMACHINE_BUILD_DIR:-${MICROMACHINE_DIR}/build-latest-api}"
MICROMACHINE_COMMIT="${MICROMACHINE_COMMIT:-eb893161371dab975a0a7e600f9e250ac03ec1ef}"
S2CLIENT_COMMIT="${S2CLIENT_COMMIT:-614acc00abb5355e4c94a1b0279b46e9d845b7ce}"
MICROMACHINE_BUILD_IDENTITY_REPORT="${MICROMACHINE_BUILD_IDENTITY_REPORT:-${MICROMACHINE_BUILD_DIR}/voi_build_identity.json}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0001-macos-latest-s2client-policy-blackboard.patch"
TACTICAL_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0002-live-tactical-operation-fixes.patch"
PRODUCTION_FIX_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0003-production-live-qa-blockers.patch"
OPERATION_STATE_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0004-live-operation-state-machine.patch"
ADDON_RECOVERY_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0005-addon-relocation-recovery.patch"
GROUNDED_ADDON_CANDIDATE_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0006-grounded-addon-candidate-fix.patch"
GUARANTEED_PRODUCER_GROUNDING_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0007-guaranteed-producer-grounding.patch"
EMERGENCY_LAND_QUERY_FALLBACK_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0008-emergency-land-query-fallback.patch"
GROUNDED_PRODUCTION_OBSERVED_TARGETING_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0009-grounded-production-and-observed-targeting.patch"
EXACT_COMPOSITION_PRODUCTION_PROGRESS_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0010-exact-composition-production-progress.patch"
PRODUCTION_RESOURCE_OPERATION_PERSISTENCE_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0011-production-resource-operation-persistence.patch"
LIVE_OPERATION_UNBLOCK_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0012-live-operation-unblock.patch"
STABLE_FLANK_STAGE_LATCH_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0013-stable-flank-stage-latch.patch"
PRODUCTION_STAGING_OBSERVED_OPERATION_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0014-production-staging-and-observed-operation.patch"
ADDON_QUERY_FOOTPRINT_VALIDATION_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0015-addon-query-footprint-validation.patch"
AUTHORITATIVE_ADDON_PLACEMENT_QUERY_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0016-authoritative-addon-placement-query.patch"
AUTHORITATIVE_ADDON_EXECUTION_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0017-authoritative-addon-execution.patch"
CONTINUOUS_ARMY_MACRO_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0018-continuous-army-macro.patch"
CONTINUOUS_ARMY_ECONOMY_SCALING_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0019-continuous-army-economy-scaling.patch"
STANDING_COMPOSITION_REINFORCEMENT_WAVES_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0020-standing-composition-reinforcement-waves.patch"
OFFENSIVE_SWEEP_SELF_BASE_EXCLUSION_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0021-offensive-sweep-self-base-exclusion.patch"
BOUNDED_PLACEMENT_QUERY_CACHE_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0022-bounded-placement-query-cache.patch"
PRODUCTION_FACILITY_STABILITY_TANK_RECOVERY_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0023-production-facility-stability-and-tank-recovery.patch"
BALANCED_COMPOSITION_WAVE_PRODUCTION_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0024-balanced-composition-wave-production.patch"
EXACT_COMPOSITION_PRODUCTION_UNBLOCK_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0025-exact-composition-production-unblock.patch"
CONTINUOUS_COMBAT_PRODUCTION_RELAUNCH_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0026-continuous-combat-production-relaunch.patch"
RESOURCE_THROUGHPUT_EXPANSION_BACKOFF_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0027-resource-throughput-and-expansion-backoff.patch"
STARTUP_TELEMETRY_INITIALIZATION_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0028-startup-telemetry-initialization.patch"
GAS_WORKER_COMPLETION_CAP_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0029-gas-worker-completion-and-cap.patch"
STABLE_OFFENSIVE_SWEEP_TARGET_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0030-stable-offensive-sweep-target.patch"
ADAPTIVE_SUPPORT_COMPOSITION_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0031-adaptive-support-composition.patch"
OPERATION_SCOPED_ADAPTIVE_COMBAT_CLOSURE_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0032-operation-scoped-adaptive-combat-closure.patch"
S2CLIENT_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0001-s2client-macos-launchservices.patch"
BLACKBOARD_HEADER_FILE="${REPO_ROOT}/integrations/micromachine/voi_policy_blackboard.hpp"

canonical_checkout_path() {
  local path="$1"
  if [[ -e "${path}" ]]; then
    (cd "${path}" && pwd -P)
    return
  fi

  local parent
  local base
  parent="$(dirname "${path}")"
  base="$(basename "${path}")"
  if [[ -d "${parent}" ]]; then
    printf '%s/%s\n' "$(cd "${parent}" && pwd -P)" "${base}"
    return
  fi
  printf '%s/%s\n' "$(canonical_checkout_path "${parent}")" "${base}"
}

require_disposable_checkout_mutation() {
  local checkout_dir="$1"
  local expected_root="$2"
  local action="$3"
  local canonical_root
  local canonical_checkout
  canonical_root="$(canonical_checkout_path "${expected_root}")"
  canonical_checkout="$(canonical_checkout_path "${checkout_dir}")"

  case "${canonical_checkout}" in
    "${canonical_root}"/*)
      ;;
    *)
      if [[ "${MICROMACHINE_ALLOW_DESTRUCTIVE_CLEAN:-0}" != "1" ]]; then
        echo "Refusing to ${action} override checkout outside ${canonical_root}: ${canonical_checkout}" >&2
        echo "Set MICROMACHINE_ALLOW_DESTRUCTIVE_CLEAN=1 only for disposable external clones." >&2
        exit 2
      fi
      ;;
  esac
}

safe_clean_git_checkout() {
  local checkout_dir="$1"
  local expected_root="$2"
  require_disposable_checkout_mutation "${checkout_dir}" "${expected_root}" "git clean"
  git -C "${checkout_dir}" clean -fdx
}

is_valid_git_checkout() {
  local checkout_dir="$1"
  [[ -d "${checkout_dir}/.git" ]] && git -C "${checkout_dir}" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

prepare_git_checkout() {
  local checkout_dir="$1"
  local expected_root="$2"
  local repo_url="$3"
  local repo_name="$4"
  require_disposable_checkout_mutation "${checkout_dir}" "${expected_root}" "repair"

  if [[ -e "${checkout_dir}" && ! -d "${checkout_dir}/.git" ]]; then
    local quarantine_dir="${checkout_dir}.invalid.$(date +%Y%m%d%H%M%S).$$"
    echo "Invalid ${repo_name} checkout without .git; moving aside: ${quarantine_dir}" >&2
    mv "${checkout_dir}" "${quarantine_dir}"
  elif [[ -d "${checkout_dir}/.git" ]] && ! is_valid_git_checkout "${checkout_dir}"; then
    local quarantine_dir="${checkout_dir}.invalid.$(date +%Y%m%d%H%M%S).$$"
    echo "Invalid ${repo_name} git checkout; moving aside: ${quarantine_dir}" >&2
    mv "${checkout_dir}" "${quarantine_dir}"
  fi

  if [[ ! -d "${checkout_dir}/.git" ]]; then
    git clone "${repo_url}" "${checkout_dir}"
  fi
}

mkdir -p "${ROOT_DIR}"
require_disposable_checkout_mutation "${S2CLIENT_DIR}" "${ROOT_DIR}" "mutate"
require_disposable_checkout_mutation "${MICROMACHINE_DIR}" "${ROOT_DIR}" "mutate"

prepare_git_checkout "${S2CLIENT_DIR}" "${ROOT_DIR}" https://github.com/Blizzard/s2client-api s2client-api
git -C "${S2CLIENT_DIR}" fetch --tags
git -C "${S2CLIENT_DIR}" checkout "${S2CLIENT_COMMIT}"
git -C "${S2CLIENT_DIR}" reset --hard "${S2CLIENT_COMMIT}"
safe_clean_git_checkout "${S2CLIENT_DIR}" "${ROOT_DIR}"
git -C "${S2CLIENT_DIR}" submodule update --init --recursive
git -C "${S2CLIENT_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${S2CLIENT_PATCH_FILE}"
git -C "${S2CLIENT_DIR}" apply --ignore-space-change --whitespace=nowarn "${S2CLIENT_PATCH_FILE}"

cmake -S "${S2CLIENT_DIR}" -B "${S2CLIENT_BUILD_DIR}" \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build "${S2CLIENT_BUILD_DIR}" --parallel "${BUILD_JOBS:-8}"

prepare_git_checkout "${MICROMACHINE_DIR}" "${ROOT_DIR}" https://github.com/RaphaelRoyerRivard/MicroMachine MicroMachine
git -C "${MICROMACHINE_DIR}" fetch --tags
git -C "${MICROMACHINE_DIR}" checkout "${MICROMACHINE_COMMIT}"
git -C "${MICROMACHINE_DIR}" reset --hard "${MICROMACHINE_COMMIT}"
safe_clean_git_checkout "${MICROMACHINE_DIR}" "${ROOT_DIR}"
git -C "${MICROMACHINE_DIR}" submodule update --init --recursive
# The upstream MicroMachine commit contains a legacy non-UTF-8 comment divider
# in BuildingManager.cpp. Normalize it before applying our UTF-8 patch bundle so
# the canonical patch remains readable by tests and review tools.
perl -0pi -e 's/\xAF{8,}/---------------/g' "${MICROMACHINE_DIR}/src/BuildingManager.cpp"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${TACTICAL_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${TACTICAL_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${PRODUCTION_FIX_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${PRODUCTION_FIX_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${OPERATION_STATE_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${OPERATION_STATE_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${ADDON_RECOVERY_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${ADDON_RECOVERY_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${GROUNDED_ADDON_CANDIDATE_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${GROUNDED_ADDON_CANDIDATE_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${GUARANTEED_PRODUCER_GROUNDING_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${GUARANTEED_PRODUCER_GROUNDING_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${EMERGENCY_LAND_QUERY_FALLBACK_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${EMERGENCY_LAND_QUERY_FALLBACK_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${GROUNDED_PRODUCTION_OBSERVED_TARGETING_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${GROUNDED_PRODUCTION_OBSERVED_TARGETING_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${EXACT_COMPOSITION_PRODUCTION_PROGRESS_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${EXACT_COMPOSITION_PRODUCTION_PROGRESS_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${PRODUCTION_RESOURCE_OPERATION_PERSISTENCE_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${PRODUCTION_RESOURCE_OPERATION_PERSISTENCE_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${LIVE_OPERATION_UNBLOCK_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${LIVE_OPERATION_UNBLOCK_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${STABLE_FLANK_STAGE_LATCH_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${STABLE_FLANK_STAGE_LATCH_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${PRODUCTION_STAGING_OBSERVED_OPERATION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${PRODUCTION_STAGING_OBSERVED_OPERATION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${ADDON_QUERY_FOOTPRINT_VALIDATION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${ADDON_QUERY_FOOTPRINT_VALIDATION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${AUTHORITATIVE_ADDON_PLACEMENT_QUERY_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${AUTHORITATIVE_ADDON_PLACEMENT_QUERY_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${AUTHORITATIVE_ADDON_EXECUTION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${AUTHORITATIVE_ADDON_EXECUTION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${CONTINUOUS_ARMY_MACRO_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${CONTINUOUS_ARMY_MACRO_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${CONTINUOUS_ARMY_ECONOMY_SCALING_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${CONTINUOUS_ARMY_ECONOMY_SCALING_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${STANDING_COMPOSITION_REINFORCEMENT_WAVES_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${STANDING_COMPOSITION_REINFORCEMENT_WAVES_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${OFFENSIVE_SWEEP_SELF_BASE_EXCLUSION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${OFFENSIVE_SWEEP_SELF_BASE_EXCLUSION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${BOUNDED_PLACEMENT_QUERY_CACHE_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${BOUNDED_PLACEMENT_QUERY_CACHE_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${PRODUCTION_FACILITY_STABILITY_TANK_RECOVERY_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${PRODUCTION_FACILITY_STABILITY_TANK_RECOVERY_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${BALANCED_COMPOSITION_WAVE_PRODUCTION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${BALANCED_COMPOSITION_WAVE_PRODUCTION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${EXACT_COMPOSITION_PRODUCTION_UNBLOCK_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${EXACT_COMPOSITION_PRODUCTION_UNBLOCK_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${CONTINUOUS_COMBAT_PRODUCTION_RELAUNCH_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${CONTINUOUS_COMBAT_PRODUCTION_RELAUNCH_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${RESOURCE_THROUGHPUT_EXPANSION_BACKOFF_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${RESOURCE_THROUGHPUT_EXPANSION_BACKOFF_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${STARTUP_TELEMETRY_INITIALIZATION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${STARTUP_TELEMETRY_INITIALIZATION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${GAS_WORKER_COMPLETION_CAP_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${GAS_WORKER_COMPLETION_CAP_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${STABLE_OFFENSIVE_SWEEP_TARGET_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${STABLE_OFFENSIVE_SWEEP_TARGET_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${ADAPTIVE_SUPPORT_COMPOSITION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${ADAPTIVE_SUPPORT_COMPOSITION_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --check --ignore-space-change --whitespace=nowarn "${OPERATION_SCOPED_ADAPTIVE_COMBAT_CLOSURE_PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --recount --ignore-space-change --whitespace=nowarn "${OPERATION_SCOPED_ADAPTIVE_COMBAT_CLOSURE_PATCH_FILE}"
cp "${BLACKBOARD_HEADER_FILE}" "${MICROMACHINE_DIR}/src/voi_policy_blackboard.hpp"

cmake -S "${MICROMACHINE_DIR}" -B "${MICROMACHINE_BUILD_DIR}" \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DSC2Api_INCLUDE_DIR="${S2CLIENT_DIR}/include" \
  -DSC2Api_Proto_INCLUDE_DIR="${S2CLIENT_BUILD_DIR}/generated" \
  -DSC2Api_Protobuf_INCLUDE_DIR="${S2CLIENT_DIR}/contrib/protobuf/src" \
  -DSC2Api_SC2API_LIB="${S2CLIENT_BUILD_DIR}/bin/libsc2api.a" \
  -DSC2Api_SC2LIB_LIB="${S2CLIENT_BUILD_DIR}/bin/libsc2lib.a" \
  -DSC2Api_SC2UTILS_LIB="${S2CLIENT_BUILD_DIR}/bin/libsc2utils.a" \
  -DSC2Api_SC2PROTOCOL_LIB="${S2CLIENT_BUILD_DIR}/bin/libsc2protocol.a" \
  -DSC2Api_CIVETWEB_LIB="${S2CLIENT_BUILD_DIR}/bin/libcivetweb.a" \
  -DSC2Api_PROTOBUF_LIB="${S2CLIENT_BUILD_DIR}/bin/libprotobuf.a"
cmake --build "${MICROMACHINE_BUILD_DIR}" --parallel "${BUILD_JOBS:-8}"

python3 -m starcraft_commander.micromachine_build_identity \
  --micromachine-dir "${MICROMACHINE_DIR}" \
  --s2client-dir "${S2CLIENT_DIR}" \
  --micromachine-build-dir "${MICROMACHINE_BUILD_DIR}" \
  --micromachine-commit "${MICROMACHINE_COMMIT}" \
  --s2client-commit "${S2CLIENT_COMMIT}" \
  --micromachine-patch "${PATCH_FILE}" \
  --micromachine-tactical-patch "${TACTICAL_PATCH_FILE}" \
  --micromachine-production-fix-patch "${PRODUCTION_FIX_PATCH_FILE}" \
  --micromachine-operation-state-patch "${OPERATION_STATE_PATCH_FILE}" \
  --micromachine-addon-recovery-patch "${ADDON_RECOVERY_PATCH_FILE}" \
  --micromachine-grounded-addon-candidate-patch "${GROUNDED_ADDON_CANDIDATE_PATCH_FILE}" \
  --micromachine-guaranteed-producer-grounding-patch "${GUARANTEED_PRODUCER_GROUNDING_PATCH_FILE}" \
  --micromachine-emergency-land-query-fallback-patch "${EMERGENCY_LAND_QUERY_FALLBACK_PATCH_FILE}" \
  --micromachine-grounded-production-observed-targeting-patch "${GROUNDED_PRODUCTION_OBSERVED_TARGETING_PATCH_FILE}" \
  --micromachine-exact-composition-production-progress-patch "${EXACT_COMPOSITION_PRODUCTION_PROGRESS_PATCH_FILE}" \
  --micromachine-production-resource-operation-persistence-patch "${PRODUCTION_RESOURCE_OPERATION_PERSISTENCE_PATCH_FILE}" \
  --micromachine-live-operation-unblock-patch "${LIVE_OPERATION_UNBLOCK_PATCH_FILE}" \
  --micromachine-stable-flank-stage-latch-patch "${STABLE_FLANK_STAGE_LATCH_PATCH_FILE}" \
  --micromachine-production-staging-observed-operation-patch "${PRODUCTION_STAGING_OBSERVED_OPERATION_PATCH_FILE}" \
  --micromachine-addon-query-footprint-validation-patch "${ADDON_QUERY_FOOTPRINT_VALIDATION_PATCH_FILE}" \
  --micromachine-authoritative-addon-placement-query-patch "${AUTHORITATIVE_ADDON_PLACEMENT_QUERY_PATCH_FILE}" \
  --micromachine-authoritative-addon-execution-patch "${AUTHORITATIVE_ADDON_EXECUTION_PATCH_FILE}" \
  --micromachine-continuous-army-macro-patch "${CONTINUOUS_ARMY_MACRO_PATCH_FILE}" \
  --micromachine-continuous-army-economy-scaling-patch "${CONTINUOUS_ARMY_ECONOMY_SCALING_PATCH_FILE}" \
  --micromachine-standing-composition-reinforcement-waves-patch "${STANDING_COMPOSITION_REINFORCEMENT_WAVES_PATCH_FILE}" \
  --micromachine-offensive-sweep-self-base-exclusion-patch "${OFFENSIVE_SWEEP_SELF_BASE_EXCLUSION_PATCH_FILE}" \
  --micromachine-bounded-placement-query-cache-patch "${BOUNDED_PLACEMENT_QUERY_CACHE_PATCH_FILE}" \
  --micromachine-production-facility-stability-tank-recovery-patch "${PRODUCTION_FACILITY_STABILITY_TANK_RECOVERY_PATCH_FILE}" \
  --micromachine-balanced-composition-wave-production-patch "${BALANCED_COMPOSITION_WAVE_PRODUCTION_PATCH_FILE}" \
  --micromachine-exact-composition-production-unblock-patch "${EXACT_COMPOSITION_PRODUCTION_UNBLOCK_PATCH_FILE}" \
  --micromachine-continuous-combat-production-relaunch-patch "${CONTINUOUS_COMBAT_PRODUCTION_RELAUNCH_PATCH_FILE}" \
  --micromachine-resource-throughput-expansion-backoff-patch "${RESOURCE_THROUGHPUT_EXPANSION_BACKOFF_PATCH_FILE}" \
  --micromachine-startup-telemetry-initialization-patch "${STARTUP_TELEMETRY_INITIALIZATION_PATCH_FILE}" \
  --micromachine-gas-worker-completion-cap-patch "${GAS_WORKER_COMPLETION_CAP_PATCH_FILE}" \
  --micromachine-stable-offensive-sweep-target-patch "${STABLE_OFFENSIVE_SWEEP_TARGET_PATCH_FILE}" \
  --micromachine-adaptive-support-composition-patch "${ADAPTIVE_SUPPORT_COMPOSITION_PATCH_FILE}" \
  --micromachine-operation-scoped-adaptive-combat-closure-patch "${OPERATION_SCOPED_ADAPTIVE_COMBAT_CLOSURE_PATCH_FILE}" \
  --s2client-patch "${S2CLIENT_PATCH_FILE}" \
  --output "${MICROMACHINE_BUILD_IDENTITY_REPORT}"

printf 'MicroMachine executable: %s\n' "${MICROMACHINE_BUILD_DIR}/bin/MicroMachine"
printf 'MicroMachine build identity report: %s\n' "${MICROMACHINE_BUILD_IDENTITY_REPORT}"
