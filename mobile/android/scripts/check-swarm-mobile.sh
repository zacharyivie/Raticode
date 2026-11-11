#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${JAVA_HOME:?Set JAVA_HOME to JDK 21}"
: "${ANDROID_HOME:=${ANDROID_SDK_ROOT:?Set ANDROID_HOME or ANDROID_SDK_ROOT}}"
export ANDROID_HOME
./gradlew assembleDebug testDebugUnitTest lintDebug --console=plain
if [[ ! -x .venv-contract/bin/python ]]; then
  python3 -m venv .venv-contract
  .venv-contract/bin/pip install -r protocol/requirements.txt
fi
.venv-contract/bin/python scripts/check-contract.py
if [[ "${RATICODE_RUN_EMULATOR_TESTS:-0}" == 1 ]]; then
  ./gradlew connectedDebugAndroidTest --console=plain
fi
