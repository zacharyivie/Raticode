"""Related-meaning search never sends ONNX Runtime or Hugging Face usage telemetry."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src"

# Runs in a fresh interpreter. A finder placed ahead of the normal ones records the two
# switches at the moment anything first imports onnxruntime, then the backend (or CLI)
# loads, then onnxruntime is imported the way related-meaning search loads it.
CHILD = """
import importlib, json, os, sys

seen = {}

class Watch:
    def find_spec(self, name, path=None, target=None):
        if name == "onnxruntime" and not seen:
            seen.update(
                ort=os.environ.get("ORT_DISABLE_TELEMETRY"),
                hf=os.environ.get("HF_HUB_DISABLE_TELEMETRY"),
            )
        return None

sys.meta_path.insert(0, Watch())
importlib.import_module(sys.argv[1])
importlib.import_module("fastembed")
print(json.dumps(seen))
"""


@pytest.mark.parametrize("startup", ["gofer.ui.server", "gofer.cli.main"])
def test_telemetry_switches_are_on_before_onnxruntime_loads(startup: str) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ORT_DISABLE_TELEMETRY", "HF_HUB_DISABLE_TELEMETRY"}
    }
    env.update(
        PYTHONPATH=str(SOURCE),
        ORT_DISABLE_TELEMETRY="0",
        HF_HUB_DISABLE_TELEMETRY="0",
    )
    result = subprocess.run(
        [sys.executable, "-c", CHILD, startup],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen == {"ort": "1", "hf": "1"}
