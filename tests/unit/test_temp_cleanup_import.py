"""The temp sweep's module loads on every platform, because the server imports it.

On Windows ``os`` has no ``O_DIRECTORY``; reading it when the module loaded stopped the
backend from importing there (seen on the Windows release build, 2026-11-28). This runs on
every platform, removing the flag the way Windows lacks it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import gofer

SOURCE = str(Path(gofer.__file__).resolve().parents[1])
PROBE = """
import os
for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"):
    if hasattr(os, name):
        delattr(os, name)
from gofer.ui import temp_cleanup
print(temp_cleanup._FOLDER_FLAGS == os.O_RDONLY)
"""


def test_the_temp_sweep_loads_without_the_posix_folder_flags() -> None:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([SOURCE, os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
