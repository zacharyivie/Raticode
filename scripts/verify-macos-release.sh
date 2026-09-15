#!/usr/bin/env bash
set -euo pipefail
# electron-builder notarizes the app. Verify that before publishing its zip.
app_count=0
while IFS= read -r -d '' app; do
  codesign --verify --deep --strict --verbose=2 "$app"
  spctl --assess --type execute --verbose=2 "$app"
  xcrun stapler validate "$app"
  app_count=$((app_count + 1))
done < <(find frontend/release -maxdepth 2 -name '*.app' -print0)
[ "$app_count" -gt 0 ] || { echo "No signed application found" >&2; exit 1; }
notarize() {
  xcrun notarytool submit "$1" --apple-id "$APPLE_ID" \
    --password "$APPLE_APP_SPECIFIC_PASSWORD" --team-id "$APPLE_TEAM_ID" \
    --wait --output-format json > "$2"
  python - "$2" <<'PY'
import json
import sys
with open(sys.argv[1]) as result:
    if json.load(result).get("status") != "Accepted":
        raise SystemExit("Notarization was not accepted")
PY
}
for dmg in frontend/release/*.dmg; do
  codesign --verify --strict --verbose=2 "$dmg"
  notarize "$dmg" frontend/release/notarization-macos-dmg.json
  # Do not mutate the DMG after electron-builder writes update hashes/blockmaps.
  # The app inside is stapled; the outer DMG has an online notarization ticket.
  spctl --assess --type open --context context:primary-signature --verbose=2 "$dmg"
done
# The standalone CLI needs its own ticket, separate from the enclosing app.
for cli in frontend/release/gof-macos-*; do
  codesign --verify --strict --verbose=2 "$cli"
  archive="$RUNNER_TEMP/raticode-cli-notarization.zip"
  ditto -c -k --keepParent "$cli" "$archive"
  notarize "$archive" frontend/release/notarization-macos-cli.json
  spctl --assess --type execute --verbose=2 "$cli"
  rm -f "$archive"
done
