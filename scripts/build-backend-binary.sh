#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
# Keep the cache in the git-ignored .uv-cache, like build-backend-binary.cjs. A fixed
# /tmp path could be created first, and filled, by another account on this computer.
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
uv run --locked --extra xlsx --extra devices pyinstaller --clean --noconfirm raticode.spec
