# Distribution notices and source

The build collects license, copyright and NOTICE files from the installed Python
runtime dependency closure, PyInstaller runtime tooling, npm production packages,
and Electron. Installed versions must match uv.lock and package-lock.json.
Optional dependencies absent on the build platform are not included. The bundle
includes the Python runtime license and Electron's Chromium notices, plus vendored
node-pty notices. Application code remains AGPL-3.0-only; dependencies retain their
own terms. inventory.json records the package versions included in this build.
reviewed.json pins reviewed versions and license declarations. New or changed
declarations require updating that review after reading the upstream terms; a
README alone does not satisfy the collector.

Standalone CLI users can export the embedded bundle with
`gof licenses --output ./third-party-licenses`. The destination must not exist.
Desktop installers also carry it in their resources/third-party-licenses folder.

## Reviewed metadata gaps

* Vosk 0.3.45: COPYING is copied verbatim from
  https://raw.githubusercontent.com/alphacep/vosk-api/v0.3.45/COPYING.
  Its wheel omits this Apache-2.0 license.
* lazy-val 1.0.5: npm metadata pins source commit
  b69ad4119f1b19bdab13c61ee2fcc88d46b89071 in develar/lazy-val.
  That commit and the npm package declare MIT but contain no license file.
  The supplied notice records the author and declaration with standard MIT terms
  from SPDX license-list-data v3.27.0. It is not represented as an upstream file.
* loguru 0.7.3: its wheel and sdist omit the MIT license. The supplied notice is
  copied verbatim from https://raw.githubusercontent.com/Delgan/loguru/0.7.3/LICENSE.
* flatbuffers 25.12.19: its wheel omits the Apache-2.0 license and it has no sdist.
  The supplied notice is copied verbatim from
  https://raw.githubusercontent.com/google/flatbuffers/v25.12.19/LICENSE.
* tokenizers 0.23.2: its wheel omits the Apache-2.0 license. The supplied notice is
  copied from `tokenizers/LICENSE` in the locked sdist after checking its uv.lock hash.
* py-rust-stemmers 0.1.8 declares no license metadata. Its wheel's LICENSE is MIT.
* bibtexparser 1.4.4 is dual-licensed "LGPLv3 or BSD"; Raticode uses it under BSD.
* onnxruntime ships its third-party notices in ThirdPartyNotices.txt; the collector
  copies it alongside the onnxruntime license.
* The local lodash.isequal adapter is application code and uses the application
  license. Lodash itself has its own MIT notice in the bundle.
* openpyxl and et-xmlfile supply their complete licenses in wheel metadata;
  the collector preserves those texts.

## Source and native release review

Application source and build instructions: https://github.com/zacharyivie/Taskurotta.
Use the release's exact tag and commit, with docs/releasing.md and the lockfiles.
The bundle includes exact source distributions for the MPL components certifi
and tqdm in sources/, downloaded using the URLs in uv.lock and verified against
its SHA-256 hashes. source-inventory.json records the other Python source archive
locations. Preserve their MPL terms even when distributing the application under AGPL.

For offline builds, set `GOFER_LICENSE_SOURCE_CACHE` to a directory containing
the source archives named exactly `name-version.tar.gz`, such as
`certifi-2026.7.22.tar.gz`. Keep this cache outside the generated notices output,
which the collector clears before each build. Cached archives must match the
SHA-256 hashes in `uv.lock`; a mismatch fails the build. Missing archives are
downloaded from the locked HTTPS URLs and checked against the same hashes.
Using the cache does not skip source inclusion or license review checks.

The generated package inventory does not prove the provenance of every native
library. Before publishing a candidate, inspect the PyInstaller native inventory
and record the actual platform libraries, licenses and source locations. In
particular, libvosk embeds Kaldi, OpenFST, OpenBLAS and CLAPACK; platform Python
builds may carry OpenSSL, libffi, zlib and GCC runtime libraries. Preserve the
licenses and required source for those exact builds. Electron's bundled Chromium
notice file must remain in the desktop distribution.

The speech model is downloaded separately, not shipped in this bundle.
vosk-model-en-us-0.22-lgraph is listed as Apache-2.0 by the upstream model catalog.

Mac builds use Vosk 0.3.44, the last published universal2 wheel. Its metadata
classifies the project as Apache Software License but declares License: UNKNOWN,
as does 0.3.45. The supplied vosk-0.3.44-COPYING duplicates the Apache-2.0
text from the v0.3.45 source referenced above; upstream has no v0.3.44 Git tag.
