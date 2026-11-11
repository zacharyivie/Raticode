# Mobile security fixes

This milestone repairs the local prototype. It does not enable pairing, a release
network permission, live receipts or desktop execution. Independent cryptographic
review and physical-device/public-network validation remain open.

## Document imports

`DocumentImport` owns untrusted provider reads. It queries with a CancellationSignal,
opens a descriptor with that signal, enforces the actual 10 MiB limit, and checks
an elapsed-realtime deadline between calls and again before accepting the result.
The UI has a Cancel import button and a 30-second deadline. Cancel and timeout
release the workspace immediately even if a provider ignores cancellation.
Provider reads run on a separate worker that cannot access the vault. Only an
accepted result enters the serialized vault save. Cancelled or expired results
are discarded and their byte arrays cleared, so late results cannot recreate data
after reset. The actual workspace quota is checked again at commit. Failed saves
clean unreferenced encrypted blobs; startup cleanup remains the process-death fallback.

There is at most one provider worker and one cancellation callback thread per
process. A provider that never returns occupies its slot until process exit.
Further imports fail promptly while normal workspace use and reset remain available.
This bound also survives activity recreation. Cancellation does not pretend to
terminate arbitrary provider code. Provider MIME lookup is omitted; attachments
use application/octet-stream and sanitized display names.

## Draft and Back behavior

Draft saves serialize with vault mutations but keep the composer editable. Saving
an older snapshot does not replace newer in-memory text. Subsequent debounce or
background flush saves the newer draft. Chat handles Back by hiding a visible
keyboard before navigating to Threads. Drafts remain intact in both cases.
The explicit Back to threads button still navigates directly.

## Dependency scope

The September 19, 2026 report matched nine advisories in six build packages.
The current before graph reproduced those versions. Kotlin's Compose plugin is
updated from 2.3.20 to 2.4.20, which also selects the built-in Kotlin Gradle plugin.
AGP remains 9.0.1 and the checksum-pinned Gradle wrapper remains 9.1.0. No separate
Android Kotlin plugin is added. Root buildscript resolution rules replace only
the affected build-tool transitives. Bouncy Castle modules are aligned together.
These rules do not add any package to the app runtime.

| Build package | Before | Resolved after | Advisory |
| --- | --- | --- | --- |
| Kotlin Gradle plugin | 2.3.20 | 2.4.20 | GHSA-r937-wjx7-w2jp |
| bcprov-jdk18on | 1.79 | 1.85 | GHSA-574f-3g2m-x479, GHSA-c3fc-8qff-9hwx, GHSA-9pwp-9qqc-pr26, GHSA-qp49-qgx5-5m26 |
| bcpkix-jdk18on | 1.79 | 1.85 | GHSA-wg6q-6289-32hp |
| jose4j | 0.9.5 | 0.9.6 | GHSA-3677-xxcr-wjqv |
| commons-lang3 | 3.16.0 | 3.18.0 | GHSA-j288-q9x7-2f5v |
| jdom2 | 2.0.6 | 2.0.6.1 | GHSA-2363-cqg2-863c |

Current GitHub advisory records and their upstream fix references are captured
under `artifacts/security/`. `docs/evidence/mobile-security.json` binds their
hashes and source URLs. An OSV batch query covers separate resolved build and
release-runtime inventories plus the entire local contract Python environment.
The initial Python inventory also found pip 26.1.1 advisories. The isolated
contract environment was upgraded to pip 26.2.1 and queried again. pip is setup
tooling, not an APK dependency. A database match is a version finding, not proof
of exploitability; no match is not proof of absence of vulnerabilities.

Regenerate inventories with:

```sh
./gradlew -I scripts/dependency-inventory.init.gradle dependencyInventory
.venv-contract/bin/pip list --format=json
```

The Gradle inventory task writes `artifacts/security/resolved-inventory.json`.
It inventories the root plugin classpath and releaseRuntimeClasspath, excluding
Gradle's embedded libraries, SDK binaries and test-only configurations. The Python
inventory covers the schema environment, not the desktop peer environment. No
full software-supply-chain certification is claimed. Dependency locks, complete
verification metadata and periodic scans remain future maintenance work.

## Verification

Developer commands, test counts and raw evidence hashes are recorded in
`docs/evidence/mobile-security.json`. Tests use the disposable
Swarm_Mobile_Protocol_API36 emulator. They cover stalled query/read, deadline,
late completion after cancel/reset, repeated rejected imports, oversize bytes,
cancel UI, encrypted import/export, Back/IME, and the existing app/protocol suite.
Cross-language tests use the accepted desktop e5f46649 source with public test
identities only. Earlier failures are retained separately; this document does not
claim that historical failed suites passed.

## Rotation and lifecycle verification repair

The retained full device run at `f6694aca` stopped in Compose's
`waitForNextChoreographerFrame` immediately after requesting rotation. Its outer
10-second semantics wait did not bound the inner frame wait. The test now waits
for the replacement activity to be resumed, laid out and focused in the requested
orientation, then awaits a real frame with a five-second latch deadline. Only
then does it query Compose semantics. The return rotation uses the same checks.

The [AndroidX implementation](https://android.googlesource.com/platform/frameworks/support/+/edd8bafb328feda384a1048242a66d77f12b6699/compose/ui/ui-test/src/androidMain/kotlin/androidx/compose/ui/test/ComposeUiTest.android.kt)
posts to a selected root and waits for its frame callback. Detachment before that
callback is a plausible race consistent with the retained stack, not a directly
observed root identity in that trace. The passing repaired test establishes the
behavior tested; it does not prove every framework hang has the same cause.

Native Back delivery and the retry reply are asynchronous, so assertions await
their expected selected tab or reply within ten seconds. The keyboard check also
waits for IME animation completion, because end-state insets are dispatched before
the animation completes, as documented by [AndroidX](https://developer.android.com/reference/androidx/core/view/WindowInsetsAnimationCompat.Callback). A test-only callback observes
animation start/end without consuming insets or stopping subtree dispatch. Both Back presses,
keyboard visibility, Chat retention, draft text, navigation and viewport bounds
remain asserted. Test teardown flushes outstanding drafts while the activity is
alive before the next test resets the disposable vault. The view model tracks the
last successfully persisted workspace and skips unchanged draft writes, including
redundant lifecycle flushes. Failed writes never update that marker, and newer
in-memory drafts remain eligible for persistence.

`docs/evidence/mobile-security-rotation-repair.json` records current results,
retained failures and recovery hashes. The earlier manifests remain historical.

## ViewModel destruction and vault ownership

The previous busy flag serialized writes within one ViewModel but did not cover a synchronous IO call that outlived coroutine cancellation. All LocalVault instances now share a storage lock. Each ViewModel has a revocable owner token, checked under that lock before any transaction. A new owner invalidates older tokens; onCleared closes its token without blocking the UI. Already-started IO completes before replacement load/reset, and queued stale writes or cleanup are rejected. The lock also protects direct vault callers and AtomicFile recovery.

VaultOwnershipTest clears an actual ViewModelStore while a draft write holds an open AtomicFile, creates a replacement model, resets through that replacement, and reloads the vault before cleanup. A second ViewModelStore destruction test delays the old save before lock acquisition until the replacement has completed reset, then proves that save is rejected. A separate controlled queued operation proves closed/replaced tokens cannot save or clean up after reset. Lifecycle coverage dispatches ON_STOP through the composable's real lifecycle observer while reset is active and exercises pending draft flushes during thread selection/deletion. Assertions inspect durable state before fixture teardown. Activity recreation alone is not evidence of ViewModel destruction. Physical-device and process-kill testing remain separate gaps.

## Export ownership repair

Export now authenticates and bounds a snapshot while holding vault ownership, then opens, writes and closes the selected document provider outside vault locks. Bytes are wiped even when cancellation lands between dispatchers. An already authorized export can finish after local reset; reset cannot retract a copy already requested at an external provider. API 36 blocked-open and blocked-write regressions verify replacement load, reset and reload finish before the provider is released.
