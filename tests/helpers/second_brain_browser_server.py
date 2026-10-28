"""Real isolated Second Brain backend for desktop tests, with a deterministic provider."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from gofer.core.provider_profiles import ProviderProfile, save_provider_profiles
from gofer.ui.server import create_server


def research(prompt: str, provider: dict[str, Any], cancel: Any) -> dict[str, Any]:
    sources = json.loads(prompt.split("Evidence:\n")[1])
    if "Compare only these sources" in prompt:
        return {
            "rows": [
                {
                    "aspect": "Observation",
                    "cells": {
                        source["docId"]: {
                            "text": source["text"],
                            "evidence": source["id"],
                            "quote": source["text"],
                        }
                        for source in sources
                    },
                }
            ],
            "conflicts": [],
        }
    source = sources[-1]
    return {
        "title": "Desktop verified report",
        "claims": [
            {
                "section": "Findings",
                "text": "Evidence was read from the selected files.",
                "support": "quoted",
                "citations": [{"evidence": source["id"], "quote": source["text"]}],
            }
        ],
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="second-brain-browser-") as directory:
        root = Path(directory)
        brain = root / "vault"
        brain.mkdir()
        (brain / "evidence.md").write_text(
            "---\nauthor: Ada\ntags: [research]\n---\n# Evidence journal\n\n"
            "The source records exact observations.\n\n"
            "A separate paragraph preserves additional context."
        )
        (brain / "reading.html").write_text(
            "<!doctype html><html><head><title>Original reading</title><style>"
            "body{color:#035c67}</style></head><body><h1>Original reading</h1>"
            "<p>A second source records another observation.</p></body></html>"
        )
        large_brain = root / "large-vault"
        large_brain.mkdir()
        for index in range(347):
            related = index < 15
            (large_brain / f"{'related' if related else 'unrelated'}-{index:03}.md").write_text(
                "---\nauthor: Rem\n---\n"
                + (
                    f"# Raticode evidence {index}\n\nRaticode preserves exact observations."
                    if related
                    else f"# Cooking notes {index}\n\nFresh vegetables and spices."
                )
            )
        save_provider_profiles(
            {
                "local-research": ProviderProfile(
                    name="local-research",
                    subscription="openai_api",
                    model="local-model",
                    api_base_url="http://localhost:9000/v1",
                )
            },
            root,
        )
        server = create_server(port=0, data_dir=root, api_token="second-brain-browser-test")
        server.second_brains.generate = research
        print(
            json.dumps(
                {
                    "port": server.server_address[1],
                    "root": str(brain),
                    "largeRoot": str(large_brain),
                    "export": str(root / "export.zip"),
                    "import": str(root / "imported"),
                }
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
