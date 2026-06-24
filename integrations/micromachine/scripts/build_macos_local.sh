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
S2CLIENT_PATCH_FILE="${REPO_ROOT}/integrations/micromachine/patches/0001-s2client-macos-launchservices.patch"

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

mkdir -p "${ROOT_DIR}"
require_disposable_checkout_mutation "${S2CLIENT_DIR}" "${ROOT_DIR}" "mutate"
require_disposable_checkout_mutation "${MICROMACHINE_DIR}" "${ROOT_DIR}" "mutate"

if [[ ! -d "${S2CLIENT_DIR}/.git" ]]; then
  git clone https://github.com/Blizzard/s2client-api "${S2CLIENT_DIR}"
fi
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

if [[ ! -d "${MICROMACHINE_DIR}/.git" ]]; then
  git clone https://github.com/RaphaelRoyerRivard/MicroMachine "${MICROMACHINE_DIR}"
fi
git -C "${MICROMACHINE_DIR}" fetch --tags
git -C "${MICROMACHINE_DIR}" checkout "${MICROMACHINE_COMMIT}"
git -C "${MICROMACHINE_DIR}" reset --hard "${MICROMACHINE_COMMIT}"
safe_clean_git_checkout "${MICROMACHINE_DIR}" "${ROOT_DIR}"
git -C "${MICROMACHINE_DIR}" submodule update --init --recursive
git -C "${MICROMACHINE_DIR}" apply --check --ignore-space-change --whitespace=nowarn "${PATCH_FILE}"
git -C "${MICROMACHINE_DIR}" apply --ignore-space-change --whitespace=nowarn "${PATCH_FILE}"

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
  --output "${MICROMACHINE_BUILD_IDENTITY_REPORT}"

printf 'MicroMachine executable: %s\n' "${MICROMACHINE_BUILD_DIR}/bin/MicroMachine"
printf 'MicroMachine build identity report: %s\n' "${MICROMACHINE_BUILD_IDENTITY_REPORT}"
