# Agent Instructions

This file provides guidance for AI coding agents, including Codex, Claude Code, and other agent providers, when working in this repository.

## Project Summary

Raticode is a Python CLI (the `Raticode` command) and desktop workflow studio. Workflows use the Rattish language in `workflow.rattish`; the compiler validates them and emits versioned JSON IR. Execution supports explicit routes, joins, branches, and cycles.

## Commands

```bash
# Install for development
pip install -e ".[dev]"

# Run all tests
python -m pytest

# Run specific test categories
python -m pytest tests/unit/
python -m pytest tests/integration/
python -m pytest tests/regression/

# Run a single test
python -m pytest tests/unit/test_executor.py::test_name -v

# Type checking
mypy src tests

# Linting and formatting
ruff check src tests --fix
ruff format src tests
```

## Repository layout

- `src/`, `frontend/`, `tests/`, `rattish/`: the desktop app, CLI and Rattish.
- `website/`: the public site, with its own `package.json`.
- `mobile/android/`: Raticode Mobile, a Gradle project with its own `AGENTS.md`. Run its commands from that folder. Its checks are `./gradlew assembleDebug testDebugUnitTest lintDebug`, `python3 scripts/version.py check`, `python3 -m unittest discover -s scripts -p 'test_*.py'` and `python3 scripts/check-contract.py`. Build it only when a change needs it, because a build adds about 1.4 GB. Point `ANDROID_TMP` and Gradle output away from `/tmp`.
- `src/gofer/devices/protocol/v2/`: the one copy of the desktop-phone protocol. The desktop loads it at run time and the phone's contract check reads it. Change it and both apps in one commit.
- The apps release separately from the same `stage` and `main`: `vX.Y.Z` tags publish the desktop and `mobile-vX.Y.Z` tags publish the phone app. See `docs/releasing.md`.

## Documentation: the website is the source of truth

- The website (`website/docs/`, published from `website/` to https://raticode.dev/) is the single source of truth for Raticode documentation, for the desktop app, the CLI and Raticode Mobile. The READMEs link to the website for user-facing behavior and must not be the only place a user-visible feature is described.
- `docs/` and `mobile/android/docs/` are git-ignored except for files that scripts, tests and release tooling read: `docs/releasing.md`, `docs/security/release-verification.md`, `docs/cli-parity.json`, `mobile/android/docs/releasing.md` and `mobile/android/docs/versioning.md`. Don't commit design notes, plans, reports, research or test evidence there; they belong in the maintainer's Second Brain, outside the repository. If a script or test starts reading a new file under `docs/`, add a `!` exception for it in `.gitignore`.
- Any new feature, or any change that alters how an existing feature works in a way users would notice (a renamed screen or setting, new steps, a removed option, a changed limit or default), must update the matching website pages in the same branch, before it is merged to `dev`. Keep installed behavior separate from planned work, and label experimental features (Feature Preview) and unreleased ones as such.
- Before fast-forwarding `dev`, run `python3 scripts/check-website-docs.py`. It fails when a branch with a `feat:` commit changes app code under `frontend/`, `src/gofer/` or `mobile/android/app/src/main/` without touching `website/`. If no user-facing documentation changes, add a `Docs-not-needed: <reason>` line to the commit message; the same line works in a pull request description. The `Website docs` GitHub workflow runs this check on pull requests.
- After editing the website, run from `website/` (with `nvm use`): `npm ci`, then `npm run check` (type check, build, and internal link and anchor check). `npm run check:downloads` confirms the download links match the newest published release. When preparing a release, `npm run set-release -- X.Y.Z` moves the site to that version. The `Website docs` workflow deploys the site to GitHub Pages from `main` only after both checks pass; see `docs/releasing.md`. Check full Rattish examples with `Raticode rattish check`.

## Architecture

Layer structure:

- `src/gofer/cli/` - Typer CLI; routes commands to workflow, agent, schedule, and builder command modules.
- `src/gofer/core/` - Domain logic: operations, graph, workflow, executor, and scheduler.
- `src/gofer/subscriptions/` - ABC for LLM CLI backends such as Claude Code and Codex. Subclasses primarily override `_build_command()`.
- `src/gofer/prompts/` - Markdown prompt templates with `{{var}}` interpolation.
- `src/gofer/utils/` - Subprocess runner, XDG paths, name-based registry, and logging.

Execution flow:

1. The Rattish lexer and parser build a source-faithful AST.
2. Semantic analysis applies machine-readable node and provider contracts.
3. The compiler emits schema-valid, versioned JSON IR with explicit defaults.
4. Preflight checks whether the compiled workflow has the resources needed to run.
5. The activation runtime executes routes, joins, branches, and cycles while preserving activation lineage.

Key patterns:

- Built-in node behavior must agree with the Rattish machine contract, compiler, preflight, runtime handler, and conformance fixtures.
- Loop nodes provide runtime fan-out with explicit concurrency and failure behavior.
- `WorkflowScheduler` wraps APScheduler with a SQLite job store persisted at `~/.local/share/gofer/schedules.db`.
- Tests use `FakeSubscription` from `tests/conftest.py` to avoid requiring real `claude` or `codex` CLIs.

## Rattish workflow format

```yaml
Rattish: 1

Workflow:
  name: My Workflow

Node step-1:
  type: bash-command
  command: echo hello
  to: step-2

Node step-2:
  type: agent
  provider: codex
  prompt: Analyze the command output.
  needs: step-1
```

## Development Rules

- Prefer the existing module boundaries and patterns before adding new abstractions.
- Keep CLI behavior covered by tests when changing command behavior.
- Keep workflow parsing and execution behavior covered by unit or regression tests when changing operation, graph, executor, or scheduler logic.
- Do not require real LLM provider CLIs in tests; use `FakeSubscription` or another test double.
- Frontend controlled inputs: do not transform or normalize a user-editable value
  on every keystroke when the displayed value is derived from the stored value,
  such as percentages, parsed numbers, paths, JSON, units, or enum-like labels.
  Keep a local draft string while the field is focused and commit on blur or
  Enter, otherwise clearing/backspacing can snap the field back to the previous
  normalized value. Add regression coverage for focus -> clear -> type -> blur
  flows.
- After code changes, run `ruff check src tests --fix`.
- After code changes, run `mypy src tests`.
- Run targeted pytest tests for the changed area; run the full suite when touching shared workflow execution, scheduling, or CLI behavior.
- To run any npm commands make sure you first use nvm to select the correct version of npm for the project.
- Real-Electron tests must not open windows on the developer's desktop. Each
  `*.browser.cjs` entry script calls `runInBackgroundDisplay()` from
  `frontend/electron/tests/background-display.cjs` before anything else, and
  scripts that spawn Electron go through its `backgroundLaunch()`. On Linux this
  runs Electron on a hidden Xvfb display (install `xvfb-run`) and a private,
  empty D-Bus session, so tests can't add tray icons, notifications or wallet
  prompts to the desktop. `electron/tests/background-display.test.cjs` fails if
  an entry script or launcher skips the helper. Set `RATICODE_TEST_VISIBLE=1`
  only when the user asks to watch a run.
- Tests must not leave files behind. `/tmp` is often RAM-backed, and a single
  browser profile or app build can take 100 MB or more. A `*.test.cjs` or browser
  test that creates temporary folders removes each one or requires
  `frontend/electron/tests/test-tmp.cjs` first, which moves the whole run to a
  disk-backed folder and removes it when the run ends, even after a failure.
  Loading `background-display.cjs` includes it. `electron/tests/test-tmp.test.cjs`
  fails if a test skips this. Agents put their own scratch files, logs and build
  output in a private folder on disk, not `/tmp`, and delete it when the task ends.
  Keep only what a report cites as evidence.
- A test that starts a Vite dev server passes `cacheDir: testViteCacheDir()`
  from `frontend/electron/tests/test-vite-cache.cjs`. Worktrees link
  `node_modules` to the main checkout, so the default cache is the one the
  developer's running dev app uses.
- A plain `xvfb-run` does not hide Electron on a Wayland desktop; launch
  through the scripts above instead of wrapping Electron yourself.
- Agents must not run `npm run electron:dev` to check a change: it opens a
  visible window and shares the developer's profile and port 5173. Use the
  real-Electron test scripts, or a throwaway harness started through
  `backgroundLaunch()` with its own profile. `dev-runner.cjs` refuses a profile
  that a running window holds, so the running window isn't brought forward.
- Standalone browsers for UI checks (Chromium, Playwright) must run headless.
  Python tests run with no display and no session bus (`tests/conftest.py`).
