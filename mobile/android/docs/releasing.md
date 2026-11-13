# Preparing and publishing a mobile release

This works like the desktop's release process (`docs/releasing.md` in the repository root). Both apps share `stage` and `main`; a stage push prepares only the app whose files changed. Set the version, push it to `stage`, wait for **Prepare mobile release candidate** to succeed, and review its draft release. When you're ready, squash merge `stage` into `main` and push the `mobile-v<version>` tag at the squash commit (or at the staged commit itself). The tag publishes the files that were already built and tested. It never starts a build.

| Phase | Trigger | Result |
| --- | --- | --- |
| Validate source | Pull requests, pushes to `main` and `dev`, and the first job of stage preparation | Version check, script tests, protocol fixtures, debug and release builds, unit tests, lint, and the release APK and App Bundle policy checks |
| Prepare candidate | Push to `stage`, or a manual run of **Prepare mobile release candidate** on `stage` | Release APK and App Bundle, signed when `ANDROID_RELEASE_SIGNING` is `true`; release lint; APK and bundle policy and signature checks; OSV dependency audit; a launch of the release APK on an API 36 emulator; instrumented tests on that emulator, recorded but non-blocking; checksums |
| Stage exact files | Successful preparation in the same run | Attested manifest, version-code and signing-key checks against earlier releases, a draft release with generated notes, and verified remote hashes |
| Squash merge | You merge `stage` into `main` | No workflow; `main` gets one commit with the staged files |
| Publish | Push of `mobile-vX.Y.Z`, then your approval of the `mobile-release-publish` deployment | Verifies the matching signed candidate and makes its draft public; a `-rc.N` version is published as a GitHub prerelease |

The scripts are `scripts/release-candidate.py` (all checks, staging and publication) and `scripts/release-policy.json` (what the release APK may contain). The workflows are `.github/workflows/mobile-android-ci.yml`, `mobile-release-candidate.yml` and `mobile-release.yml` in the repository root.

## What the Android checks cover

`release-candidate.py check-apk` reads the built APK with `aapt2` and `apksigner`. It fails when:

- the package, `versionName` or `versionCode` differ from `version.properties`, or the min/target SDK levels differ from the policy;
- the APK is debuggable or test-only, allows backups, or allows cleartext traffic;
- its permissions or exported components (with the permission guarding each) differ from `scripts/release-policy.json`;
- in signed mode, the signature doesn't verify, has more than one signer, lacks a v2 or v3 scheme, uses Android's debug key, or its certificate differs from `ANDROID_SIGNING_CERT_SHA256`;
- in unsigned mode, the APK carries a valid signature;
- a 64-bit native library has a LOAD segment aligned below 16 KB, which Google Play refuses for updates from 1 February 2027.

`check-bundle` applies the same manifest checks and the 16 KB check to the App Bundle Google Play takes (`app/build/outputs/bundle/release/app-release.aab`), reading its base module with `aapt2`. It also estimates the largest download one phone gets (every shared file plus one ABI's libraries) and fails above Play's 500 MB limit. In signed mode it verifies the bundle's JAR signature with `jarsigner`, requires exactly one signer that isn't the debug key, and requires the certificate to equal `ANDROID_SIGNING_CERT_SHA256` and the APK's. A signed candidate stages the bundle as `raticode-mobile-X.Y.Z.aab` with `bundle-checks.json`; an unsigned candidate checks the bundle but doesn't stage it, because Play refuses unsigned uploads.

A new permission or exported component is a deliberate review point, like the desktop's reviewed licence list. Review the change, then update `scripts/release-policy.json` in the same commit. CI runs this check on every push, so the policy fails early rather than on release day.

`audit` looks up every library in the release runtime classpath (118 at 0.20.0) in [OSV](https://osv.dev). An advisory fails the candidate unless `accepted_advisories` in the policy names it with a reason. A network failure fails the run; it never leaves an earlier clean report behind.

`device-test` records the instrumented test results and then installs the release APK on the emulator, starts it, waits five seconds and checks that it is still running, did not crash, reports the right version and is not debuggable. An unsigned candidate is installed as a copy signed with a throwaway key, because Android won't install an unsigned APK. The command refuses to run on anything but an emulator, since it uninstalls the app first.

**The instrumented emulator tests don't block a candidate.** The workflow passes Gradle's exit code to `device-test`, which records the counts, the exit code and the names of failed tests in `device-tests.json`, and the candidate is still built, checked, launched and staged. Failures show in three places: the build job's summary ("Emulator tests failed (non-blocking)" with the failed test names and a warning annotation), a separate **Emulator tests (non-blocking)** job that turns red while the run stays green, and the draft's release notes and `instrumented_tests` in the manifest, which then lists `instrumented-tests` among its checks only when they passed. The `candidate-reports` artifact keeps the test reports and per-test logcat for 14 days. Everything else still fails the run: the release build and lint, the APK policy and signature checks, the dependency audit, the release APK's launch, and an emulator run that produced no test results at all (for example when the test APK didn't build).

The R8 mapping file is uploaded as the `r8-mapping` Actions artifact for 90 days, not to the public release. `apk-checks.json` records its hash. Download it during review and keep it with the release; it turns obfuscated crash reports back into readable stack traces.

## Differences from the desktop

| | Desktop | Mobile |
| --- | --- | --- |
| Tag | `vX.Y.Z` | `mobile-vX.Y.Z`, so the two never collide in their shared repository |
| Versions | Stable `X.Y.Z` only | Prereleases such as `1.0.0-rc.1` are allowed and published as GitHub prereleases |
| Unsigned releases | Published by default | Staged as build evidence only. Publication refuses them, because Android can't install an unsigned APK |
| Signing | Paid Apple and Windows certificates, optional | A key you create yourself with `keytool`, free, and required to publish |
| Update rules | The updater checks hashes | Android installs an update only with a higher `versionCode` and the same signing key, so staging and publishing compare both with every earlier mobile release |
| Packages | Linux, Windows and macOS packages, CLIs | One APK plus `apk-checks.json`, `device-tests.json`, checksums and the dependency audit. Signed candidates add the App Bundle for Google Play and `bundle-checks.json` |
| Runtime tests | Packaged backends start on each OS | Instrumented tests plus a launch of the minified release APK on an emulator |
| Dependency audit | pip-audit and npm audit of the lockfiles | OSV lookup of the resolved release runtime classpath; Gradle has no lockfile here |
| Updates on devices | Built-in updater on Windows and Linux | None. People install each APK by hand |

The rest matches the desktop: stage-only candidates, a seven-day limit before first publication, provenance attestation, identical-file checks for squash merges, no asset replacement and safe retries.

## One-time setup

The release key exists on the maintainer's computer (created 11 November 2026). The GitHub settings below have not been configured.

### The release key

Every future update must be signed with the same key. If it is lost, phones can't update; the only way forward is uninstalling, which deletes the app's pairing and history. Google Play needs the same key too.

It lives outside any repository, in an owner-only folder (`0700`, files `0600`):

| File in `~/.local/share/raticode-android-signing/` | Holds |
| --- | --- |
| `raticode-release.jks` | The PKCS12 keystore, alias `raticode`, RSA 4096, valid until 2054 |
| `keystore-password` | The password, no trailing newline. PKCS12 uses it for the key too |
| `certificate-sha256.txt` | The public SHA-256 certificate fingerprint |
| `signing.properties` | No secrets: points Gradle at the files above |

It was created without the password ever reaching a command line, with `keytool -genkeypair ... -storepass:file keystore-password -keypass:file keystore-password`. To make a new one elsewhere, generate the password into an owner-only file first and pass it the same way. Back the keystore and password up offline in two places. Never commit either, and keep them out of Second Brain.

### GitHub settings

| Setting | Where | Value |
| --- | --- | --- |
| `ANDROID_RELEASE_SIGNING` | Repository variable | `true` once the rest is in place. Unset or `false` prepares unsigned candidates, which can't be published |
| `ANDROID_SIGNING_CERT_SHA256` | Repository variable | The SHA256 fingerprint from `keytool -list`, with or without colons. It is public. It has to be a repository variable so the tag job, which runs outside the signing environment, can check it |
| `ANDROID_KEYSTORE_BASE64` | Secret in environment `android-release-signing` | The keystore, base64-encoded |
| `ANDROID_KEYSTORE_PASSWORD` | Secret in `android-release-signing` | The keystore password |
| `ANDROID_KEY_PASSWORD` | Secret in `android-release-signing` | The key password (the same as the keystore's for PKCS12) |
| `ANDROID_KEY_ALIAS` | Variable in `android-release-signing` | `raticode` |

Create the **android-release-signing** environment. It is separate from the desktop's `release-signing`, so turning on phone signing never asks desktop builds for Apple or Windows certificates under the repository's Settings > Environments, and limit its deployment branches to `stage`. Store the secrets straight from those files, without printing them (`gh secret set` reads the value from standard input):

```bash
key="$HOME/.local/share/raticode-android-signing"
base64 -w0 "$key/raticode-release.jks" | gh secret set ANDROID_KEYSTORE_BASE64 --repo OWNER/REPO --env android-release-signing
gh secret set ANDROID_KEYSTORE_PASSWORD --repo OWNER/REPO --env android-release-signing < "$key/keystore-password"
gh secret set ANDROID_KEY_PASSWORD --repo OWNER/REPO --env android-release-signing < "$key/keystore-password"
gh variable set ANDROID_KEY_ALIAS --repo OWNER/REPO --env android-release-signing --body raticode
gh variable set ANDROID_SIGNING_CERT_SHA256 --repo OWNER/REPO < "$key/certificate-sha256.txt"
``` The **Release tags** ruleset lets only repository admins create, move or delete `mobile-v*` tags, and the publish job waits in the `mobile-release-publish` environment for your approval (see Release approval in the root `docs/releasing.md`). Changes to `.github/workflows/` aren't protected by a rule; a maintainer with write access could still change the workflow on a branch.

The build job writes the keystore to the runner's temporary folder with owner-only permissions and deletes it after Gradle finishes. The Gradle build refuses partial signing settings and never falls back to the debug key.

## Candidate review and tagging

1. Set the version with `python3 scripts/version.py bump ...` (see [versions and release tags](versioning.md)), then run `npm run set-release -- --mobile` in `website/` so the site's APK download names the same version (with a desktop release, `npm run set-release -- X.Y.Z` does both). Add the README update notes, commit everything in one commit, and push to `origin/stage`. `npm run check` in `website/` fails while the two versions differ. Every stage push prepares a candidate, and each candidate given to testers needs its own higher version code.
2. Wait for the whole **Prepare mobile release candidate** run to succeed. Open the draft linked in the **Stage exact files** job summary. It is named `mobile-candidate-<commit>-<run>-<attempt>`.
3. Download the draft's files and the `r8-mapping` artifact. Check them:

   ```bash
   sha256sum -c checksums-android.txt
   "$ANDROID_HOME/build-tools/36.0.0/apksigner" verify --print-certs raticode-mobile-X.Y.Z.apk
   ```

   The certificate's SHA-256 digest must equal the fingerprint you recorded.
4. Install the APK on a test phone over the previous release and run the physical-phone acceptance checks (pairing, sync, files and notifications on a real phone). A phone with a debug build can't take the release build as an update, because the keys differ. Use a separate phone or profile rather than uninstalling a debug build whose pairing and history you need.
5. Squash merge `stage` into `main`. The squash commit has a different SHA but this app's folder must have exactly the same files. These two lines must print the same tree:

   ```bash
   git rev-parse 'REVIEWED_FULL_COMMIT_SHA:mobile/android'
   git rev-parse 'origin/main:mobile/android'
   ```

   If they differ, bring `stage` up to date with `main`, push it, and review the new candidate instead.
6. Tag and push. Replace the placeholders first:

   ```bash
   git tag -a mobile-vX.Y.Z SQUASH_FULL_COMMIT_SHA -m 'Raticode Mobile X.Y.Z'
   git push origin refs/tags/mobile-vX.Y.Z
   ```

   When the desktop releases from the same squash commit, push both tags in one command, such as `git push origin refs/tags/mobile-vX.Y.Z refs/tags/vA.B.C`. GitHub creates no tag events when more than three tags are pushed at once.

7. **Publish mobile release** waits for your approval: open its run in the Actions tab, choose **Review deployments**, tick `mobile-release-publish` and choose **Approve and deploy**. It then checks that the tag equals `mobile-v` plus `versionName`, that the remote tag hasn't moved, and that exactly one ready draft has the tagged commit's files. It then checks the draft's attestation, its successful stage run, its signing certificate against `ANDROID_SIGNING_CERT_SHA256`, its age, every remote SHA-256, and that its version code is higher than, and its key the same as, every earlier mobile release. Only then does it publish the draft. It never replaces a file.

8. The website follows by itself. It always links the APK of the version in `version.properties` (step 1), and its deploy waits until that release is public: `npm run check:downloads` exits 3 while `mobile-vX.Y.Z` is missing or a draft, so the live site keeps its previous version and never links a missing file. When **Publish mobile release** (or the desktop's **Publish release**) finishes, the **Website docs** workflow runs again on `main` and deploys once every release the site links is public. Once public, the check fails (exit 1) if the release lacks `raticode-mobile-X.Y.Z.apk`, `checksums-android.txt` or `apk-checks.json`, if `apk-checks.json` records a different version or a certificate other than `mobileSigningCertificate` in `website/data/releases.ts`, or if a newer mobile release exists.

Don't use the web **Publish release** button. It skips these checks and would publish the internal candidate name.

## Retries and candidate lifetime

- A tag pushed before preparation finishes fails without creating a release. Let preparation finish, then rerun the failed publish job.
- Upload retries accept identical existing files and refuse different ones. A rerun gets a new attempt number and its own draft.
- If two ready candidates match the tagged files, publication refuses to choose. Delete the superseded draft, then rerun.
- A candidate must be at most seven days old when first published. Re-prepare a stale one so its dependency audit is current.
- If publication succeeds but the final response is lost, rerunning verifies the existing release without changing it.
- Keep the draft and its preparation run until publication; the publish job verifies both.

## Local checks

`./scripts/build-release.sh unsigned` builds the release APK and App Bundle and runs `check-apk` and `check-bundle` on them. `signed` mode needs either `RATICODE_RELEASE_SIGNING_PROPERTIES` (the owner-only `signing.properties` above, which also supplies the fingerprint) or the four `RATICODE_RELEASE_*` variables and `ANDROID_SIGNING_CERT_SHA256`:

```bash
RATICODE_RELEASE_SIGNING_PROPERTIES="$HOME/.local/share/raticode-android-signing/signing.properties" ./scripts/build-release.sh signed
```

The signed bundle is `app/build/outputs/bundle/release/app-release.aab`. To launch-test the APK on an emulator without stealing focus, start one headless (`emulator -avd NAME -no-window -read-only`), then:

```bash
SIGNED_RELEASE=false python3 scripts/release-candidate.py collect --output /tmp/raticode-candidate
python3 scripts/release-candidate.py device-test --candidate /tmp/raticode-candidate/android --serial emulator-5590
```

`device-test` uninstalls the app from that emulator before and after the launch.

## Not verified yet

The candidate workflow has run on GitHub since November 2026. The release key exists on the maintainer's computer, but the GitHub signing settings aren't configured yet, so every candidate so far is unsigned: its APK is staged in a draft as build evidence, but publication refuses it and Android won't install it. 0.34.0 is planned as the first signed release, published with desktop 0.5.4. Some instrumented tests fail on GitHub's fresh emulator (a permission dialog nothing dismisses, timing, and a test that resets the assistant role from inside the app); those are recorded as non-blocking until the tests are fixed.
