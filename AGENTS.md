# Agent Instructions

This file provides guidance for AI coding agents, including Codex, Claude Code, and other agent providers, when working in this repository.

## Project Summary

Raticode is a Python CLI (the `Raticode` command) and desktop workflow studio. Workflows use the Rattish language in `workflow.rattish`; the compiler validates them and emits versioned JSON IR. Execution supports explicit routes, joins, branches, and cycles.

## Commands

```bash
# Install for development
pip install -e ".[dev]"

# Run all tests on 8 cores (pytest-xdist, in the dev extras)
python -m pytest -n 8 --dist worksteal

# Run all tests in one process (also works without pytest-xdist)
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

# Frontend tests, from frontend/ after `nvm use`
npm test              # unit tests in one process, slow browser and git files beside it
npm run test:serial   # everything in one process
```

### Before landing on `dev`

- Before fast-forwarding `dev`, run `python3 scripts/check-dev.py` from the
  repository root or worktree. It runs `ruff check src tests`, `mypy src tests`,
  the repository-wide test files in its `REPO_WIDE_TESTS` list (CLI parity, the
  scripting contract, the website address and mobile download, the phone
  protocol, no credential on the command line, `tests/unit/test_check_dev.py`),
  the tests of changed modules, `scripts/check-website-docs.py`, `scripts/check-commit-messages.py`,
  `scripts/check-dead-code.py`, `mobile/android/scripts/check-contract.py` and
  `scripts/check-protocol-compat.py`, plus `npm run lint` in `frontend/` when
  `git diff --name-only dev...HEAD` lists a file under `frontend/`, and prints one
  PASS or FAIL line for each. SKIP marks the protocol compat check in a clone
  without `v*` tags and the frontend lint when no frontend file changed. The
  frontend lint fails when `frontend/node_modules` is missing: run `npm ci` in
  `frontend/` first, or link the main checkout's `node_modules` into a worktree.
  Don't land while any line says FAIL. It takes about a minute. The phone's
  Gradle interop tests in `contract-check.yml` stay CI-only. It doesn't run
  `npm audit`, which needs the network.
- The "tests of changed modules" check runs, with the same pytest flags and
  workers, every test file under `tests/` that imports a `src/gofer/**/*.py`
  module `git diff --name-only dev...HEAD` lists (`import gofer.a.b`,
  `from gofer.a.b import ...` or `from gofer.a import b`), less the files in
  `REPO_WIDE_TESTS`. It prints SKIP when no such module changed or no test imports
  one, and when more than 150 files match; then run the full suite (below).
- `scripts/check-dead-code.py` fails when a function, class, method or UPPER_CASE
  module constant in `src/gofer`, an export in `frontend/src` or `frontend/electron`, or a frontend
  module has no callers anywhere in the repository (tests and browser tests
  count). Delete the unused code. Only when it's kept on purpose, add a line to
  `scripts/dead-code-allowlist.txt` with the reason. A `path:name`, route or CSS
  line there that no longer matches anything fails the check too
  (`(allowlist line matches nothing)`); delete it with the code it excused.
- It also fails on production code only tests call: a `src/gofer` definition or a
  frontend export whose name appears outside its own definition only in `tests/`,
  `*.test.*`, browser tests or `frontend/electron/tests` (printed with
  `(only tests use it)`). Point the tests at the production path, make the app
  call the helper it copies inline, or delete it with its tests. A helper kept
  for tests needs an allowlist reason that starts with `test seam:`.
- It also fails on an export only its own file uses: an `export` in `frontend/src`
  or `frontend/electron` (test files left out) whose name appears as a word in no
  other file the check searches (printed with `(export only its own file uses)`).
  Drop the `export` keyword.
- It also fails on a dependency nothing uses: a package in `pyproject.toml`'s
  `[project].dependencies` or its `devices` or `xlsx` extra that no file under
  `src/gofer`, `scripts` or `packaging` imports and `raticode.spec` doesn't name
  (imports in `tests/` don't count; move such a package to the dev extra), or a
  package in `frontend/package.json` that no frontend file names as a module and
  no `package.json` script runs. Remove it and relock (`uv lock`, `npm install`),
  and drop its entry from `packaging/licenses/reviewed.json`. One kept on purpose
  gets an allowlist line such as `pyproject.toml:name  # reason`.
- It also reads the phone's Kotlin: a `fun`, `class` or `object` in
  `mobile/android/app/src/main` whose name no other Kotlin, XML, Gradle or R8
  rules file under `mobile/android` mentions fails the check, and one only the
  phone's test source sets (`src/test`, `src/androidTest`, `src/testShared`)
  mention prints with `(only tests use it)`. An `override` or `operator` fun, an
  `@Preview` function and a name `AndroidManifest.xml` uses are skipped. The same
  allowlist and `test seam:` rule apply.
- It also reads the Electron bridge: a key of an object a
  `frontend/electron/*preload*.cjs` file passes to `contextBridge.exposeInMainWorld`
  fails the check when no non-test file under `frontend/src` and no `.html` page
  under `frontend/electron` names it (a test listing the bridge's keys doesn't
  count), and so does a channel in the `ipc-handlers.cjs` table no preload file
  names. Remove the key, its IPC row and the handler wiring together.
- It also reads the stylesheets: a class selector in a `.css` file under
  `frontend/src` or `frontend/electron` fails the check (printed with
  `(CSS class nothing uses)`) when no non-test, non-CSS file there or
  `frontend/index.html` names it as a word and none builds it from a template
  (`st-${state}` covers `.st-running`). Delete the rule, or the dead class's part
  of a shared selector. A class a library adds, such as `.xterm-*`, gets an
  allowlist line; CSS lines may use `*` patterns.
- It also fails on scripts nothing runs (printed with `(script nothing runs)`): a
  file directly under `scripts/` that no other file in the repository, `docs/`,
  `benchmarks/` or the root Markdown names by file name, and a `*.browser.cjs` or
  `run-*.cjs` file under `frontend/browser-tests` or `frontend/electron/tests`
  that no non-test file names, either by file name or by its short name as a
  quoted string (`"usage"` in `run-studio-browser.cjs`'s list covers
  `usage.browser.cjs`), and no other browser test requires. `*.test.cjs` files
  are skipped, since `npm test` runs them all. Give a kept browser test an npm
  script in `frontend/package.json`, list a kept probe in `benchmarks/README.md`
  with its run command, or delete the file.
- Commit messages carry no `Co-Authored-By` line for an AI tool and no
  "Generated with ..." line, even when your tool adds one by default.
  `scripts/check-commit-messages.py` fails on them; amend or reword those
  commits before landing.
- It uses the checkout's `.venv`, or the main checkout's `.venv` from a git
  worktree. Pass `--python` to choose another interpreter.
- A test file that reads `git ls-files` (or `git grep`) or `docs/cli-parity.json`
  checks the whole repository, so add it to `REPO_WIDE_TESTS`.
  `tests/unit/test_check_dev.py` fails until you do.
- It doesn't replace the full suite. Run the full suite as well for changes to
  shared workflow execution, scheduling or CLI behavior.

### Full test runs

- Parallel runs must give the same results as `python -m pytest`. Tests get their
  own HOME, data folders and temporary folders from `tests/conftest.py`; use
  `tmp_path`, port 0 and the isolated data folder rather than shared paths,
  fixed ports or `tmp_path.parent`. Collection must be the same in every worker,
  so parametrized IDs can't contain random values. Mark a test
  `@pytest.mark.serial` only when it can't be isolated: it then runs alone under
  `-n` (`tests/helpers/serial_tests.py`). Don't drop it.
- Rich folds output at 80 columns and xdist makes temporary paths longer, so
  compare paths in CLI output after joining the lines.
- Tests must pass in any temp folder, however long its path or whatever its name
  holds (Rem's worker scratch folders are long and contain `x` and `-scratch`):
  don't count characters in text that includes a path, cap argument sizes below
  the OS limit, or find folders by a word in their path.
- `npm test` (`frontend/electron/tests/run-tests.cjs`) runs the files in its
  `SEPARATE` list in their own processes, 2 at a time beside the unit tests.
  Add a new slow real-browser or git file there. `RATICODE_TEST_JOBS` changes
  the count.
- Memory: the backend suite on 8 workers peaked at about 2.3 GB (process RSS),
  under a 6 GB cap. The frontend run without browsers peaked at about 1.7 GB;
  nobody has measured it with real browsers yet. Inside a capped scope
  `npm test` ends with `ℹ memory_peak_mib`.
- Unattended full runs, such as the nightly, start with
  `python3 scripts/check-dev.py` (see "Before landing on `dev`" below) and report
  its PASS/FAIL lines, so the list of repository-wide checks lives in one place.
  Then they run both suites capped. From the repository root, with the project's
  `.venv` synced with the `dev` extra:

  ```bash
  PYTHONPATH=src systemd-run --user --scope --quiet -p MemoryMax=6G -p MemorySwapMax=0 --setenv=PATH=$PATH .venv/bin/python -m pytest -n 8 --dist worksteal -q -p no:cacheprovider --no-header -rf tests
  ```

  and from `frontend/`:

  ```bash
  env -u FORCE_COLOR systemd-run --user --scope --quiet -p MemoryMax=4G -p MemorySwapMax=0 npm test
  ```

  Keep `RATICODE_TEST_JOBS` at its default of 2 until a run with real browsers
  records `memory_peak_mib`. Raise it to 3 only if that peak is well under 4096.
  When `.venv` has no pytest-xdist, drop `-n 8 --dist worksteal`; the suite
  then runs in one process.

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
- Before fast-forwarding `dev`, run `python3 scripts/check-website-docs.py` (`scripts/check-dev.py` runs it for you). It fails when a branch with a `feat:` commit changes app code under `frontend/`, `src/gofer/` or `mobile/android/app/src/main/` without touching `website/`. If no user-facing documentation changes, add a `Docs-not-needed: <reason>` line to the commit message; the same line works in a pull request description. The `Website docs` GitHub workflow runs this check on pull requests.
- After editing the website, run from `website/` (with `nvm use`): `npm ci`, then `npm run check` (type check, build, and internal link and anchor check). `npm run check:downloads` confirms the download links match the newest published release. When preparing a release, `npm run set-release -- X.Y.Z` moves the site to that version. The `Website docs` workflow deploys the site to GitHub Pages from `main` only after both checks pass; see `docs/releasing.md`. Check full Rattish examples with `Raticode rattish check`.

## Architecture

Layer structure:

- `src/gofer/cli/` - Typer CLI; routes commands to the workflow, rattish, provider, rem and other command modules. Every CLI call imports every command module, so import the Rattish runtime, trigger scheduler, agent core and other heavy modules inside the command functions that use them. Typer reads each command's option types when the CLI starts, so those types must stay cheap to import. `tests/unit/test_cli_startup_imports.py` fails if SQLAlchemy, APScheduler or NetworkX load at startup.
- `src/gofer/core/` - Shared domain logic: agents, approvals, providers, usage, resources and structured output.
- `src/gofer/rattish/` - The Rattish language and its runtime: parser, compiler, preflight, runs, triggers and `toml_convert.py`, which converts old TOML workflows. Raticode no longer loads or runs TOML workflows.
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
- Rattish triggers (`rattish/triggers.py`, `rattish/trigger_service.py`) run cron, file and webhook triggers on each computer; there is no other workflow scheduler.
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

- Shared words: read [GLOSSARY.md](GLOSSARY.md) before naming anything users see or anything new in code.
- Prefer the existing module boundaries and patterns before adding new abstractions.
- Keep CLI behavior covered by tests when changing command behavior.
- Keep workflow parsing and execution behavior covered by unit or regression tests when changing the Rattish compiler, runtime or trigger logic.
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
- Run targeted pytest tests for the changed area; run the full suite when touching shared workflow execution, scheduling, or CLI behavior. Before landing on `dev`, also run `python3 scripts/check-dev.py`.
- To run any npm commands make sure you first use nvm to select the correct version of npm for the project.
- Real-Electron tests must not open windows on the developer's desktop. Each
  `*.browser.cjs` entry script calls `runInBackgroundDisplay()` from
  `frontend/electron/tests/background-display.cjs` before anything else, and
  scripts that spawn Electron go through its `backgroundLaunch()`. On Linux this
  runs Electron on a hidden Xvfb display (install `xvfb-run`) and a private,
  empty D-Bus session, so tests can't add tray icons, notifications or wallet
  prompts to the desktop. A launch whose arguments include
  `--ozone-platform=headless` keeps the private environment but skips Xvfb, for
  tests that need no display and must run where Xvfb can't start, such as an
  agent's sandbox. `electron/tests/background-display.test.cjs` fails if
  an entry script or launcher skips the helper. Set `RATICODE_TEST_VISIBLE=1`
  only when the user asks to watch a run.
- Tests must not leave files behind. `/tmp` is often RAM-backed, and a single
  browser profile or app build can take 100 MB or more. A `*.test.cjs` or browser
  test that creates temporary folders removes each one or requires
  `frontend/electron/tests/test-tmp.cjs` first, which moves the whole run to a
  disk-backed folder and removes it when the run ends, even after a failure.
  Loading `background-display.cjs` includes it. `electron/tests/test-tmp.test.cjs`
  fails if a test skips this. For a folder one test uses, call
  `tempFolder(prefix, t)` from `frontend/electron/tests/temp-folder.cjs`, which
  removes it when that test ends (without `t`, when the calling test ends), and
  pass any Firefox engine or store that writes into it to
  `stopBeforeRemoving(folder, engine)` so it stops before the folder goes.
  `npm test` prints `ℹ temp_left_behind N` with its totals: entries the tests
  left in the run folder, with their names, so a new leak shows up. Leftovers
  from runs before the run folder existed are not removed automatically. To clear
  them, older than an hour and owned by you, run
  `find /tmp /tmp/claude-$(id -u) -maxdepth 1 -user $(id -u) -mmin +60 \( -name 'browser-sessions-??????' -o -name 'browser-pdf-??????' -o -name 'raticode-browser-window-??????' -o -name 'firefox-*-??????' \) -exec rm -rf -- {} +`
  (leave off `-exec rm -rf -- {} +` to list them first). Agents put their own scratch files, logs and build
  output in a private folder on disk, not `/tmp`, and delete it when the task ends.
  Keep only what a report cites as evidence.
- That private folder is outside the repository, for example
  `~/.cache/<task>-tmp`, never in the main checkout or a worktree (no
  `TMPDIR=$PWD/.scratch` or `$PWD/../../.scratch`). The main checkout is on the
  branch the user's app runs from and must stay clean. `tests/conftest.py`
  refuses to run with a temp folder inside a checkout.
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
