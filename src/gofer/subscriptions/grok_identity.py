"""Bounded no-model Grok Build CLI family checks from public source 37949780.

Help/version fingerprints distinguish a same-named CLI, not cryptographic origin
or proven compatibility of the installed release with every ACP extension.
"""

from __future__ import annotations

import json
import threading

from gofer.utils.process import env_with_executable_on_path, run_subprocess


async def grok_build_version(
    executable: str, cancel_event: threading.Event | None = None
) -> str:
    """Require source-backed root/agent help and structured version output."""
    outputs: list[str] = []
    for arguments in (["--help"], ["agent", "--help"], ["version", "--json"]):
        if cancel_event is not None and cancel_event.is_set():
            raise ValueError("Grok identity check cancelled")
        code, stdout, _ = await run_subprocess(
            [executable, *arguments],
            timeout=10,
            max_output_bytes=128 * 1024,
            cancel_event=cancel_event,
            env=env_with_executable_on_path(executable),
        )
        if cancel_event is not None and cancel_event.is_set():
            raise ValueError("Grok identity check cancelled")
        if code:
            raise ValueError("Grok Build identity probe failed; check installed CLI/version")
        outputs.append(stdout)
    if "Grok Build TUI" not in outputs[0] or not all(
        marker in outputs[1] for marker in ("--no-leader", "--plugin-dir", "stdio")
    ):
        raise ValueError("Executable is not a supported Grok Build CLI family")
    try:
        version = json.loads(outputs[2])
    except (ValueError, RecursionError) as exc:
        raise ValueError("Grok Build returned invalid version JSON") from exc
    if (
        not isinstance(version, dict)
        or not isinstance(version.get("currentVersion"), str)
        or not version["currentVersion"].strip()
        or not isinstance(version.get("channel"), str)
        or not version["channel"].strip()
    ):
        raise ValueError("Grok Build returned incomplete version metadata")
    current_version: str = version["currentVersion"]
    return current_version
