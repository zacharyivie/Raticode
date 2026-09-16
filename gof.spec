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
datas += copy_metadata("gofer-flow")
datas += collect_data_files("openpyxl")
datas += collect_data_files("tzdata")
if find_spec("vosk") is not None:
    datas += collect_data_files("vosk")
datas += [
    ("rattish/contracts", "gofer/rattish/assets/contracts"),
    ("rattish/providers", "gofer/rattish/assets/providers"),
    ("rattish/schemas", "gofer/rattish/assets/schemas"),
    ("rattish/spec", "gofer/rattish/assets/docs"),
    ("skills/gofer-flow-workflow-builder", "gofer/rattish/assets/assistant-skill"),
]

hiddenimports = []
hiddenimports += collect_submodules("apscheduler")
hiddenimports += collect_submodules("gofer")
hiddenimports += collect_submodules("openpyxl")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_settings")
hiddenimports += collect_submodules("sqlalchemy")
hiddenimports += collect_submodules("typer")
hiddenimports += collect_submodules("watchdog.observers")
if find_spec("vosk") is not None:
    hiddenimports += collect_submodules("vosk")

binaries = []
if find_spec("vosk") is not None:
    binaries += collect_dynamic_libs("vosk")

a = Analysis(
    ["packaging/pyinstaller/gof_entry.py"],
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
    name="gof",
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
    codesign_identity=os.environ.get("GOFER_CODESIGN_IDENTITY"),
    entitlements_file=None,
)
