#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="${REPO_ROOT}/integrations/micromachine"
BUILD_DIR="${MICROMACHINE_NATIVE_TEST_BUILD_DIR:-${TMPDIR:-/tmp}/voi-micromachine-native-contracts}"
mkdir -p "${BUILD_DIR}"

CXX="${CXX:-c++}"
COMMON=(-std=c++17 -Wall -Wextra -pedantic -I "${ROOT_DIR}")
"${CXX}" "${COMMON[@]}" "${ROOT_DIR}/tests/direct_lease_bridge_test.cpp" \
  -o "${BUILD_DIR}/direct_lease_bridge_test"
"${CXX}" "${COMMON[@]}" "${ROOT_DIR}/tests/operation_completion_test.cpp" \
  -o "${BUILD_DIR}/operation_completion_test"
"${BUILD_DIR}/direct_lease_bridge_test"
"${BUILD_DIR}/operation_completion_test"
printf 'native MicroMachine contract tests passed\n'
