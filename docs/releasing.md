# Preparing and publishing a release

Push the final version to `main`, wait for **Prepare release candidate** to succeed,
review its draft downloads, then push the version tag at that exact commit.
Tagging publishes the already staged files. It never starts a build.

| Phase | Trigger | Result |
| --- | --- | --- |
| Validate source | Pull request, and the first job of main preparation | Locked dependency audits, Python lint/types/full tests, frontend lint/unit/browser tests, package-version agreement |
| Prepare candidate | Push to `main`, or manual **Prepare release candidate** on `main` | Windows x64, macOS arm64 and Linux x64 packages; unsigned by default, with optional Windows/macOS signing and Apple notarization; native tests and distribution backend tests |
| Stage exact files | Successful preparation in the same run | Attested manifest, complete draft release, release notes and verified remote hashes |
| Publish | Push of `vX.Y.Z` | Verify the matching successful candidate and make its existing draft public |

The old unsigned dry-run and duplicate frontend-check workflows are replaced by
`validate-source.yml` and `release-candidate.yml`. Update any required status checks
that referred to their old names. The tag workflow has no build, package upload,
signing, notarization, audit or dependency-install step.

## Unsigned releases are the default

No Apple membership, SignPath account or signing credentials are needed to prepare
or publish the current releases. Leave the repository Actions variable
`RELEASE_SIGNING` unset or set it to `false`. Unsigned jobs select the
`release-unsigned` environment, which GitHub creates automatically if absent;
it needs no secrets. Credential checks, Windows signing and Apple notarization
are skipped. Certificate auto-discovery is disabled, and configured signing values
are not passed to unsigned packaging.

All source validation, dependency audits, native runtime and distribution tests,
updater hash checks, checksums, provenance attestations and remote-file verification
still run. Main stages the tested candidate; tags publish those exact files.
The build's policy output supplies the manifest's boolean `signed` field. Signed
candidates require signature and notarization receipts. Unsigned candidates omit
those receipts and the signing check; they cannot claim a signed verification pass.
Release notes disclose the chosen mode and installation limitations.

Windows may show Unknown publisher or SmartScreen prompts. macOS may require
approval under **System Settings > Privacy & Security** after the first launch.
Unsigned Apple Silicon apps use local ad-hoc signatures so they can run; those do
not establish a verified publisher and are not Apple notarization.
Unsigned macOS apps check for releases and open the release page for manual
installation. They do not offer automatic installation. Windows and Linux retain
the existing updater behavior. Test installation and updates on clean machines
before publishing; CI backend smoke tests do not establish Gatekeeper or SmartScreen behavior.

## Optional signing setup for later

Complete the provider setup below, then set the **repository** Actions variable
`RELEASE_SIGNING` to the exact value `true`. It must be a repository variable because
the caller reads it before entering an environment. Every subsequent main candidate
will require Windows signing and macOS signing/notarization. Missing credentials
or failed signatures stop preparation; the signed path never falls back to unsigned.
A staged candidate retains its recorded mode even if this variable changes later.
Review that mode in its manifest and release notes before tagging.

Create the GitHub environment **release-signing** in
[repository Settings > Environments](https://github.com/zacharyivie/gofer-flow/settings/environments).
Restrict its deployment branches to **Selected branches and tags**, then add a
**branch** rule for `main` only. Both the credential check and native build jobs
explicitly select this environment. Pull-request source validation does not.
Required reviewers are optional; adding them means each main preparation waits
for approval before signing. Without reviewers, trusted main pushes run automatically.

Add these five **environment secrets** and three **environment variables** in that
environment's settings. Repository-level values with the same names can also be
resolved by Actions, but environment scope keeps signing access tied to trusted main.
No personal access token is needed for uploading, attestation or publication;
the workflows use the automatically issued `GITHUB_TOKEN` and GitHub OIDC.

| Setting | Store as | What it does | How to get it |
| --- | --- | --- | --- |
| `WINDOWS_CERTIFICATE` | Secret | Supplies the existing Windows signing adapter with a base64 PFX/PKCS#12 certificate **and private key**. Signs the standalone backend, embedded app and NSIS installer. | Export a usable code-signing identity and its private key to password-protected PFX through your current signing provider or Windows certificate manager, **if that provider permits export**. Encode the PFX as base64. See the hardware limitation below before buying anything. A `.cer` file alone is insufficient. |
| `WINDOWS_CERTIFICATE_PASSWORD` | Secret | Opens that PFX. | The password chosen when exporting it. This is not your Windows account password. |
| `MACOS_CERTIFICATE` | Secret | Imports a Developer ID Application identity for PyInstaller, Electron and DMG signing. | Enroll in the Apple Developer Program. The Account Holder creates a **Developer ID Application** certificate using a CSR from the signing Mac. Import the downloaded certificate on that Mac. In Keychain Access, export the identity including its private key from **My Certificates** as password-protected `.p12`, then base64-encode it. Developer ID Installer and Apple Development certificates are different certificate types. |
| `MACOS_CERTIFICATE_PASSWORD` | Secret | Opens the exported `.p12`. | Choose it during Keychain Access export. It is not your Apple account password. |
| `APPLE_APP_SPECIFIC_PASSWORD` | Secret | Authenticates Electron's and `notarytool`'s Apple notarization submissions. | Enable two-factor authentication for the authorized Apple account. At [account.apple.com](https://account.apple.com), open **Sign-In and Security > App-Specific Passwords**, then generate one for Raticode CI. |
| `MACOS_SIGNING_IDENTITY` | Variable | Selects the exact identity used to sign the backend. | On the Mac with the imported identity, run `security find-identity -v -p codesigning`. Copy the full identity, for example `Developer ID Application: Example Name (TEAMID1234)`, without the surrounding quotes. |
| `APPLE_ID` | Variable | Identifies the Apple account authorized to submit notarization for the team. | Use the Apple account email associated with the developer membership and the app-specific password above. |
| `APPLE_TEAM_ID` | Variable | Selects the Apple Developer team for notarization. | Copy the Team ID from your developer account's membership details. It must match the signing certificate's team. It is not the app bundle identifier. |

For either exported certificate, encode the file locally without printing it:

```bash
openssl base64 -A -in developer-id.p12 -out developer-id.base64
# Feed the file into the environment secret. Do not commit either file.
gh secret set MACOS_CERTIFICATE --repo zacharyivie/gofer-flow \
  --env release-signing < developer-id.base64
```

Use the same method with your permitted Windows PFX and `WINDOWS_CERTIFICATE`.
Set passwords through GitHub's secret editor or the interactive `gh secret set`
prompt. Base64 is an encoding, not encryption. Keep certificates with private keys
and their passwords out of this repository and Second Brain.

### Why these settings appeared after 0.2.5

Commit `3ed11cb8252a16f412d6586c5c39b7439f01398e`, the September 10 security refresh,
introduced mandatory Windows/macOS signing, notarization and provenance.
The 0.2.5 release workflow had no `signed_release: true` input. Earlier unsigned
releases therefore needed none of these credentials. The version bump itself did
not introduce an Apple or Microsoft requirement. GitHub can host unsigned release
files. Signing is now optional again, following the September 11 release decision.

That policy change landed without completing account/certificate setup. The main
dry run also remained unsigned, so it skipped the new requirements. The two 0.2.6
failures were empty inputs, before compilation. The read-only GitHub checks on
September 11 returned no repository secrets and no environments.

The previous workflow stored all eight values as secrets. Three identify an account
or certificate and do not authenticate anything alone; this flow reads them from
`vars` instead. The remaining five contain private key material or passwords.
Environment settings are configuration for signing, not eight new service accounts.

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

The current plan is to establish the landing page and domain, and possibly an LLC,
before setting up paid Apple membership and Windows signing. Those business steps
are not prerequisites for the unsigned release pipeline.

## Candidate review and tagging

1. Bump versions before preparation and commit the release notes and code. Push to
   `origin/main`. Every main push prepares the current package version, even if you
   are not ready to publish it. This costs native build time and, when enabled, signing service use.
2. Wait for the whole **Prepare release candidate** run to finish successfully. Open
   the draft linked in **Stage exact files**' job summary. It is identified by the
   full source SHA, run ID and attempt, not by a moving `latest` artifact name.
3. Download and review those files. Automated checks run the native Electron fixtures,
   validate final updater SHA-512 hashes, execute each standalone CLI's `--version`,
   install the Windows NSIS package and start its backend, and extract the Linux
   AppImage/DEB/RPM and macOS ZIP/DMG packages and start their embedded backends.
   Linux DEB/RPM checks cover extraction and startup, not package-manager dependency
   resolution or maintainer scripts. Full desktop launch, clean-machine installation
   and an update from 0.2.5 remain manual review tasks. No user workflow runs in these tests.
4. Create `vX.Y.Z` at the **reviewed SHA**, then push that tag. For example, replace
   both placeholders before running:

   ```bash
   git tag -a vX.Y.Z REVIEWED_FULL_COMMIT_SHA -m 'Release vX.Y.Z'
   git push origin refs/tags/vX.Y.Z
   ```

5. The publish job verifies package/tag versions, the remote tag's commit, the
   manifest's GitHub attestation, the successful main preparation run and attempt,
   the complete asset inventory and every remote SHA-256. It changes the existing
   draft's candidate name to the version tag and publishes it. It verifies the
   public asset bytes afterward. It never replaces release assets.

A draft's `candidate-<SHA>-<RUN>-<ATTEMPT>` identifier reserves an unpublished release;
it does not push a `v*` tag. Do not click **Publish release** in the web UI, which
would bypass the tag workflow and could publish the internal candidate identifier.
Repository maintainers with release-write access can bypass workflow policy, so
protect workflow changes and `v*` tag creation with repository rules.

## Retries and candidate lifetime

- Tagging before readiness fails without creating a release. Let preparation finish,
  then rerun the failed tag job. An old unsigned dry run cannot qualify.
- A draft is ready only when remote verification completes **and** the recorded
  preparation attempt succeeds. Failed/in-progress runs cannot authorize publication.
- Upload retries accept identical existing files and refuse mismatches. There is no
  `--clobber`. A rerun uses a distinct attempt identifier; an incomplete older draft
  remains unpublished. A new main commit gets its own draft.
- If two successful candidates exist for the same SHA, publication refuses to choose.
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
  automatically deleted by a later main push.

A normal candidate can coexist with later commits on main; tag the reviewed SHA.
For the already pushed `v0.2.6` at the old commit, rerunning that historical workflow
will still use its historical definition. Prefer preparing the next unused version
with this flow. Reusing 0.2.6 would require an explicit decision to move/delete the
existing tag and prepare the new commit. This change does neither.

Main preparation exposes build failures and, when enabled, signing/account/service
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

Install both locked dependency sets before building the backend. `gof.spec` runs
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
`gof licenses --output <new-directory>`. Final package checks inspect the embedded
inventory before accepting a backend.

`native-inventory.json` records the destination, build input and SHA-256 of every
PyInstaller native library. Review this inventory on each release platform and
retain the notices and source required by the actual native builds. Package
metadata alone cannot establish the contents of Vosk's compiled dependencies.
See `packaging/licenses/README.md` for the known components and source references.
GitHub's source archive must resolve to the same reviewed commit as the binary
candidate; keep the lockfiles and build instructions in that commit.
