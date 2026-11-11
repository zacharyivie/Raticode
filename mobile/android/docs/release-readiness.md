# Release candidate preparation and acceptance

This is a reproducible release checklist, not a public-release approval. Source checks, emulator tests and an unsigned minified build do not replace independent security review or acceptance on physical devices. Do not publish until the open gates below have recorded results against the exact artifacts being distributed.

## Recommended first release scope

Start with a private experimental Android 10+ and Linux desktop pilot. Android 8 and 9 only show a notice; the local demo is no longer in the APK. Start with one home desktop, explicitly shared existing threads, text replies, scoped files, reconnect-based LAN preference and user-started foreground sync. English only. Keep fleet execution experimental until the physical two-host authorization test passes. Treat macOS and Windows support as unaccepted until their packaged binaries, credential stores and scoped file operations pass the same matrix.

The phone connects over Wi-Fi to the desktop's local address, or through the user's own Tailscale network when they turn that on; there is no internet relay or push service. Without Tailscale, both devices must share a network that allows direct connections. The Tailscale route has not been accepted on physical devices. The desktop backend must remain running. Android may stop background sync because of a timeout, battery policy, process death or force-stop. Do not promise continuous background delivery.

Multiple home desktops and conversation export are outside this first release. Dictation, model and permission choices, remote approvals, a swarm console and thread search have since been built; they are tested on emulators but not yet accepted on physical phones. The user-facing feature list is on the website at `website/docs/mobile/`.

## Build a candidate without publishing

GitHub release candidates and publication are described in [preparing a release](releasing.md). This section is the local equivalent.

Use JDK 21 and Android SDK platform 37 (GeckoView compiles against it; the app targets 36). Run from the repository root with `JAVA_HOME` and `ANDROID_HOME` set to local tool installations. No host paths belong in version control.

```sh
./scripts/build-release.sh unsigned
```

This runs `assembleRelease lintRelease` with R8 minification and writes `app/build/outputs/apk/release/app-release-unsigned.apk`. The helper then runs `scripts/release-candidate.py check-apk`, the same check a GitHub release candidate gets: version, SDK levels, debuggable flag, backups, cleartext, permissions and exported components against `scripts/release-policy.json`, and that the APK has no valid signature. It prints the APK's SHA-256. It is not signed and cannot be installed as a release app. Keep `app/build/outputs/mapping/release/mapping.txt` privately with the eventual signed artifact for crash diagnosis. An unsigned build is useful build evidence, not proof of runtime behavior after shrinking.

For an owner-managed release key, inject all four variables from a local secret manager or protected CI environment. Never paste passwords into shell commands, this document, issue trackers or logs.

| Environment variable | Meaning |
| --- | --- |
| `RATICODE_RELEASE_STORE_FILE` | Absolute path to an existing readable keystore outside the repository |
| `RATICODE_RELEASE_STORE_PASSWORD` | Keystore password |
| `RATICODE_RELEASE_KEY_ALIAS` | Release key alias |
| `RATICODE_RELEASE_KEY_PASSWORD` | Key password |

```sh
./scripts/build-release.sh signed
```

Signed mode also needs `ANDROID_SIGNING_CERT_SHA256`, the owner's independently recorded certificate fingerprint. The helper runs the same checks and fails unless the APK has exactly one v2 or v3 signature from that certificate and not from Android's debug key. It prints the certificate fingerprint and the APK hash. The default build-tools version is `36.0.0`; override `RATICODE_ANDROID_BUILD_TOOLS` only for another installed version. For an AAB, use `./gradlew bundleRelease` with the same signing environment and separately verify the bundle/distribution process.

All signing variables absent means unsigned release. Any partial or empty configuration fails Gradle configuration. `unsigned` mode rejects even a partially configured signing environment. `signed` mode requires all four variables. Release never falls back to Android's debug key. The helper neither creates keys nor installs or publishes anything.

The version comes from `version.properties`. Before building a candidate, set it with `scripts/version.py` (for example `bump 1.0.0-rc.1`), and tag the tested commit `mobile-v<versionName>` as described in [versions and release tags](versioning.md). Every artifact given to testers needs its own higher version code. Preserve the application ID and signing key for future upgrades. An existing debug installation cannot be upgraded in place with a different release certificate; use a separate test phone/profile or deliberately remove a disposable installation. Never uninstall an installation containing history that needs preserving just to bypass a signing conflict.

## Evidence to record

For each candidate, record the repository commit ID, dirty-tree status, JDK/SDK/build-tools versions, command exit statuses, APK and desktop package SHA-256 hashes, signing certificate fingerprints, dependency audit outputs and the R8 mapping hash. Record actual device models, Android versions, desktop OS versions and credential-store implementation. Use disposable test content and sanitize evidence. Do not archive invitations, routing topics, plaintext messages, keys or credentials.

Fresh build evidence from this preparation run is listed at the end of this document. The final report owns the aggregate test results. Do not carry an earlier green check forward if the artifact or relevant code changed.

## Physical phone and two-host acceptance

Use a physical Android 10+ phone and two separate Linux hosts on a private LAN. Test at least the oldest Android version advertised and a current Android 15/16 device, including a vendor with battery restrictions. Use separate OS users or disposable app data and real OS credential stores. A same-host fixture or canned reply does not satisfy these rows. The only actual provider request should be a harmless task using the user's approved existing subscription; never spend money or execute a real workflow merely to complete this checklist.

Each row starts **NOT RUN** until an operator records dated evidence. A failure blocks the related release claim. A blocked prerequisite is not a pass.

| ID | Procedure | Pass condition | Status |
| --- | --- | --- | --- |
| P1 | Install matching signed mobile and packaged desktop artifacts on clean test profiles. Start desktop with its real credential store. | Packages launch, correct versions/fingerprints match the manifest, credential failures fail closed, no development environment is required. | NOT RUN |
| P2 | Scan a real QR, compare fingerprints and confirm the phone on desktop. Try expired/reused QR and decline a separate invitation. | Camera works, explicit confirmation controls trust, rejected invitations dispatch no work. | NOT RUN |
| P3 | Grant one existing thread and deny another. Send one harmless message through the existing provider subscription. Change the thread's provider/model on desktop. | Only granted history is visible, one durable acceptance and one response appear, conversation ID/context/history remain intact. | NOT RUN |
| P4 | Pair the two desktops, deny a fleet request, then grant the exact target project/tools locally and repeat a harmless request originating from the phone. Revoke the grant and retry. | Denied/revoked work never executes; authorized work runs once on the desktop worker; phone remains a controller; result returns to the correct thread. | NOT RUN |
| P5 | Stop worker/backend during queued and running requests, restart, reconnect and replay the same request IDs. | No duplicate dispatch; uncertain work reports outcome unknown until resolved; stale fleet evidence never claims a reachable or idle host. | NOT RUN |
| P6 | Revoke the phone while a request is queued. Attempt reconnect, replay and file access. | Revocation denies all new dispatch and file access; fresh pairing requires local confirmation. | NOT RUN |
| P7 | Upload and download 0-byte, 20,000-byte and 10 MiB files on LAN. Attempt oversized, corrupt, expired and wrong-thread transfers. Export, delete completed files and fill/recover quota. | Only full authenticated size/hash-matching files export; scope/size failures fail closed; capacity recovers without forgetting pairing or losing queued requests. | NOT RUN |
| P8 | Type with large fonts on a small portrait/landscape screen, use TalkBack and a keyboard. Deny then grant notifications. | Text/controls remain legible and reachable, focus and labels make sense, drafts survive, permission state is explained, previews stay generic. | NOT RUN |
| P9 | Upgrade a signed candidate with the same key and higher version code. Restart with queued messages, a draft and encrypted files. | Trust/history/files/draft remain usable, pending work retains IDs and is not dispatched twice. | NOT RUN |
| P10 | Close the desktop window while keeping backend alive, send from phone, then exit backend. | Backend-alive delivery works; backend-down UI truthfully reports interruption/queued state. | NOT RUN |
| P11 | On Android 12+ phones from at least two makers, including a Galaxy S20 on One UI 5.1, open Raticode from its icon and use the setup offer to choose Raticode as the digital assistant; check Not now, Don't ask again and that the offer stops once chosen. With the app closed, hold the power button and use the assist gesture; speak a harmless request and listen to the reply. Repeat with a PIN-locked phone, without an offline speech language, without an offline voice and over Tailscale on mobile data. Switch the assistant back. | The panel opens without the app, words are recognized on the phone, one request reaches desktop Rem and the reply is shown and read with an offline voice; nothing is sent while locked until the unlock; missing speech or voice support is explained and typing works; the previous assistant returns. | NOT RUN |

## Wi-Fi and background endurance

Recommended acceptance budget: one 24-hour run on home Wi-Fi with realistic sleep, roaming and router restarts. Use local outage injection to exercise failures. Record attempts, durable acceptances, final responses, duplicate executions, reconnect delay, unexpected exits and generic error categories, with no payload logging.

Recommended delivery target while both endpoints are alive and networking is available: 20 small messages per route, all durably accepted within 60 seconds and eventually answered by the selected provider. Measure provider response time separately. This is an acceptance target, not an advertised guarantee; document actual measured percentiles before setting a user-facing promise. Rate-limited and offline periods are explicitly excluded from the latency target but must retain pending work and show truthful state.

| ID | Procedure | Pass condition | Status |
| --- | --- | --- | --- |
| E1 | Phone on Wi-Fi, then mobile data only, then Wi-Fi. Repeat ten cycles including airplane mode and a changed desktop DHCP address. | No device traffic leaves over mobile data; reconnect follows Wi-Fi return; unavailable old addresses do not bypass pins; stable IDs prevent duplicate work. | NOT RUN |
| E2 | Hold foreground sync for 24 hours with screen off, battery saver and realistic intermittent connectivity; include Android data-sync service timeout on a disposable device. | No crash or data loss; service interruption is visible; manual reconnect resumes queued work; no claim of always-on delivery. | NOT RUN |
| E3 | Inject dropped, delayed and truncated TCP traffic between phone and desktop. | Buffers/deadlines stay bounded; corrupted traffic is rejected; recovery never duplicates execution. | NOT RUN |
| E5 | Pause network during file transfer; reconnect, cancel and expire incomplete transfers. | Only committed offsets resume, no partial export, quota recovers and unrelated queued messages survive. | NOT RUN |
| E6 | Deny notifications, force-stop/reopen, reboot and stop background sync explicitly. | No unsolicited service restart after explicit stop; user controls and Android restrictions are respected; reopening recovers durable state and generic notifications never expose content. | NOT RUN |

Any duplicate execution, unauthorized access, plaintext exposure or corrupted successful export is an immediate failure. Unexpected exits and unexplained missing durable messages also fail.

## Independent security review scope

An implementation agent's review is useful engineering work but does not satisfy independent review. Recommend a qualified reviewer who did not implement the feature, using the exact candidate revisions and built packages. Commissioning a paid review requires the owner's budget decision; do not incur a cost automatically.

Give the reviewer the v2 protocol document and schemas, mobile `live/` and `protocol/`, Android manifests/storage/notification code, desktop device enrollment/transport/registry/application code, Rem bridge, fleet authorization and file handling, dependency inventories, release build configuration, test evidence and this threat model:

- TLS 1.3 configuration, exact ALPN and P-256 pin/validity/self-signature checks, fresh sessions on reconnect/migration and no plaintext fallback.
- Invitation expiry, one-time-secret handling, concurrent use, explicit confirmation, crash recovery during enrollment, lost keys and durable activation before grants.
- Wi-Fi-only socket binding, strict input bounds, reordering/gap deadlines, replay/conflict handling and metadata/content leakage through logs and notifications.
- Stable immutable event IDs, durable acceptance transactions, sequence/cursor recovery, compaction, lifetime operation deduplication and unknown outcomes after crashes.
- Peer/thread/project/resource/tool authority, mobile-controller restrictions, phone-origin fleet grant, target-local approval, revocation races and filesystem traversal/symlinks.
- Android Keystore and desktop OS key storage, encrypted journal/blob integrity, export authentication, quota recovery, reset/cancellation races and backup exclusions.
- Signed/minified artifact behavior, dependency vulnerabilities, release key custody and upgrade preservation.
- The Wake computer packet, the only desktop traffic outside end-to-end TLS. It is unencrypted by design and holds only the hardware address. See "Wake the computer" in [architecture](architecture.md) for the accepted limits, including a foreign network that reuses the desktop's subnet numbers.

Require a written findings list with severity, reproduction and remediation verification. Block public release on unresolved critical/high findings or any unmitigated trust/authorization violation. Document accepted lower-severity risks and owner decisions rather than silently treating them as fixed.

## Decisions and recommendations still owned by the user

| Decision | Recommendation and prepared next step |
| --- | --- |
| Release scope/platform claims | Approve the private Android 10+/Linux experimental pilot above; defer broader OS and fleet claims until their acceptance rows pass. |
| Release identity and signing-key custody | Choose a candidate version and existing or new owner-managed release key; back it up offline with controlled access. Inject signing credentials only through protected environment variables, then build and verify the signed artifact. No key was generated here. |
| Distribution/update channel | Start with direct signed APK delivery to named testers plus hashes and certificate fingerprint. Decide a durable update/distribution policy before wider release. No store upload or publication is authorized by this checklist. |
| Independent reviewer/budget | Assign a reviewer independent of implementation and approve budget separately if needed; use the review scope above. |
| Hardware/subscription acceptance | Assign a physical phone and two Linux hosts, use disposable data and one approved harmless provider task, then record every P/E row. No hardware result can be inferred from emulator success. |
| Network expectations | Wi-Fi, or the user's own Tailscale network when they turn it on. Without Tailscale, phones away from the desktop's network cannot connect. |

## Preparation build evidence

On 19 September 2026, `./scripts/build-release.sh unsigned` completed successfully with JDK 21 and SDK/build-tools 36.0.0. Its `assembleRelease lintRelease` invocation completed 50 tasks in 41 seconds, with 14 executed and 36 up to date. Release lint reported zero errors, 14 warnings and one hint. Those warnings remain diagnostic items, not a claim of warning-free source. The produced manifest retains application ID `dev.raticode.mobile`, version code 2, version name `0.2.0-local`, min SDK 26 and target SDK 36.

The first minified build exposed Kotlin metadata warnings from AGP 9.0.1's R8 9.0.32. The root buildscript now pins R8 9.1.43. Android's [Kotlin compatibility table](https://developer.android.com/build/kotlin-support) requires R8 9.1.29 or later for Kotlin 2.4. The repeat build emitted no Kotlin metadata parsing warnings, and its mapping header confirms R8 9.1.43. No metadata-warning suppression or broad ProGuard keep rule was added.

| Final local artifact | SHA-256 |
| --- | --- |
| `app/build/outputs/apk/release/app-release-unsigned.apk` | `98420639b4555be7abcc6f0a692c9efe0ac1a7dd5936af6c06c598a8481aae4e` |
| `app/build/outputs/mapping/release/mapping.txt` | `c04768910f4e858969981c5dddd425b85e4ba294681e7eb74c8abb8fb5ebab9b` |

The helper confirmed absent signing; direct `apksigner verify` on the first unsigned candidate returned `DOES NOT VERIFY` with missing signing metadata, as expected. The helper's signed-without-credentials, unsigned-with-a-signing-variable and invalid-mode cases all rejected with exit 2. Direct Gradle configuration with only a test alias rejected with the required all-four-variables error. No real key or password was used for these negative checks.

Build output is ignored and may be replaced by later builds. Final logs, test XML and artifact hashes are retained locally in `.git/readiness-work-2026-09-19/verification.json` and its adjacent evidence files. The hashes above identify the final local candidate after source repairs and verification, not a published release. Rebuild after further relevant edits and record the new hashes.

Signed/minified installation and upgrade testing, physical-device acceptance, relay endurance and independent review have not run in this preparation task. No keys were generated, no artifacts were published and no paid service was used.
