#!/usr/bin/env bash
# Deterministic delivery assets. Preserve full-size source artwork and alignment.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p frontend/src/assets/rem-options/display
for pose in indigo-android-open indigo-android indigo-android-wave indigo-android-sleep; do
  for size in 224 336; do
    magick "frontend/src/assets/rem-options/$pose.png" -resize "${size}x${size}" -strip "frontend/src/assets/rem-options/display/$pose-$size.png"
  done
done
