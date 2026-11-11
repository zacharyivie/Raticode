#!/usr/bin/env bash
# Runs one instrumented test against the real desktop code in this checkout.
# Needs a running headless emulator and the repository's .venv. Usage:
#   ./scripts/run-desktop-fixture-test.sh [test class] [fixture flags...]
# The fixture uses temporary keys and a canned provider. The test resets the app's data.
set -euo pipefail
cd "$(dirname "$0")/.."

test_class="${1:-dev.raticode.mobile.LiveDeviceTest}"
[[ $# -eq 0 ]] || shift
python="$PWD/../../.venv/bin/python"
[[ -x "$python" ]] || { echo "Create the repository's .venv first (uv sync at the repository root)." >&2; exit 2; }
adb="${ANDROID_HOME:?Set ANDROID_HOME to your Android SDK}/platform-tools/adb"
if [[ -z "${ANDROID_SERIAL:-}" ]]; then
  mapfile -t devices < <("$adb" devices | awk 'NR>1 && $2=="device" {print $1}')
  (( ${#devices[@]} == 1 )) || { echo "Set ANDROID_SERIAL to one private emulator." >&2; exit 2; }
  export ANDROID_SERIAL="${devices[0]}"
fi

# Fixture files stay off /tmp, which is often RAM-backed.
work_root="${XDG_CACHE_HOME:-$HOME/.cache}/raticode-fixture"
mkdir -p -m 700 "$work_root"
work="$(mktemp -d "$work_root/run.XXXXXX")"
fixture=""
cleanup() {
  status=$?
  [[ -z "$fixture" ]] || kill "$fixture" 2>/dev/null || true
  if [[ "$status" != 0 ]]; then
    cat "$work/fixture.log" >&2 || true
  fi
  rm -rf "$work"
}
trap cleanup EXIT

remote=/data/local/tmp/raticode-test-invitation.txt
RATICODE_FIXTURE_OUT="$work" PYTHONDONTWRITEBYTECODE=1 \
  "$python" scripts/live-device-fixture.py "$@" >"$work/fixture.log" 2>&1 &
fixture=$!
for _ in {1..60}; do
  [[ -s "$work/raticode-test-invitation.txt" ]] && break
  kill -0 "$fixture" 2>/dev/null || { echo "The desktop fixture stopped." >&2; exit 1; }
  sleep 1
done
[[ -s "$work/raticode-test-invitation.txt" ]] || { echo "The desktop fixture never wrote an invitation." >&2; exit 1; }
"$adb" -s "$ANDROID_SERIAL" push "$work/raticode-test-invitation.txt" "$remote" >/dev/null
./gradlew connectedDebugAndroidTest --console=plain \
  -Pandroid.testInstrumentationRunnerArguments.class="$test_class" \
  -Pandroid.testInstrumentationRunnerArguments.deviceInvitationFile="$remote"
