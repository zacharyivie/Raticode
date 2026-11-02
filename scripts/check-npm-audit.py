"""Fail the release on npm advisories, except reviewed ones in build tools only.

Each exception names one advisory and one package, carries a reason and an
expiry date, and covers only packages the lockfile marks as development-only,
which are never packaged into the app. Any other advisory, a shipped package,
an expired exception or an unreadable audit report fails the check.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ADVISORY_ID = re.compile(r"GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}")


def advisories(report: dict[str, Any]) -> list[tuple[str, str]]:
    """Return (advisory, package) for every direct advisory."""
    found = []
    for entry in report["vulnerabilities"].values():
        for via in entry["via"]:
            if not isinstance(via, dict):
                continue
            match = ADVISORY_ID.search(str(via.get("url", "")))
            advisory = match.group(0) if match else f"npm-{via.get('source')}"
            found.append((advisory, str(via["name"])))
    return found


def check(
    report: dict[str, Any],
    lock: dict[str, Any],
    exceptions: list[dict[str, Any]],
    today: date,
) -> tuple[list[str], list[str]]:
    """Return (failures, notes) for one audit report."""
    if not isinstance(report.get("vulnerabilities"), dict) or "metadata" not in report:
        return ["npm audit did not produce a complete report."], []
    packages = lock["packages"]
    allowed = {(item["advisory"], item["package"]): item for item in exceptions}
    used: set[tuple[str, str]] = set()
    failures = []
    for advisory, package in advisories(report):
        exception = allowed.get((advisory, package))
        if exception is None:
            failures.append(f"{advisory} in {package} has no reviewed exception.")
            continue
        used.add((advisory, package))
        if date.fromisoformat(exception["expires"]) < today:
            failures.append(
                f"The exception for {advisory} in {package} expired on {exception['expires']}."
            )
    # Packages pulled in only because they depend on an advisory's package must
    # also stay out of the app, or the exception would cover shipped code.
    for name, entry in report["vulnerabilities"].items():
        shipped = [node for node in entry["nodes"] if not packages.get(node, {}).get("dev")]
        if shipped:
            failures.append(f"{name} is shipped with the app: {', '.join(shipped)}.")
    notes = [
        f"The exception for {key[0]} in {key[1]} is no longer needed; remove it."
        for key in allowed
        if key not in used
    ]
    return failures, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="npm audit --json output")
    parser.add_argument("--lock", type=Path, default=ROOT / "frontend/package-lock.json")
    parser.add_argument(
        "--exceptions", type=Path, default=ROOT / "frontend/npm-audit-exceptions.json"
    )
    parser.add_argument("--today", type=date.fromisoformat, default=datetime.now(UTC).date())
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf8"))
    except (OSError, ValueError):
        report = {}
    lock = json.loads(args.lock.read_text(encoding="utf8"))
    exceptions = json.loads(args.exceptions.read_text(encoding="utf8"))["exceptions"]
    failures, notes = check(report, lock, exceptions, args.today)
    for note in notes:
        print(note)
    for failure in failures:
        print(failure, file=sys.stderr)
    if not failures:
        print("npm audit: no advisories outside the reviewed build-tool exceptions.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
