"""Isolated real HTTP backend for the Organizations browser test; no provider CLIs."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from gofer.ui.server import create_server


async def fake_employee(**options: Any) -> AsyncGenerator[dict[str, Any], None]:
    yield {
        "type": "final",
        "message": {"body": "Verified the assigned task using a test provider."},
    }


if __name__ == "__main__":
    os.environ["RATICODE_EXPERIMENTAL_ORGANIZATIONS"] = (
        "0" if os.environ.get("RATICODE_TEST_ORGS_DISABLED") == "1" else "1"
    )
    with tempfile.TemporaryDirectory(prefix="organization-browser-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        (root / "other-project").mkdir()
        (root / "third-project").mkdir()
        server = create_server(port=0, data_dir=root, api_token="organization-browser-test")
        server.organizations.stream = fake_employee
        print(json.dumps({"port": server.server_address[1], "project": str(project)}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
