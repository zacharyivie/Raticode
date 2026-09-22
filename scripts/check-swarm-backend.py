"""Exhaustive deterministic pytest phases, bound to one checkout and revision."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports-swarm" / "backend"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity():
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    files = {
        name: digest(ROOT / name) for name in sorted(set(names)) if name and (ROOT / name).is_file()
    }
    return {
        "revision": revision,
        "root": str(ROOT),
        "tree": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
    }


class Capture:
    def __init__(self):
        self.nodes = []
        self.reports = []

    def pytest_collection_finish(self, session):
        self.nodes = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report):
        self.reports.append(
            {"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome}
        )


def write(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def main():
    phase = sys.argv[1]
    OUT.mkdir(parents=True, exist_ok=True)
    current = identity()
    state_path = OUT / "inventory.json"
    if phase == "backend-reconcile":
        state = json.loads(state_path.read_text())
        assert state["identity"] == current, "Stale inventory"
        seen = []
        skipped = []
        for number in range(1, 5):
            part = json.loads((OUT / f"phase-{number}.json").read_text())
            assert part["identity"] == current and part["run"] == state["run"], "Stale phase"
            assert part["exit_code"] == 0
            assert sorted(part["nodes"]) == state["nodes"][number - 1 :: 4]
            xml = OUT / f"phase-{number}.xml"
            assert digest(xml) == part["junit_sha256"]
            cases = ET.parse(xml).findall(".//testcase")
            assert len(cases) == len(part["nodes"]), "JUnit count mismatch"
            assert not ET.parse(xml).findall(".//failure") and not ET.parse(xml).findall(".//error")
            reports = part["reports"]
            assert not any(r["outcome"] == "failed" for r in reports)
            for node in part["nodes"]:
                terminal = [
                    r
                    for r in reports
                    if r["nodeid"] == node
                    and (
                        r["when"] == "call" or (r["when"] == "setup" and r["outcome"] == "skipped")
                    )
                ]
                assert len(terminal) == 1, f"Missing/duplicate result: {node}"
                if terminal[0]["outcome"] == "skipped":
                    skipped.append(node)
            assert set(r["nodeid"] for r in reports) == set(part["nodes"])
            seen.extend(part["nodes"])
        assert len(seen) == len(set(seen)) and sorted(seen) == state["nodes"]
        result = {
            "identity": current,
            "run": state["run"],
            "total": len(seen),
            "passed": len(seen) - len(skipped),
            "skipped": skipped,
        }
        write(OUT / "reconciled.json", result)
        print(json.dumps(result, indent=2))
        return 0
    number = int(phase.rsplit("-", 1)[1])
    collector = Capture()
    assert pytest.main(["--collect-only", "-q"], plugins=[collector]) == 0
    nodes = sorted(collector.nodes)
    assert nodes and len(nodes) == len(set(nodes))
    if number == 1:
        state = {"identity": current, "run": str(uuid.uuid4()), "nodes": nodes}
        write(state_path, state)
        (OUT / "reconciled.json").unlink(missing_ok=True)
    else:
        state = json.loads(state_path.read_text())
        assert state["identity"] == current and state["nodes"] == nodes, "Inventory changed"
    selection = nodes[number - 1 :: 4]
    selection_path = OUT / f"selection-{number}.json"
    write(selection_path, selection)
    # A fresh pytest process avoids repeated plugin initialization and collection state.
    started = time.monotonic()
    code = subprocess.call([sys.executable, __file__, "run", str(number)], cwd=ROOT)
    part_path = OUT / f"phase-{number}.json"
    part = json.loads(part_path.read_text())
    assert identity() == current, "Checkout changed during tests"
    part.update(
        identity=current,
        run=state["run"],
        exit_code=code,
        seconds=time.monotonic() - started,
        junit_sha256=digest(OUT / f"phase-{number}.xml"),
    )
    write(part_path, part)
    return code


if __name__ == "__main__":
    os.chdir(ROOT)
    if sys.argv[1] == "run":
        number = int(sys.argv[2])
        nodes = json.loads((OUT / f"selection-{number}.json").read_text())
        capture = Capture()
        code = pytest.main(
            ["-q", f"--junitxml={OUT / f'phase-{number}.xml'}", *nodes], plugins=[capture]
        )
        write(OUT / f"phase-{number}.json", {"nodes": capture.nodes, "reports": capture.reports})
        sys.exit(code)
    sys.exit(main())
