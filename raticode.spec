# -*- mode: python ; coding: utf-8 -*-

import os
import runpy
from pathlib import Path

from importlib.util import find_spec

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


block_cipher = None

# Generate from this build environment before freezing. Fail on missing notices.
license_collector = runpy.run_path(str(Path(SPECPATH) / "scripts/collect-licenses.py"))
license_collector["collect"](Path(SPECPATH) / "dist/third-party-licenses")
datas = [("dist/third-party-licenses", "third-party-licenses")]
datas += copy_metadata("raticode")
datas += collect_data_files("tzdata")
for package in ("fastembed", "onnxruntime", "tokenizers", "huggingface_hub", "pypdf", "bibtexparser"):
    datas += collect_data_files(package)
if find_spec("sherpa_onnx") is None:
    raise RuntimeError("sherpa-onnx is required for offline dictation in release builds")
datas += [
    ("src/gofer/devices/protocol", "gofer/devices/protocol"),
    ("rattish/contracts", "gofer/rattish/assets/contracts"),
    ("rattish/providers", "gofer/rattish/assets/providers"),
    ("rattish/schemas", "gofer/rattish/assets/schemas"),
    ("rattish/spec", "gofer/rattish/assets/docs"),
    ("rattish/starters", "gofer/rattish/assets/starters"),
    ("skills/raticode-workflow-builder", "gofer/rattish/assets/assistant-skill"),
]

hiddenimports = []
hiddenimports += collect_submodules("apscheduler")
hiddenimports += collect_submodules("gofer")
hiddenimports += collect_submodules("segno")
hiddenimports += collect_submodules("python_calamine")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("typer")
hiddenimports += collect_submodules("watchdog.observers")
hiddenimports += collect_submodules("sherpa_onnx")
for package in ("fastembed", "onnxruntime", "tokenizers", "pypdf", "bibtexparser"):
    hiddenimports += collect_submodules(package)

# sherpa-onnx keeps its native module and its own ONNX Runtime in sherpa_onnx/lib, and
# the Linux wheel adds libasound in sherpa_onnx.libs. The module finds them relative
# to itself (RPATH $ORIGIN on Linux and macOS, its own folder on Windows), so they keep
# their folders. Windows' copy is also named onnxruntime.dll, like Raticode's own
# ONNX Runtime; gofer.ui.offline_speech loads sherpa-onnx's copy by full path first.
# Only the module and its ONNX Runtime are needed; the wheel's C and C++ API libraries
# aren't used from Python. A throwaway Linux build of exactly this collection imported
# sherpa-onnx and decoded audio, with the libraries at 12.1 MiB compressed.
binaries = collect_dynamic_libs(
    "sherpa_onnx", search_patterns=["_sherpa_onnx*", "*onnxruntime*"]
)
_speech_names = [Path(source).name for source, _ in binaries]
if not any(name.startswith("_sherpa_onnx") for name in _speech_names) or not any(
    "onnxruntime" in name for name in _speech_names
):
    raise RuntimeError("The sherpa-onnx native speech library is missing")
for package in ("onnxruntime", "numpy", "tokenizers"):
    binaries += collect_dynamic_libs(package)

a = Analysis(
    ["packaging/pyinstaller/raticode_entry.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Preserve the concrete native payload for each platform's release review.
import hashlib
import json
native_inventory = [
    {"destination": destination, "source": source,
     "sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest()}
    for destination, source, kind in a.binaries if Path(source).is_file()
]
(Path(SPECPATH) / "dist/third-party-licenses/native-inventory.json").write_text(
    json.dumps(native_inventory, indent=2) + "\n", encoding="utf-8"
)
a.datas.append(("third-party-licenses/native-inventory.json",
                str(Path(SPECPATH) / "dist/third-party-licenses/native-inventory.json"), "DATA"))
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Raticode",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    # Signed Mac releases set both; the hardened runtime needs the entitlements that let
    # Python's native libraries load.
    codesign_identity=os.environ.get("GOFER_CODESIGN_IDENTITY") or None,
    entitlements_file=(
        os.path.join(SPECPATH, os.environ["GOFER_CODESIGN_ENTITLEMENTS"])
        if os.environ.get("GOFER_CODESIGN_ENTITLEMENTS")
        else None
    ),
)
