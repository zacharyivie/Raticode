#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sdk_root="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}"
adb="$sdk_root/platform-tools/adb"
emulator="$sdk_root/emulator/emulator"
avd="${RATICODE_AVD:-Raticode_Pixel_8_API_36}"

if [[ ! -x "$adb" ]]; then
    echo "ADB not found. Set ANDROID_HOME to your Android SDK." >&2
    exit 1
fi
export ANDROID_HOME="$sdk_root"
mkdir -p "$project_root/artifacts"
if [[ -z "${ANDROID_SERIAL:-}" ]]; then
    mapfile -t devices < <("$adb" devices | awk 'NR>1 && $2=="device" {print $1}')
    if (( ${#devices[@]} > 1 )); then
        echo "Multiple devices are connected. Set ANDROID_SERIAL to the intended device." >&2
        exit 1
    elif (( ${#devices[@]} == 1 )); then
        export ANDROID_SERIAL="${devices[0]}"
    else
        if [[ ! -x "$emulator" ]] || ! "$emulator" -list-avds | grep -Fxq "$avd"; then
            echo "Create AVD '$avd' in Android Studio, or set RATICODE_AVD." >&2
            exit 1
        fi
        # A fixed console port makes device selection unambiguous after startup.
        export ANDROID_SERIAL=emulator-5580
        nohup "$emulator" -avd "$avd" -port 5580 -no-window -no-snapshot-save > "$project_root/artifacts/emulator.log" 2>&1 < /dev/null &
    fi
fi

booted=false
for ((attempt = 0; attempt < 120; attempt++)); do
    if [[ "$("$adb" -s "$ANDROID_SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; then
        booted=true
        break
    fi
    sleep 2
done
if [[ "$booted" != true ]]; then
    echo "Device did not boot within 4 minutes. Check artifacts/emulator.log or ANDROID_SERIAL." >&2
    exit 1
fi
cd "$project_root"
./gradlew assembleDebug --console=plain
"$adb" -s "$ANDROID_SERIAL" install -r app/build/outputs/apk/debug/app-debug.apk
"$adb" -s "$ANDROID_SERIAL" shell am start -W -n dev.raticode.mobile/.MainActivity
