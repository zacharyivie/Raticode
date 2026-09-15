#!/usr/bin/env bash
set -euo pipefail
: "${CSC_LINK:?Missing base64 Developer ID certificate}"
: "${CSC_KEY_PASSWORD:?Missing certificate password}"
: "${GOFER_CODESIGN_IDENTITY:?Missing Developer ID signing identity}"
: "${APPLE_ID:?Missing notarization Apple ID}"
: "${APPLE_APP_SPECIFIC_PASSWORD:?Missing notarization password}"
: "${APPLE_TEAM_ID:?Missing Apple team ID}"
keychain="$RUNNER_TEMP/raticode-signing.keychain-db"
certificate="$RUNNER_TEMP/raticode-signing.p12"
keychain_password="$(openssl rand -hex 32)"
export RATICODE_SIGNING_CERTIFICATE="$certificate"
python - <<'PY'
import base64
import os
from pathlib import Path
path = Path(os.environ["RATICODE_SIGNING_CERTIFICATE"])
path.write_bytes(base64.b64decode(os.environ["CSC_LINK"], validate=True))
path.chmod(0o600)
PY
security create-keychain -p "$keychain_password" "$keychain"
security set-keychain-settings -lut 21600 "$keychain"
security unlock-keychain -p "$keychain_password" "$keychain"
security import "$certificate" -k "$keychain" -P "$CSC_KEY_PASSWORD" -T /usr/bin/codesign
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$keychain_password" "$keychain" >/dev/null
security list-keychains -d user -s "$keychain" login.keychain-db
rm -f "$certificate"
