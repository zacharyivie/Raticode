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
`Raticode licenses --output ./third-party-licenses`. The destination must not exist.
Desktop installers also carry it in their resources/third-party-licenses folder.

## Reviewed metadata gaps

* sherpa-onnx-core 2.0.1 declares `License: Apache-2.0` but its wheel has no license
  file. The supplied notice is the project's LICENSE, copied verbatim from
  https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/v2.0.1/LICENSE (identical to
  the LICENSE in the sherpa-onnx 2.0.1 wheel).
* sherpa-onnx 2.0.1 ships its Apache-2.0 LICENSE. Its Linux wheel also bundles
  alsa-lib 1.2.12 (`sherpa_onnx.libs/libasound-*.so.2.0.0`, LGPL-2.1-or-later), which
  the native module links dynamically. The supplied sherpa-onnx-2.0.1-LICENSE holds
  alsa-lib's COPYING, copied verbatim from its v1.2.12 tag. It stays a separate shared
  library inside the backend, and Raticode's source and build instructions are public,
  so it can be replaced.
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
* bibtexparser 2.1.0 declares `License-Expression: MIT` and ships its MIT LICENSE.
  Version 1.x was dual-licensed "LGPLv3 or BSD".
* onnxruntime ships its third-party notices in ThirdPartyNotices.txt; the collector
  copies it alongside the onnxruntime license.
* The local lodash.isequal adapter is application code and uses the application
  license. Lodash itself has its own MIT notice in the bundle.
* python-calamine 0.9.1 (the .xlsx reader) ships its MIT license in wheel metadata.
  Its native module statically links Rust crates under permissive licenses (MIT,
  Apache-2.0, BSD-3-Clause, Zlib, Unicode-3.0, Unlicense), listed in the CycloneDX
  SBOM in the wheel's dist-info `sboms/` folder. The collector copies wheel SBOMs
  next to each package's license as `sbom-<name>`.

## Source and native release review

Application source and build instructions: https://github.com/zacharyivie/Raticode.
Website and documentation: https://raticode.dev/.
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
particular, sherpa-onnx-core bundles its own prebuilt ONNX Runtime 1.28.2 (MIT,
Microsoft; the same notices as the onnxruntime package) and sherpa-onnx's Linux wheel
bundles alsa-lib (above); platform Python
builds may carry OpenSSL, libffi, zlib and GCC runtime libraries. Preserve the
licenses and required source for those exact builds. Electron's bundled Chromium
notice file must remain in the desktop distribution.

## Offline speech model

The speech model is downloaded separately, only when the user clicks Download offline
model, and is not shipped in this bundle or any installer. It is NVIDIA's Nemotron 3.5
ASR Streaming 0.6B (nvidia/nemotron-3.5-asr-streaming-0.6b, int8 ONNX export by
sherpa-onnx), from https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models,
under the OpenMDW License Agreement, version 1.1. openmdw-1.1.txt is a copy of that
license taken from https://openmdw.ai/license/1-1/ on 2026-11-16, for the record of what
was reviewed. It grants use, modification and distribution without restriction; anyone
who distributes the model must keep a copy of the license and its notices of origin, and
rights end for anyone who sues claiming the model infringes a patent or copyright. The
upstream archive carries neither the license nor a notice, so when the model is
installed Raticode writes NOTICE.txt beside it naming the model, NVIDIA, the sherpa-onnx
export and the license with its URL. If Raticode ever bundles or re-hosts the model,
ship openmdw-1.1.txt and that notice with each copy.
