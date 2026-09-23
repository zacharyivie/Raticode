"""Audit the complete lockfiles, including Electron and every Python platform branch."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def audit_packages(packages: list[str], evidence: Path) -> int:
    # pip-audit rejects multiple versions of one project in a requirements file.
    # Split only those collisions, without resolving away any platform branch.
    batches: list[dict[str, str]] = [{}]
    for requirement in packages:
        name = re.sub(r"[-_.]+", "-", requirement.split("==")[0]).lower()
        for batch in batches:
            if name not in batch:
                batch[name] = requirement
                break
        else:
            batches.append({name: requirement})

    output = evidence / "python-audit.json"
    output.unlink(missing_ok=True)
    dependencies: list[dict[str, Any]] = []
    failed = False
    with tempfile.TemporaryDirectory(prefix="python-audit-", dir=evidence) as temporary:
        for index, batch in enumerate(batches):
            inventory = Path(temporary) / f"requirements-{index}.txt"
            inventory.write_text("\n".join(batch.values()) + "\n", encoding="utf8")
            report = Path(temporary) / f"audit-{index}.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip_audit",
                    "--disable-pip",
                    "--no-deps",
                    "--progress-spinner",
                    "off",
                    "--format",
                    "json",
                    "--output",
                    str(report),
                    "--requirement",
                    str(inventory),
                ],
                cwd=ROOT,
                check=False,
            )
            # A tool/network failure must not leave a previous clean report behind.
            try:
                payload = json.loads(report.read_text(encoding="utf8"))
                audited = payload["dependencies"]
                actual = {f"{item['name']}=={item['version']}" for item in audited}
                complete = actual == set(batch.values()) and all(
                    "skip_reason" not in item and isinstance(item.get("vulns"), list)
                    for item in audited
                )
            except (OSError, ValueError, KeyError, TypeError):
                complete = False
            if not complete:
                print(
                    "Python dependency audit did not cover the complete inventory.", file=sys.stderr
                )
                return 1
            dependencies.extend(audited)
            failed = failed or result.returncode != 0 or any(item["vulns"] for item in audited)

    output.write_text(
        json.dumps({"dependencies": dependencies, "fixes": []}, indent=2) + "\n",
        encoding="utf8",
    )
    return int(failed)


def main() -> int:
    evidence = ROOT / "audit-evidence"
    evidence.mkdir(exist_ok=True)
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf8"))
    packages = sorted(
        {
            f"{package['name']}=={package['version']}"
            for package in lock["package"]
            if "registry" in package.get("source", {})
        }
    )
    # Keep all platform branches. Resolving this file would drop packages shipped
    # on a different OS, so pip-audit must inspect the exact inventory instead.
    inventory = evidence / "python-lock-inventory.txt"
    inventory.write_text("\n".join(packages) + "\n", encoding="utf8")
    result = audit_packages(packages, evidence)
    npm_lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf8"))
    (evidence / "npm-lock-inventory.json").write_text(
        json.dumps(
            {
                "lockfileVersion": npm_lock["lockfileVersion"],
                "packages": {
                    path: {
                        key: package[key]
                        for key in ("version", "integrity", "dev")
                        if key in package
                    }
                    for path, package in npm_lock["packages"].items()
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf8",
    )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
