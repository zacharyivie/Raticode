# Working on Raticode Mobile

This app lives at `mobile/android` in the Raticode repository. Run the commands below from this folder. The repository's root `AGENTS.md` covers the desktop app.

## Product

Raticode Mobile lets a user pair their phone with Raticode Desktop by QR code and chat with Rem remotely. Planned messages include text, speech-to-text, attachments, returned files, swarm requests, status updates, and notifications. Desktop Rem decides which threads and agents handle a request. Keep that responsibility on the desktop.

Always write the agent's name as **Rem**, never all caps. Explain the expanded name only when asked. Conversation identity, project context, and resource selections belong to a thread, independently of its provider or model.

## Current state

This Android Kotlin/Jetpack Compose app opens into live pairing and authorized desktop conversations. The historical simulator exists only in test source sets. Live mode uses Android Keystore identities, pinned end-to-end TLS 1.3 over Wi-Fi, an opt-in route through the user's own Tailscale network, encrypted trust and message journals, explicit desktop thread grants, scoped file transfers, per-thread resource toggles, opt-in approvals and swarm controls, opt-in desktop control (Rem, organizations, workflows to view, run, stop and resume, swarm start, display settings and usage), a user-started foreground sync service, and an Android 12+ voice mode that can serve as the phone's digital assistant using on-device speech only. The desktop owns Rem dispatch and all execution policy. Keep delivery states tied to authenticated durable receipts. Independent security review and physical phone/two-host acceptance remain outstanding. Read docs/mobile-desktop-v2.md and docs/live-device-setup.md before changing peer behavior.

## Layout and commands

- `app/src/main/java/dev/raticode/mobile/ui`: Compose screens, theme, and view model.
- `app/src/main/java/dev/raticode/mobile/data`: bounded document import and file metadata helpers.
- `app/src/test`: local behavior tests.
- `app/src/androidTest`: emulator interaction tests.
- `docs/architecture.md`: boundaries, security requirements, and open decisions.
- `protocol/v1`: application schemas and public test fixtures.
- `../../src/gofer/devices/protocol/v2`: the one copy of the v2 schemas and fixtures, shared with the desktop. Change it and the app together in one commit.
- `docs/desktop-handoff.md`: required desktop integration.
- `.venv-contract/bin/python scripts/check-contract.py`: schema checks after installing protocol/requirements.txt.
- `gradle/libs.versions.toml`: pinned dependency versions.
- `version.properties`: the app version. Change it only with `python3 scripts/version.py bump ...`; see docs/versioning.md.
- `../../.github/workflows/mobile-android-ci.yml` and `contract-check.yml`: CI. Keep it in step with the commands below; actions stay pinned to commit SHAs.
- `../../.github/workflows/mobile-release-candidate.yml`, `mobile-release.yml`, `scripts/release-candidate.py`: stage release candidates and `mobile-v*` publication; see docs/releasing.md. A new permission or exported component needs a matching, reviewed change to `scripts/release-policy.json`.
- `./gradlew assembleDebug testDebugUnitTest lintDebug`: build and local checks.
- `./gradlew connectedDebugAndroidTest`: interaction tests on a running emulator. Start it with `-no-window`, and set `ANDROID_SERIAL` to a private emulator.
- `./scripts/run-android.sh`: build, install, and launch on one emulator or selected device.

Use JDK 21, Android SDK platform 37 (GeckoView compiles against it; the app targets 36), and the committed Gradle wrapper. AGP 9 provides Kotlin support; do not add `org.jetbrains.kotlin.android`. Keep host paths in ignored `local.properties` or environment variables. Do not commit build output, signing keys, relay tokens, pairing secrets, or user files.

## Implementation

Keep session/transport behavior out of composables. Use explicit state and stable message IDs. Handle lifecycle, cancellation, retries, duplicate delivery, offline use, and accessibility when the relevant feature is added. UI strings are English-only in this prototype; extract them into resources before localization. Use readable contrast, semantic controls, and text labels. Check small screens and keyboard visibility when changing layouts.

E2EE is a requirement. The contract proposes a Noise profile, but library selection, session encoding, interoperability, and independent review remain release gates. Do not invent cryptography or add a plaintext fallback. Device traffic stays on Wi-Fi; do not add an internet relay, mobile-data route or cloud fallback unless the user asks. Document protocol decisions and use maintained, reviewed cryptographic libraries. File names, message bodies, and notification previews must not leak outside end-to-end TLS. Do not log keys, plaintext, or pairing payloads. See the architecture document before integrating real communication.

The mobile app must not silently gain authority to execute desktop work, merge code, or read arbitrary files. Desktop authorization and existing approval policies remain authoritative. A QR scan must not execute a command. Adding a provider/model selector must preserve thread identity and history.

Run checks appropriate to each change. The website (`../../website/docs/mobile/`) is the single source of truth for user documentation: a new phone feature, or a change users would notice, must update those pages before it is merged to `dev` (see the root `AGENTS.md` and `scripts/check-website-docs.py`; use `Docs-not-needed: <reason>` in the commit message when nothing user-facing changes). Update the architecture notes and README for developer-facing behavior or setup. Report what actually ran and any unimplemented features. Do not publish, push, deploy, or execute real workflows unless the user authorizes it.
