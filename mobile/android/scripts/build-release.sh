#!/usr/bin/env bash
# Build locally only. Never creates keys, installs, uploads or publishes.
# Runs the same APK checks a stage candidate gets; see docs/releasing.md.
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-unsigned}"
if [[ $# -gt 1 || "$mode" != unsigned && "$mode" != signed ]]; then
  echo "Usage: scripts/build-release.sh [unsigned|signed]" >&2
  exit 2
fi
: "${JAVA_HOME:?Set JAVA_HOME to JDK 21}"
: "${ANDROID_HOME:=${ANDROID_SDK_ROOT:?Set ANDROID_HOME or ANDROID_SDK_ROOT}}"
export ANDROID_HOME
signing_names=(RATICODE_RELEASE_STORE_FILE RATICODE_RELEASE_STORE_PASSWORD RATICODE_RELEASE_KEY_ALIAS RATICODE_RELEASE_KEY_PASSWORD)
properties="${RATICODE_RELEASE_SIGNING_PROPERTIES:-}"
if [[ "$mode" == unsigned && -v RATICODE_RELEASE_SIGNING_PROPERTIES ]]; then
  echo "Unsigned mode requires RATICODE_RELEASE_SIGNING_PROPERTIES to be unset." >&2
  exit 2
fi
for name in "${signing_names[@]}"; do
  # Signed mode takes either the properties file (local builds) or all four variables (CI).
  if [[ "$mode" == signed && -z "$properties" && -z "${!name:-}" ]]; then
    echo "Signed mode requires RATICODE_RELEASE_SIGNING_PROPERTIES or all four RATICODE_RELEASE_* signing variables." >&2
    exit 2
  fi
  if [[ "$mode" == unsigned && -v "$name" ]]; then
    echo "Unsigned mode requires signing variables to be unset." >&2
    exit 2
  fi
done
if [[ "$mode" == signed && -z "${ANDROID_SIGNING_CERT_SHA256:-}" && -n "$properties" ]]; then
  # The fingerprint is public, so the properties file may record it.
  ANDROID_SIGNING_CERT_SHA256="$(sed -n 's/^certificateSha256=//p' "$properties" | tr -d '[:space:]')"
  export ANDROID_SIGNING_CERT_SHA256
fi
if [[ "$mode" == signed && -z "${ANDROID_SIGNING_CERT_SHA256:-}" ]]; then
  echo "Signed mode requires ANDROID_SIGNING_CERT_SHA256, the release certificate's recorded fingerprint." >&2
  exit 2
fi

build_tools="${RATICODE_ANDROID_BUILD_TOOLS:-36.0.0}"
if [[ ! -x "$ANDROID_HOME/build-tools/$build_tools/apksigner" ]]; then
  echo "Install Android build-tools $build_tools or set RATICODE_ANDROID_BUILD_TOOLS." >&2
  exit 2
fi

./gradlew assembleRelease bundleRelease lintRelease --console=plain --max-workers=2
# Version, SDK levels, debuggable flag, backups, cleartext, permissions, exported
# components, 16 KB native alignment and the signature (or its deliberate absence).
python3 scripts/release-candidate.py check-apk --signing "$mode"
# The same for the App Bundle Google Play takes, plus Play's download-size limit.
python3 scripts/release-candidate.py check-bundle --signing "$mode"
echo "Release acceptance and publication are separate steps; see docs/releasing.md."
