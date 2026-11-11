# Preparing and publishing a release

Push the final version to `stage`, wait for **Prepare release candidate** to succeed,
and review its draft downloads. When you're ready, squash merge `stage` into `main`
and push the version tag at the squash commit (or at the staged commit itself).
Tagging publishes the already staged files. It never starts a build.

| Phase | Trigger | Result |
| --- | --- | --- |
| Validate source | Pull request, and the first job of stage preparation | Locked dependency audits, Python lint/types/full tests, frontend lint/unit/browser tests, package-version agreement |
| Prepare candidate | Push to `stage`, or manual **Prepare release candidate** on `stage` | Windows x64, macOS arm64 and Linux x64 packages; macOS always Developer ID signed, notarized and stapled, Windows signed only when configured; native tests, signing checks and distribution backend tests |
| Stage exact files | Successful preparation in the same run | Attested manifest, complete draft release, release notes and verified remote hashes |
| Squash merge | You merge `stage` into `main` | No workflow; `main` gets one new commit with the staged files |
| Publish | Push of `vX.Y.Z` | Verify the matching successful candidate, refuse it unless its Mac packages are signed, and make its existing draft public |

Pushes to `main` no longer build or stage anything. A pull request from `stage` into
`main`, such as the one GitHub's **Squash and merge** button uses, still runs
**Validate source** on the pull request. Release notes are generated when the draft is
staged, from the staged commit.

The old unsigned dry-run and duplicate frontend-check workflows are replaced by
`validate-source.yml` and `release-candidate.yml`. Update any required status checks
that referred to their old names. The tag workflow has no build, package upload,
signing, notarization, audit or dependency-install step.

## Two apps, one repository

Raticode Mobile lives in `mobile/android` and has its own guide, `mobile/android/docs/releasing.md`.

- A `stage` push prepares a desktop candidate unless every changed file is under `mobile/android/` or `website/`, or is a `.github/workflows/mobile-*.yml` file. It prepares a mobile candidate when phone files, the shared protocol or those workflows change. A change to both prepares both at the same commit.
- `vX.Y.Z` tags publish the desktop and `mobile-vX.Y.Z` tags publish the phone app. Their versions are independent.
- The desktop updater reads the repository's latest release, so desktop releases set `make_latest` to `true` and phone releases always set it to `false`. After publishing a phone release, confirm `/releases/latest` still names a `v*` tag.
- Each app's "same files" check compares only that app. A fix to one app leaves the other app's candidate valid.
- Phone signing uses `ANDROID_RELEASE_SIGNING` and the `android-release-*` environments, so it never affects desktop builds.
- The phone candidate's instrumented emulator tests are recorded but don't block it: the APK is still built, checked, launched and staged, and failures show in the build job's summary and a separate **Emulator tests (non-blocking)** job. Only signed phone candidates can be published. After a `mobile-vX.Y.Z` release is public, `npm run set-release -- --mobile X.Y.Z` in `website/` turns on the site's APK download (see `mobile/android/docs/releasing.md`).
- The phone app's own commit history was not carried into this repository: it arrived as part of one squashed commit (`bc75634c`). The old phone repository and its backups remain the record of that history, so `main` takes ordinary squash merges like any other release.

## The website

The public site in `website/` deploys to GitHub Pages from the **Website docs** workflow
(`.github/workflows/website.yml`). User documentation lives there; see the website's
`README.md` for local commands.

- **What deploys.** Only `main`. The deploy job runs after `npm run check` (type check,
  build, internal links and anchors, sitemap and robots.txt) and `npm run check:downloads`
  (every download link exists on the newest published desktop release) both pass. Pull
  requests and `dev` pushes build and check the site but never deploy it, and neither
  does the weekly scheduled check.
- **When it deploys.** On a push to `main` that changes `website/`, when **Publish
  release** finishes successfully, and from **Run workflow** with `main` selected.
- **Releases.** While preparing a release (step 1 below), run
  `npm run set-release -- X.Y.Z` in `website/`. It moves `data/releases.ts` and the
  versioned file names in the install guide to the new version and lists other lines that
  mention the old one, to reword by hand. After the squash merge, the push to `main`
  finds that `vX.Y.Z` isn't published yet; the job records "Website waits for its
  release", keeps the old site live and succeeds. The deploy happens when the tag's
  **Publish release** run finishes. If a release is published without moving the site,
  the download check fails and the old site stays up until you run `set-release` and
  push.
- **One-time setup (repository owner).** In **Settings > Pages > Build and deployment**,
  set **Source** to **GitHub Actions**. GitHub creates the `github-pages` environment on
  the first deploy; its deployment rule should allow `main` only. Until Pages is turned
  on, the deploy job fails while the build and checks still pass.
- **Address.** The site builds for `https://zacharyivie.github.io/Raticode/`. Two optional
  repository variables change that (**Settings > Secrets and variables > Actions >
  Variables**): `WEBSITE_URL`, the full address, and `WEBSITE_BASE`, the path it is
  served from, which defaults to the path of `WEBSITE_URL`. The sitemap, robots.txt,
  canonical links and the social preview image use the full address.
- **Custom domain.** Verify the domain under your profile's **Settings > Pages** first,
  add it in the repository's **Settings > Pages > Custom domain**, then create the DNS
  records GitHub lists (apex `A`/`AAAA` records to GitHub's Pages addresses, or a `www`
  `CNAME` to `zacharyivie.github.io`). In the same sitting set `WEBSITE_URL` to
  `https://<domain>/` and run the workflow on `main`, because a `/Raticode/` build served
  from the domain root has no styles or scripts. Turn on **Enforce HTTPS** once the
  certificate is issued. No `CNAME` file is needed: GitHub ignores it for sites deployed
  by a workflow.

## Mac releases are always signed and notarized

Every **Prepare release candidate** run in `zacharyivie/Raticode` signs the Mac app
with the Developer ID Application certificate, turns on the hardened runtime,
notarizes it with Apple and staples the ticket. There is no variable that turns this
off. If a secret is missing, or signing or notarization fails, preparation fails; it
never falls back to an unsigned Mac build. A fork has no Apple secrets, so its
candidates build an explicitly unsigned, ad-hoc signed Mac app (labelled "Unsigned
macOS build" in the run), and the tag workflow refuses to publish any candidate whose
Mac packages aren't signed.

What the macOS job does, in order:

1. **Prepare macOS signing keychain** (`scripts/macos-release-signing.py prepare`), the
   only step that receives the Apple secrets. It checks that the `.p12` holds a
   *Developer ID Application* certificate with its private key and isn't expired,
   reads the team ID from the certificate, re-encodes the identity under a random
   password (macOS can't import the AES-encrypted `.p12` that OpenSSL 3 exports on
   Linux, and the export password never reaches a command line), imports it into a
   temporary keychain together with Apple's intermediate certificates from
   electron-builder, confirms macOS trusts it for code signing, and stores
   notarytool's credentials in that keychain. Storing them checks the Apple ID,
   app-specific password and team with Apple, so bad credentials fail here, before the
   build. Later steps receive only the identity's SHA-1 hash, the team ID, the keychain
   path and the profile name.
2. **Build backend**: PyInstaller signs the backend and the standalone CLI with that
   identity, the hardened runtime, a secure timestamp and
   `frontend/build-resources/entitlements.mac.plist`.
3. **Build macOS speech helper**, then **Build Electron packages**: electron-builder
   signs every Mach-O file inside `Raticode.app` (Electron's helpers and frameworks,
   node-pty, the backend copy in `Contents/Resources/backend` and the speech helper in
   `Contents/Resources/speech`) with the hardened runtime and the same entitlements,
   then notarizes the app through the keychain profile and staples its ticket before
   making the ZIP and DMG.
4. **Verify macOS signatures and notarization** (`macos-release-signing.py verify`):
   checks the app, backend, speech helper and CLI for this team's Developer ID, a secure
   timestamp and the hardened runtime, the app's entitlements (audio input present,
   `get-task-allow` absent), `codesign --verify --deep --strict`,
   `xcrun stapler validate` and `spctl --assess --type execute`. It then signs the DMG
   with a timestamp, notarizes it, staples it, validates the staple, runs
   `spctl --assess --type open`, and rebuilds the DMG's blockmap and its
   `latest-mac.yml` entry (stapling changes the DMG's bytes). Finally it notarizes the
   CLI. Apple's receipts are saved as `notarization-macos-dmg.json` and
   `notarization-macos-cli.json`; if Apple rejects a file, the step prints Apple's log.
5. **Delete temporary macOS signing keychain**, which runs even when a step failed.
6. **Verify final updater hashes** checks every `latest-*.yml` hash and every blockmap
   against the final files, and **Smoke-test final distributions** repeats the
   signature, staple and Gatekeeper checks on the app extracted from the ZIP and from
   the DMG, checks that the app's updater label says `signed`, and records
   `"signing": "developer-id"` and the team in `package-tests-macos.json`. The manifest
   step refuses Mac evidence that doesn't match the candidate's policy.

The entitlements are the same for the app and everything inside it: JIT and unsigned
executable memory (Electron and Python's native libraries), disabled library
validation (the backend loads its own libraries, including Vosk, at run time) and
audio input (dictation). macOS has no entitlement for speech recognition; the
`NSSpeechRecognitionUsageDescription` in the app's Info.plist covers it.

### Setup checklist: GitHub secrets

The workflow reads these **repository secrets** (Settings > Secrets and variables >
Actions > Repository secrets). Environment secrets with the same names in the
`release-signing` environment work too and take precedence. Values are never printed.

| Secret | Required | What to put in it |
| --- | --- | --- |
| `MACOS_CERTIFICATE` | Yes | The Developer ID Application `.p12` (certificate **and** private key), base64-encoded: `openssl base64 -A -in developer-id.p12 -out developer-id.base64`, then paste the file's contents. Line breaks from `base64` are fine. `APPLE_CERTIFICATE_P12_BASE64` or electron-builder's `CSC_LINK` is accepted instead. |
| `MACOS_CERTIFICATE_PASSWORD` | Yes | The password chosen when exporting the `.p12`. `APPLE_CERTIFICATE_PASSWORD` or `CSC_KEY_PASSWORD` is accepted instead. |
| `APPLE_ID` | Yes | The Apple Account email that is a member of the developer team. May be a repository variable instead. |
| `APPLE_APP_SPECIFIC_PASSWORD` | Yes | An app-specific password from [account.apple.com](https://account.apple.com) > Sign-In and Security > App-Specific Passwords. Not your Apple Account password. |
| `APPLE_TEAM_ID` | No | The 10-character Team ID. If set, it must match the certificate's team; if not, the team is read from the certificate. May be a repository variable. |
| `MACOS_SIGNING_IDENTITY` | No | Only to pin the identity: its full name (`Developer ID Application: Name (TEAMID)`) or SHA-1 hash. It must match the certificate. May be a repository variable. |

1. Add the four required secrets above.
2. In [Settings > Environments](https://github.com/zacharyivie/Raticode/settings/environments),
   open **release-signing** (GitHub creates it on the first signed run if it doesn't
   exist). Under deployment branches choose **Selected branches and tags** and add a
   branch rule for `stage` only. If an older setup restricted it to `main`, change it,
   or the macOS job waits forever. Required reviewers are optional.
3. Push the release to `stage`. The **Prepare macOS signing keychain** step reports the
   identity, team and certificate expiry, and fails with the name of any missing or
   wrong secret.

### Rotating the secrets

- **App-specific password** (any time, or if it leaks): create a new one at
  account.apple.com, replace `APPLE_APP_SPECIFIC_PASSWORD`, then revoke the old one.
- **Certificate** (it lasts five years; the keychain step warns 30 days before expiry):
  make a new Developer ID Application certificate from a new certificate signing
  request, export it with its key as a new `.p12`, and replace both
  `MACOS_CERTIFICATE` and `MACOS_CERTIFICATE_PASSWORD` (or whichever accepted names
  you used, such as `APPLE_CERTIFICATE_P12_BASE64` and `APPLE_CERTIFICATE_PASSWORD`). Keep the old certificate valid
  until a release signed with the new one is published; don't revoke it unless its key
  leaked, because revoking a Developer ID certificate can stop apps signed with it from
  opening. A new certificate from the same team keeps updates and macOS permissions
  working, because both check the bundle ID and team, not the certificate.
- **Apple ID**: if another team member takes over notarization, replace `APPLE_ID` and
  `APPLE_APP_SPECIFIC_PASSWORD` together.
- After any change, push to `stage` (or rerun **Prepare release candidate**) and check
  that the keychain step accepts the new values.

### Updates and permissions

electron-updater installs Mac updates from the ZIP and checks it against
`latest-mac.yml`. macOS's updater (Squirrel.Mac) only accepts an update signed with the
same team as the running app, and Raticode only installs Mac updates itself when the
app's packaged label `raticodeReleaseSigning` is `signed`. Signed builds carry that
label, so after the first signed release, updates download and install automatically.

Copies installed from an unsigned release (0.5.2 or earlier) are labelled unsigned, so
they keep offering **Download Mac installer**. Users of those copies install the first
signed DMG by hand once: quit Raticode, open the DMG and drag Raticode into
Applications to replace the old copy. Release notes of signed releases say so.

macOS keys microphone and speech recognition permissions to the app's bundle ID
(`com.goferflow.desktop`) and its code signature. An ad-hoc signature changes with every
build, so unsigned releases could lose those permissions on each update. With a stable
Developer ID signature they should carry over from one signed release to the next. The
first signed copy is a new signature, so macOS asks once more; if it doesn't ask, or
dictation still fails, run `tccutil reset Microphone com.goferflow.desktop` and
`tccutil reset SpeechRecognition com.goferflow.desktop`, then use **Test speech
recognition** in Settings > Devices.

### Still unverified until the first signed run

These were written and tested against fake Apple tools and a stand-in certificate on
Linux; no real Mac signing or notarization has run yet:

- that `security import` accepts the re-encoded identity and `find-identity` trusts it
  on the `macos-15` runner, and that notarytool accepts the stored profile;
- that Apple accepts the app, DMG and CLI (for example, that nothing inside the
  PyInstaller backend trips notarization) and that stapling succeeds;
- that the hardened-runtime backend, speech helper and dictation work on a real Mac;
- that the codesign, stapler and spctl output parsed by the checks matches the
  runner's tools;
- Gatekeeper on a downloaded copy, the manual move from an unsigned copy, an automatic
  update between two signed releases, and microphone and speech permissions surviving
  that update.

## Windows signing is optional

Windows packages are unsigned unless the repository variable
`WINDOWS_RELEASE_SIGNING` is set to the exact value `true`. It must be a repository
variable because the caller reads it before entering an environment. Unsigned Windows
builds use the `release-unsigned` environment and may show Unknown publisher or
SmartScreen prompts; release notes say so. With signing on, every candidate requires
Windows signing, and missing credentials or failed signatures stop preparation. A
staged candidate keeps its recorded mode even if the variable changes later.

| Setting | Store as | What it does | How to get it |
| --- | --- | --- | --- |
| `WINDOWS_CERTIFICATE` | Secret | Supplies the existing Windows signing adapter with a base64 PFX/PKCS#12 certificate **and private key**. Signs the standalone backend, embedded app and NSIS installer. | Export a usable code-signing identity and its private key to password-protected PFX through your current signing provider or Windows certificate manager, **if that provider permits export**. Encode the PFX as base64. See the hardware limitation below before buying anything. A `.cer` file alone is insufficient. |
| `WINDOWS_CERTIFICATE_PASSWORD` | Secret | Opens that PFX. | The password chosen when exporting it. This is not your Windows account password. |

Set passwords through GitHub's secret editor or the interactive `gh secret set`
prompt. Base64 is an encoding, not encryption. Keep certificates with private keys
and their passwords out of this repository and Second Brain. No personal access token
is needed for uploading, attestation or publication; the workflows use the
automatically issued `GITHUB_TOKEN` and GitHub OIDC.

### Windows signing needs a provider decision

The existing adapter assumes an exportable PFX. Modern publicly trusted code-signing
keys generally cannot be obtained that way. Since June 1, 2023, industry rules require
new standard code-signing private keys to use qualifying hardware protection.
A hardware token or cloud signing key normally cannot be exported into a GitHub secret.
A self-signed PFX can test signing mechanics but does not provide public Windows trust.

For a new public Windows signing setup, I recommend evaluating **Microsoft Artifact
Signing** and its identity-validation/region eligibility, or your certificate
provider's hosted signing service. That route needs a separate integration for
**both** `scripts/sign-windows-backend.ps1` and Electron Builder's app/installer signing.
OIDC can avoid a stored cloud client password. It also changes the required settings,
so do not purchase a certificate expecting that two PFX secrets will necessarily work.
This change retains the existing PFX integration; no cloud provider or account has
been selected or configured. Without a usable existing PFX, the optional signed
Windows path remains blocked until that integration is implemented. Unsigned
preparation remains available. SignPath also needs its own integration when adopted.

The Apple Developer membership and Developer ID certificate are in place, so Mac
releases are signed. Windows signing still waits on that provider decision; unsigned
Windows packages don't block a release.

### Why these settings appeared after 0.2.5

Commit `3ed11cb8252a16f412d6586c5c39b7439f01398e`, the September 10 security refresh,
introduced mandatory Windows/macOS signing, notarization and provenance. The 0.2.5
release workflow had no signing input, so earlier unsigned releases needed none of
these credentials. Signing became optional again after the September 11 release
decision, and in November 2026 Mac signing became mandatory on its own once the Apple
Developer ID certificate existed, with Windows signing left optional.

## Dictation on Mac and Windows

Mac builds include Vosk 0.3.44, the latest published universal2 wheel, and its
`libvosk.dyld` native library. Other platforms retain Vosk 0.3.45. The app declares
microphone usage and audio-input entitlements. Before publishing a Mac candidate,
test Rem recording with microphone access granted and denied, then test Settings >
Devices microphone input. If access was denied, enable Raticode under System Settings >
Privacy & Security > Microphone and restart it. The offline speech model is
downloaded from Settings > Devices and needs an internet connection.

Mac dictation uses Apple's on-device speech recognizer through a small helper,
`frontend/native/macos/RaticodeSpeech.swift`. `npm run dist:mac` and the release
workflow's "Build macOS speech helper" step build it with
`scripts/build-macos-speech-helper.cjs --require` (needs Xcode or the Command Line
Tools) into `Contents/Resources/speech/raticode-speech`. electron-builder skips a
missing helper without failing, so `scripts/test-release-packages.py` checks that
both the zip and the dmg contain it and that `raticode-speech check` runs. The app's Info.plist declares
`NSSpeechRecognitionUsageDescription`. macOS charges the permission to
Raticode.app, so without that declaration it ends the helper. That is also why
development builds, which run inside Electron.app, fall back to the offline
model. Before publishing, dictate with speech recognition allowed and denied.
Check Settings > Devices > Speech recognition with Dictation off in System
Settings > Keyboard; the on-device language comes with Dictation. Also check
that a refusal mid-dictation hands over to the offline model.

When Mac dictation fails, Settings > Developer shows why. Look for "macOS speech
helper" at startup (present or missing), "Dictation: macOS microphone access" when
the microphone opens, the backend's "Dictation started with ..." and helper
start, ready and exit lines (with the signal and stderr when it crashes), and the
window's "Voice input:" warnings (for example, a microphone sending only zeros). The
frozen backend's OpenSSL has no certificate store on a Mac, so the offline model's
download reads the Mac's root and System keychains with `/usr/bin/security` first
(which covers company roots behind HTTPS inspection) and then the certifi bundle
shipped inside it. The log names the certificate sources when the download fails.
The offline model is downloaded only from Settings > Devices (**Download offline
model**, with progress); dictation never starts the download. **Test speech
recognition** runs the helper's `listen` once with no audio so macOS asks for the
speech permission. Before publishing a Mac candidate, also check the download on a
network that inspects HTTPS and the test button's permission prompt. No audio or
recognized words are logged.

On Windows, dictation drives the offline `System.Speech` recognizer through
Windows PowerShell 5.1 (`src/gofer/ui/system_speech.py`). It needs no
packaging step. Before publishing, check it on Windows 10 and 11, including
11 24H2. Microsoft deprecated Windows Speech Recognition, and whether a
recognizer is still installed there is unconfirmed. Also test a PC with no
speech language installed and a PC where PowerShell is restricted. Each should
show its reason under Settings > Devices.

## Candidate review and tagging

1. Bump versions before preparation, move the website to the new version
   (`npm run set-release -- X.Y.Z` in `website/`, then `npm run check`), and commit the
   release notes and code. Push to `origin/stage`. Every stage push prepares the current package version, even if you
   are not ready to publish it. This costs native build time and, when enabled, signing service use.
2. Wait for the whole **Prepare release candidate** run to finish successfully. Open
   the draft linked in **Stage exact files**' job summary. It is identified by the
   full source SHA, run ID and attempt, not by a moving `latest` artifact name.
3. Download and review those files. Automated checks run the native Electron fixtures,
   validate final updater SHA-512 hashes, execute each standalone CLI's `--version`,
   install the Windows NSIS package and start its backend, and extract the Linux
   AppImage/DEB/RPM and macOS ZIP/DMG packages and start their embedded backends.
   Each standalone CLI also starts as a headless backend (`serve --headless --port 0`)
   on a scratch data folder, with its home, config and data folders all inside that
   scratch folder, and the same binary then acts as its client: `api GET /health`,
   `rem tasks list`, an agent token that can read `/workflows` but is refused
   `/session` (exit 5), the endpoint file removed on stop (not on Windows, where the
   backend is killed), and exit 6 once the backend is gone. The results are recorded
   under `headless` in `package-tests-<platform>.json`.
   On macOS they also check the Developer ID signatures, hardened runtime, stapled
   tickets and Gatekeeper assessment of both app copies and the DMG, and Apple's
   notarization receipts are part of the draft.
   Linux DEB/RPM checks cover extraction and startup, not package-manager dependency
   resolution or maintainer scripts. Full desktop launch, clean-machine installation
   and an update from 0.2.5 remain manual review tasks. No user workflow runs in these tests.
4. Squash merge `stage` into `main`. The squash commit is new, so it has a different
   SHA from the reviewed one, but it must contain exactly the same files. Check before
   tagging; these two lines must print the same tree:

   ```bash
   git rev-parse 'REVIEWED_FULL_COMMIT_SHA^{tree}'
   git rev-parse 'origin/main^{tree}'
   ```

   They differ if `main` had commits that `stage` didn't, or if the merge resolved a
   conflict. Then bring `stage` up to date with `main`, push it, and review the new
   candidate instead.
5. Create `vX.Y.Z` at the squash commit on `main`, or at the reviewed stage SHA, then
   push that tag. For example, replace the placeholder before running:

   ```bash
   git tag -a vX.Y.Z SQUASH_FULL_COMMIT_SHA -m 'Release vX.Y.Z'
   git push origin refs/tags/vX.Y.Z
   ```

6. The publish job verifies package/tag versions, the remote tag's commit, that the
   tagged commit has the candidate's exact file tree, the manifest's GitHub
   attestation for the staged commit, the successful stage preparation run and attempt,
   the complete asset inventory and every remote SHA-256. It changes the existing
   draft's candidate name to the version tag and publishes it. It verifies the
   public asset bytes afterward. It never replaces release assets.

A draft is created with a `candidate-<SHA>-<RUN>-<ATTEMPT>` identifier;
it does not push a `v*` tag. Publication discovers ready drafts through their
verification marker and verifies their attested manifests, not their editable tag
names. A draft renamed to `untagged-*` can still be published when all checks pass.
Do not click **Publish release** in the web UI, which
would bypass the tag workflow and could publish the internal candidate identifier.
Repository maintainers with release-write access can bypass workflow policy, so
protect workflow changes and `v*` tag creation with repository rules.

## AUR packages

`packaging/aur/raticode-bin` (desktop) and `packaging/aur/raticode-cli-bin` (CLI) are
exact copies of the two AUR repositories. Both repackage release files: the desktop
`.deb`, installed under `/opt/raticode`, and `raticode-cli-linux-x64`. User-facing
install and update steps are on the website (Installation > Arch Linux).

**Checksums come only from a published release.** `scripts/bump-version.cjs` no
longer touches these folders and refuses the old `--appimage-sha256` and
`--cli-sha256` flags: a checksum typed in before the build always went stale. The
folders describe the newest published release until `scripts/aur-packages.py` moves
them, using that release's `checksums-linux.txt`, which the Linux build computes from
the packages it built and the candidate manifest attests:

```bash
gh release download vX.Y.Z -R zacharyivie/Raticode -p checksums-linux.txt -D /path/to/scratch
python3 scripts/aur-packages.py update --version X.Y.Z --checksums /path/to/scratch/checksums-linux.txt
python3 scripts/aur-packages.py check
```

`update` sets `pkgver`, resets `pkgrel` to 1 for a new version (`--pkgrel N` for a
packaging-only fix), hashes the local helper files and regenerates `.SRCINFO` in
makepkg's own format. It changes nothing unless every packaged asset is in the
checksum file. `check` fails on `SKIP` or stale checksums, a stale `.SRCINFO`, a
`replaces=` line, a placeholder maintainer or a missing `LICENSE`,
`LICENSES/0BSD.txt` or `REUSE.toml`, and
compares `.SRCINFO` with `makepkg --printsrcinfo` when makepkg is installed. The
branding check runs it too. Commit the refreshed folders to `dev` after each release.

**The Arch package must not update itself.** `raticode-bin` writes `pacman` to
`/opt/raticode/resources/package-type`. The app reads that marker, never runs
electron-updater, and its update button says to update with pacman and opens the
release notes. The `.deb` and `.rpm` builds write `deb` and `rpm` and keep updating
themselves. Removing the marker from the PKGBUILD would let the app try to replace
root-owned files.

**Automatic updates (`.github/workflows/aur.yml`).** After **Publish release**
succeeds, or when started by hand with a version, it:

1. checks the release is published, downloads its `checksums-linux.txt` and verifies
   that file's attestation came from `release-candidate.yml`;
2. renders both packages with `aur-packages.py update` and `check`;
3. in a pinned `archlinux:base-devel` container, compares `.SRCINFO` with makepkg,
   requires a clean `namcap PKGBUILD`, builds both packages from the published files
   (makepkg refuses any file whose sha256 differs) and prints `namcap` findings for
   the built packages;
4. in the `aur-publish` environment, pushes each changed package to
   `ssh://aur@aur.archlinux.org/<package>.git` on `master`, trusting only the host
   keys pinned in `packaging/aur/known_hosts`.

Step 4 runs only when an `AUR_SSH_PRIVATE_KEY` secret exists. Without it the job logs
"AUR publishing is off" and pushes nothing. To turn publishing on:

1. Make a key used only by this workflow (`ssh-keygen -t ed25519 -N '' -f aur-ci`)
   and add `aur-ci.pub` to your AUR account (My Account > SSH Public Key, one key per
   line, next to your personal key).
2. In GitHub, Settings > Environments, create `aur-publish`, add yourself as a
   required reviewer and allow only the `main` branch (both triggers run there). Add the private
   key (`aur-ci`) there as the secret `AUR_SSH_PRIVATE_KEY`, then delete the local copy.
3. Optional: repository variables `AUR_COMMIT_NAME` and `AUR_COMMIT_EMAIL` change the
   AUR commit author (default: Zack Ivie, publisheddoonk@gmail.com).

The AUR asks maintainers to keep checking automated updates by hand, so keep the
required reviewer and read the run's namcap output before approving. To rotate the
key, add the new public key to the AUR account, replace the secret, then remove the
old public key. If aur.archlinux.org changes its host keys, compare the new ones with
the fingerprints on https://aur.archlinux.org/ before updating `known_hosts`.

What has not run yet: the workflow itself, `gh attestation verify` against a real
release, and any push to the AUR. A local makepkg build and namcap of both 0.5.2
packages passed on 2026-11-10; no clean-chroot build or install was done.

## Retries and candidate lifetime

- Tagging before readiness fails without creating a release. Let preparation finish,
  then rerun the failed tag job. An old unsigned dry run cannot qualify.
- A draft is ready only when remote verification completes **and** the recorded
  preparation attempt succeeds. Failed/in-progress runs cannot authorize publication.
- Upload retries accept identical existing files and refuse mismatches. There is no
  `--clobber`. A rerun uses a distinct attempt identifier; an incomplete older draft
  remains unpublished. A new stage commit gets its own draft.
- If two successful candidates exist for the same SHA, publication refuses to choose.
  A candidate built from the tagged commit itself wins over others with the same
  files. A squash-commit tag that matches two stage candidates' files refuses too.
  Review them and delete the superseded draft through GitHub before tagging or
  rerunning publication. Never delete a published release as a retry mechanism.
- A candidate must be at most seven days old at its first publication. Re-prepare
  and review a stale candidate so its dependency audits and signing checks are fresh.
  Public-release retries still work after seven days.
- If GitHub publishes successfully but the final network response or verification
  fails, rerunning verifies the existing release without rebuilding or re-uploading.
- Retain the draft and its preparation run until publication. Draft assets are the
  durable candidate store; Actions artifact expiration does not erase them. Deleting
  the source run or provenance evidence prevents the publish gate from verifying it.
- Delete abandoned drafts through normal repository maintenance. They are not
  automatically deleted by a later stage push.

A normal candidate can coexist with later commits on stage; tag the reviewed SHA or its squash commit.
For the already pushed `v0.2.6` at the old commit, rerunning that historical workflow
will still use its historical definition. Prefer preparing the next unused version
with this flow. Reusing 0.2.6 would require an explicit decision to move/delete the
existing tag and prepare the new commit. This change does neither.

Stage preparation exposes build failures and, when enabled, signing/account/service
failures before you tag.
Publication still depends on GitHub reads, provenance verification and the final
visibility API call. Service outages can fail an attempt; safe retries and refusing
partial publication are the guarantees this design can enforce.

## References

- [Apple Developer ID certificates](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/)
- [Apple notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)
- [Apple app-specific passwords](https://support.apple.com/en-us/102654)
- [GitHub environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [Code-signing hardware requirement](https://knowledge.digicert.com/general-information/new-private-key-storage-requirement-for-standard-code-signing-certificates-november-2022)
- [Microsoft Artifact Signing](https://learn.microsoft.com/en-us/azure/artifact-signing/overview)
- [Pinned runner OS and architecture labels](https://github.com/actions/runner-images)

## Third-party notices

Install both locked dependency sets before building the backend. `raticode.spec` runs
`scripts/collect-licenses.py` in the build environment. Source validation also runs
the collector. A missing license text, dependency version mismatch, or change from
`packaging/licenses/reviewed.json` fails the build. Review upstream terms before
updating that file; do not regenerate it solely to silence a failure.

The collector includes Python runtime dependencies, PyInstaller notices, npm
production dependencies, Electron/Chromium notices and the documented Vosk and
lazy-val metadata exceptions. Certifi and tqdm source archives are downloaded
from their locked URLs and checked against their locked SHA-256 hashes.

The frozen CLI embeds this bundle. Desktop packages also expose it under
`resources/third-party-licenses`; Debian and RPM CLI packages install it under
their documentation/license directories. Standalone users can export it with
`Raticode licenses --output <new-directory>`. Final package checks inspect the embedded
inventory before accepting a backend.

`native-inventory.json` records the destination, build input and SHA-256 of every
PyInstaller native library. Review this inventory on each release platform and
retain the notices and source required by the actual native builds. Package
metadata alone cannot establish the contents of Vosk's compiled dependencies.
See `packaging/licenses/README.md` for the known components and source references.
GitHub's source archive must resolve to the same reviewed commit as the binary
candidate; keep the lockfiles and build instructions in that commit.
