#!/usr/bin/env bash
# Each phase fits the managed command limit; no tests are omitted.
set -eo pipefail
raticode_check_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$raticode_check_root"
source "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
nvm use "$(cat .nvmrc)"
export PYTHONPATH="$raticode_check_root/src${PYTHONPATH:+:$PYTHONPATH}"
case "${1:-all}" in
  backend-[1-4]|backend-reconcile)
    .venv/bin/python scripts/check-swarm-backend.py "$1" ;;
  ruff) .venv/bin/ruff check src tests ;;
  mypy) .venv/bin/mypy src tests ;;
  frontend-test) npm --prefix frontend test ;;
  frontend-lint) npm --prefix frontend run lint ;;
  frontend-build) npm --prefix frontend run check:build ;;
  browser)
    if [[ -n "${DISPLAY:-}" ]]; then
      npm --prefix frontend run test:pairing-browser
    else
      xvfb-run -a npm --prefix frontend run test:pairing-browser
    fi ;;
  all)
    for phase in backend-1 backend-2 backend-3 backend-4 backend-reconcile ruff mypy frontend-test frontend-lint frontend-build browser; do
      bash "$0" "$phase"
    done ;;
  *) echo 'Unknown check phase' >&2; exit 2 ;;
esac
