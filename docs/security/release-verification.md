# Dependency and release verification

The security refresh upgrades Electron to 41.10.7, React Router to 7.18.3,
Vite to 7.3.6 with its React plugin 5.2.0, pydantic-settings to 2.15.0, and
setuptools to 84.0.0. The lockfiles also contain patched transitive packages.
React 18 and the studio's existing routes and appearance are unchanged.

The full npm audit includes Electron even though npm classifies it as a development
dependency. `scripts/audit-dependencies.py` audits every registry package version
in `uv.lock`, including other platforms and optional/build dependencies. It does
not resolve away platform-specific packages. The release publishes both audit
JSON documents and lock inventories with its artifacts.

Release validation requires Ruff, mypy, the complete Python suite, frontend lint,
frontend unit/browser tests, and both advisory checks before packaging. Every
advisory severity fails the gate. There are currently no exceptions. A future
exception must name one advisory and its affected package/version, explain the
unreachable code path, identify an owner, and expire on a date. Do not add a
blanket severity downgrade or `continue-on-error` to the audit steps.

Validation and every platform build check out the main event's immutable
`github.sha`. Publication checks the tag against the attested candidate commit
and rechecks the remote tag before making the draft public. Desktop packages exclude the
Electron test directory and test modules.

Actions use commit hashes. Dependabot proposes weekly action and dependency
updates for review. Python installation and backend builds use `--locked` with
uv 0.11.21. A manifest change without its updated lock therefore fails the build.

## Packaging deprecations

`@electron/asar` 4.3.0 removes the old glob/inflight path from active ASAR packaging;
`@electron/get` 5.1.0 removes global-agent/boolean from the builder download path.
These explicit overrides require the pinned Node 22.12.0 toolchain. Linux unpacked
packaging and native node-pty rebuilds exercise the actual consumer APIs.

The frontend `.npmrc` omits peer-only packages. This excludes the unused Squirrel
packager and its electron-winstaller/temp/rimraf/glob/inflight chain from clean
installs. NSIS remains in app-builder-lib and DMG remains a direct builder
dependency. The omitted peer packages stay resolved in the lockfile; the release
audit uses `npm audit --include=peer` to inspect them as well. Do not use
`--legacy-peer-deps`, which would discard peer constraint checking.

The updater's deprecated standalone lodash.isequal package is replaced by a local
adapter at `frontend/vendor/lodash.isequal`. It delegates to the maintained
`lodash/isEqual` implementation, preserving Lodash semantics for signed zero,
boxed numbers, sparse arrays and metadata objects. The adapter's npm override
references the direct file dependency; `install-links=true` copies it into the
installed tree and desktop archive. No third-party sources are rewritten after
installation. Remove this adapter when a compatible upstream updater stops
requiring the standalone package.

Clean `npm ci` completes without deprecation warnings. The updater compatibility
tests exercise its real cached-download helper, accepting equal metadata and
rejecting changed versions, changed hashes and missing files. Linux unpacked
packaging verifies the adapter, Lodash and native node-pty load from the generated
ASAR. The release matrix repeats those checks on each native platform.

## Legacy recent-folder migration

On the first launch after this update, isolated preload reads the existing
`gofer.recentProjects` localStorage list before page scripts run. Main imports
absolute paths into `trusted-projects.json` and records
`legacyRecentProjectsMigrated: true` in the same atomic replacement. This
one-time acceptance of legacy renderer history is an explicit compatibility
decision. Later changes to that list cannot authorize additional folders.

The launch capability stays inside preload. Main checks the sender and frame,
consumes the capability once, and never exposes migration on `goferDesktop`.
Old array-shaped trusted registries remain readable. Existing native selections
remain in the new record, and offline folders can renew when they become
available without restarting. Normal renewal still registers the grant with
the backend and reports registration failures.

Registry writes sync their temporary file before replacement and sync the parent
directory on POSIX. An unreadable or corrupt registry disables migration and
restores no authority from that file; startup continues and logs the error.
Unavailable localStorage or failed publication can retry on the next launch.
Malformed or empty history completes migration with no additional grants.

The unit suite covers persistence, replay, foreign senders, publication failure,
offline renewal and preload ordering. The real Electron regression is:

```sh
source /home/doonk/.nvm/nvm.sh
nvm use
env -u ELECTRON_RUN_AS_NODE xvfb-run -a frontend/node_modules/.bin/electron \
  frontend/electron/tests/legacy-project-migration.browser.cjs
```

The Linux test uses an isolated session and temporary folders. Its BrowserWindow
keeps context isolation and renderer sandboxing enabled; like the existing
Electron policy fixture, the test disables the operating-system sandbox to run
under the test host. Windows/macOS verification remains separate.

## Release signing setup

Main preparation defaults to unsigned releases and needs no signing credentials.
Set the repository variable `RELEASE_SIGNING=true` to opt into signed candidates,
which require five secrets and three variables in the `release-signing` environment. See [the release guide](../releasing.md) for every setting, its purpose,
how to obtain it, and the Windows hardware/cloud signing limitation. Both the early
credential check and native build jobs select that environment only for signed
preparation. Unsigned jobs use `release-unsigned` with no secrets required. Restrict
the signing environment to main.

Main builds the candidate and verifies it before uploading a draft. Its manifest
records `signed: true` or `signed: false`, and release notes disclose the mode. Tag pushes
only publish a matching successful candidate. Signing values are passed to scoped
signing/packaging steps, after dependency installation. Source validation on pull
requests needs no signing credentials.

When signing is enabled, the Windows backend is timestamped and signed before it
enters the installer.
Electron Builder signs the application and NSIS installer with signing required.
Verification rejects an invalid or untimestamped installer or standalone CLI.

For signed macOS releases, a temporary keychain supplies the PyInstaller and Electron signing
identity. Electron Builder signs and notarizes the app. Verification checks its
signature, stapled ticket, and Gatekeeper assessment. The outer DMG and standalone
CLI receive their own notarization submissions and Gatekeeper checks. The DMG is
not modified after its update hashes/blockmaps are generated; its enclosed app
has a stapled ticket and the outer DMG has an online ticket. Accepted notarization
receipts are release artifacts. The temporary certificate and keychain are removed.

Unsigned preparation skips signature/notarization checks and omits their receipts.
macOS uses ad-hoc signing for local execution and manual update downloads. This
does not establish publisher identity or notarization. Tests, dependency audits,
checksums and provenance remain mandatory in either mode.

The staging job attests every candidate artifact, including Linux and standalone
CLI binaries, checksum files and dependency evidence, using GitHub's OIDC build
provenance, plus a manifest binding the source SHA, run/attempt, artifact IDs and
file hashes. Publication verifies that manifest, the successful main run and every
remote asset before changing draft visibility. Verification of a downloaded artifact is:

```sh
gh attestation verify ./ARTIFACT --repo zacharyivie/gofer-flow
```

Repository administrators must restrict `v*` tag creation and protect release
workflow/lockfile changes with review. Those GitHub settings are external to this checkout and require authenticated
repository administration to change. Platform signatures and notarization still need
the first credentialed Windows/macOS CI run; Linux checks do not prove them.

## Second Brain indexing

Queries share an index coordinator for each canonical knowledge root. Native
watchdog notifications coalesce repeated changes by note path, and a successful
`save_note` invalidates that path immediately. Unchanged queries use SQLite FTS
without walking or statting the knowledge tree. External changes appear after
the operating system delivers its event; a recovery scan every 60 seconds catches
missed events. Where native watches cannot start, reconciliation falls back to a
one-second interval. Watchers are bounded to eight cached roots and stopped/joined
on eviction and process exit.

A coordinator worker reconciles native events and periodic recovery between
queries. Each `reconcile()` call shares a 16 MiB read budget and a 100 ms
cooperative deadline across directory enumeration and note ingestion. It retains
its iterator for the next pass, releases its SQLite transaction and coordinator
lock, and resumes after yielding. A single filesystem or SQLite operation can
finish after the deadline. Existing 2 MiB note, 10,000-note, 100,000-directory-entry
and 10-second enumeration-work limits remain in force.

Search waits for pending passes to finish before reading results. This avoids
presenting partial or stale reconciliation output as complete, and preserves the
existing API. A cold search still waits for its initial index. Background recovery
usually begins before the next query, but a query that races an ongoing recovery
waits for completion. This bounds individual work intervals, not the total cold
query latency. Splitting commits has measurable overhead on a cold rebuild.

Tests cover external additions, edits, renames, deletions, concurrent searches,
complete results between passes, background recovery without a query, byte/time
budgets, database recreation, failures/retries and worker shutdown. Unchanged
queries perform no additional tree walk or note reads.

`save_note` now uses exclusive atomic publication through pinned directories.
A concurrent creator or an existing plain file, hard link, or dangling symlink
causes failure while preserving the existing directory entry. Partially written
notes are never published, and swapped parent links cannot redirect a save.

Watchdog's native recursive API cannot exclude directories during watch setup.
Before enabling it, a separate preflight includes ignored subtrees and stops at
512 directories, 10,000 entries, or 100 ms between filesystem operations. Larger
or slower native watch scopes use the filtered one-second fallback scan. Ordinary
index scans still exclude hidden directories, `node_modules`, and `__pycache__`.
Small ignored subtrees may have native watches, but their events are discarded.
A full reconciliation rechecks the native scope and stops its observer if the
scope has grown beyond the budget. The preflight bounds a filesystem snapshot;
OS watch registration itself cannot be interrupted if the tree changes during
registration. Kernel watch exhaustion also falls back to filtered scans.

## Incremental chat persistence and previews

Chat snapshot watchers start before the baseline capture. Warm previews consume
changed file/subtree paths and update only affected diffs. A 30-second recovery
scan and an authoritative final capture catch missed events and preserve undo.
Native-watch setup has directory, entry and elapsed-time caps. If watching fails
or the tree exceeds those caps, previews retain the full-scan fallback. Snapshot
file states own their backing storage directly so successive incremental captures
do not retain obsolete chains of spool directories.

The renderer caches each immutable message object's serialized JSON and joins
those fragments into the existing single-key localStorage array. Updating one
message no longer serializes every unchanged message. The final full-sized
string allocation and localStorage write remain. Keeping that atomic single-key
replacement preserves existing quota capacity, multi-message edits, failed-write
recovery and older-version compatibility. A split-record design would need
transient staging space and could reject edits that previously fit the quota.

No storage migration, debounce or asynchronous loss window was added. Tests cover
navigation/reload, edits, truncation, deletion, unchanged-message reuse and quota
failures, including single and multi-message replacements. The browser fixture
measures actual Chromium localStorage costs. Byte-volume reductions reported for
the earlier experimental split-record implementation do not describe the final
compatibility-preserving code.

## Native verification and measurement

After building the native unpacked desktop package, run from `frontend/`:

```sh
source /home/doonk/.nvm/nvm.sh
nvm use
npm run test:platform
```

The runner checks node-pty and updater loading from the packaged ASAR, then runs
browser permission/partition, legacy-folder migration and conversation-persistence
fixtures. Linux needs Xvfb. These fixtures disable the test process OS sandbox;
renderer sandboxing and context isolation stay enabled. Production flags are not
changed. `.github/workflows/release-build.yml` runs the fixtures after packaging on
Linux, Windows and macOS. Native filesystem tests include Windows junctions and
adversarial ancestor replacement during reads, enumeration and writes.

Reproducible component benchmarks write JSON under the ignored `audit-evidence/`
folder. Run from the repository root with its development Python environment and
Node version selected through nvm:

```sh
mkdir -p audit-evidence
.venv/bin/python scripts/benchmark-chat-previews.py > audit-evidence/chat-preview-benchmark.json
.venv/bin/python scripts/benchmark-second-brain.py --output audit-evidence/second-brain-benchmark.json
node scripts/benchmark-chat-history.mjs
node scripts/benchmark-idle.mjs
```

Generated fixtures and actual checkout Git/file-tree work are identified in each
result. Python/Node process RSS and CPU are not full-desktop peak RSS or aggregate
CPU. Slow-fsync injection measures event-loop isolation, not physical disk speed.
Native Windows/macOS execution, credentialed signatures/notarization and published
artifact provenance still require their runners and repository credentials.
The public repository API reported an empty ruleset list on 2026-09-10; requests
for branch protection and signing-secret metadata required authentication. The
existing v0.2.5 release predates these hardening commits and cannot verify them.
