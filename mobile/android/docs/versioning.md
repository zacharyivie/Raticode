# Versions and release tags

The app version lives in one file, [`version.properties`](../version.properties). Gradle reads it for `versionName` and `versionCode`, and the build fails if either value is malformed. Don't type a version into `app/build.gradle.kts`; a script test fails if anyone does.

## What the two numbers mean

`versionName` is what people see in Android's app info. It follows [SemVer 2.0.0](https://semver.org/): `MAJOR.MINOR.PATCH`, with an optional prerelease such as `1.0.0-rc.1`. Build metadata (`+...`) and a leading `v` are not allowed.

- Raise **MAJOR** when a phone with the new version can no longer pair or sync with a desktop the previous version worked with, or when a stored journal or vault needs a one-way migration. While the app is `0.x`, raise MINOR for these instead.
- Raise **MINOR** for a user-visible feature, including one that needs a matching desktop update. The README's update notes name the minor version that added each feature.
- Raise **PATCH** for fixes and small changes that need no desktop change.
- Use a **prerelease** (`-rc.1`, `-rc.2`) for a candidate given to testers before the final version.

`versionCode` is the number Android compares when it installs an update. It goes up by one every time the version changes, never goes down and is never reused, even for a candidate that was thrown away. Android refuses to install a lower code over a higher one, so a reused or lowered code strands installed phones. The code has no meaning beyond that ordering.

Debug builds add `-debug` to the name (`0.9.0-debug`) and keep the same code. Release builds use the name as written.

The history before this file existed went `0.1.0-demo` (1) through `0.9.0` (14). The suffixes on early names such as `0.5.0-desktop-parity` were labels, not SemVer prereleases; new versions don't use them.

## Changing the version

Change the version in the same branch and commit as the feature or fix that justifies it, or in a separate commit before pushing a release candidate to `stage`. The candidate is built with the version it finds there, and the tag must match it. Use the script; it refuses names that don't move forward and always raises the code by one.

```bash
python3 scripts/version.py show            # 0.9.0 (14)
python3 scripts/version.py bump minor      # 0.9.0 (14) -> 0.10.0 (15)
python3 scripts/version.py bump patch      # 0.10.0 (15) -> 0.10.1 (16)
python3 scripts/version.py bump 1.0.0-rc.1 # an exact newer version
python3 scripts/version.py bump 1.0.0      # finish a candidate
python3 scripts/version.py check           # what CI runs
```

`--code N` sets a specific higher code, for example to skip codes already given to testers from another branch. The script edits only `version.properties`. It never commits, tags, builds or publishes.

When a version needs a matching desktop change, say so in the README update notes ("Version 0.7.0 requires the matching desktop backend") and in `docs/mobile-desktop-v2.md`, as earlier versions did.

## Release tags

A release tag is `mobile-v<versionName>`, for example `mobile-v0.10.0` or `mobile-v1.0.0-rc.1`. The `mobile-` prefix keeps these tags apart from the desktop's `v*` tags. The two apps share a repository, the desktop already has `v*` tags, and its release workflow publishes on any `v*` tag.

A pushed tag publishes the release. It never builds anything: it makes public the signed candidate that a push to `stage` already built, tested and staged as a draft, after checking that the tag matches `version.properties` and the candidate's files match the tagged commit. [Preparing a release](releasing.md) has the full steps. Never move or reuse a pushed tag; fix a bad release with the next version.

## Continuous integration

`.github/workflows/mobile-android-ci.yml`, in the repository root, runs on pushes to `main` and `dev` and on pull requests when phone files or the shared protocol change, every five days to keep the Gradle cache warm, and by hand. A push to `stage` runs it as the first job of the release candidate (`.github/workflows/mobile-release-candidate.yml`), and `mobile-v*` tags run only the publish workflow.

| Job | What it runs |
| --- | --- |
| Version, scripts and protocol | `scripts/version.py check`, the script tests, and `scripts/check-contract.py` |
| Build, unit tests and lint | `./gradlew assembleDebug testDebugUnitTest lintDebug assembleRelease lintRelease`, then checks that the debug APK carries the version from `version.properties` and runs `scripts/release-candidate.py check-apk --signing unsigned` on the release APK. Uploads the debug APK for 14 days, and test and lint reports when a step fails. |
| Instrumented tests (emulator) | `connectedDebugAndroidTest` on an API 36 emulator. Runs only when started by hand with **emulator-tests** ticked. Release candidates always run them. Tests that need the desktop fixture skip themselves. |

The release APK in this workflow is unsigned. No signing key or secret reaches it, and it has read-only repository access. Actions are pinned to commit SHAs, and Dependabot proposes weekly updates for actions, Gradle and the protocol check's Python dependency.

To run the same checks locally:

```bash
python3 scripts/version.py check
python3 -m unittest discover -s scripts -p 'test_*.py'
.venv-contract/bin/python scripts/check-contract.py
./gradlew assembleDebug testDebugUnitTest lintDebug
./scripts/build-release.sh unsigned   # assembleRelease lintRelease, then check-apk
```
