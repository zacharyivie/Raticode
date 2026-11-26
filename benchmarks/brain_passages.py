"""How much the Second Brain view downloads when its library changes.

Indexes a copy of a folder of notes in a throwaway data folder, then times what the
desktop's Second Brain view asks for: the library snapshot after a change, and the
passages it fetches for changed documents (``passages``, when the library has it).
Point it at a copy, never at a brain you use: opening a brain writes its metadata
into the folder, and the probe edits files to give documents stored history.

    PYTHONPATH=src .venv/bin/python benchmarks/brain_passages.py COPY_OF_A_BRAIN

Client parse times come from Node's JSON.parse when ``node`` is on PATH.
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from gofer.ui.brain_library import BrainLibrary

RUNS = 7
EDITED = 40


def wait(library: BrainLibrary) -> None:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if all(task["state"] != "running" for task in library.snapshot()["tasks"]):
            return
        time.sleep(0.2)
    raise SystemExit("Indexing did not finish in 15 minutes")


def timed(call: Any) -> tuple[float, Any]:
    samples = []
    result = None
    for _ in range(RUNS):
        start = time.perf_counter()
        result = call()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), result


def node_parse_ms(text: str, folder: Path) -> float | None:
    node = shutil.which("node")
    if node is None:
        return None
    path = folder / "payload.json"
    path.write_text(text, encoding="utf-8")
    script = (
        "const fs=require('fs');const text=fs.readFileSync(process.argv[1],'utf8');"
        "const s=[];for(let i=0;i<7;i++){const t=performance.now();JSON.parse(text);"
        "s.push(performance.now()-t);}s.sort((a,b)=>a-b);console.log(s[3]);"
    )
    out = subprocess.run([node, "-e", script, str(path)], capture_output=True, text=True)
    path.unlink()
    return float(out.stdout.strip()) if out.returncode == 0 else None


def measure(label: str, payload_call: Any, folder: Path) -> dict[str, Any]:
    server_ms, payload = timed(payload_call)
    text = json.dumps(payload)
    encode_ms, _ = timed(lambda: json.dumps(payload))
    return {
        "what": label,
        "serverMs": round(server_ms, 1),
        "encodeMs": round(encode_ms, 1),
        "bytes": len(text.encode()),
        "clientParseMs": node_parse_ms(text, folder),
    }


def main() -> None:
    source = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="brain-passages-") as scratch:
        folder = Path(scratch)
        root = folder / "brain"
        shutil.copytree(source, root)
        library = BrainLibrary(folder / "app", start=False)
        try:
            started = time.perf_counter()
            brain = library.open({"root": str(root)})
            wait(library)
            indexed_s = time.perf_counter() - started
            # Edit some files so their documents keep a stored version.
            docs = sorted(library.entities("documents", brain["id"]), key=lambda d: d["path"])
            for doc in docs[:EDITED]:
                path = root / doc["path"]
                path.write_text(path.read_text(encoding="utf-8") + "\n\nEdited.\n", "utf-8")
            library.scan(brain["id"])
            docs = library.entities("documents", brain["id"])
            ids = [doc["id"] for doc in docs]
            rows: list[dict[str, Any]] = [
                measure("snapshot (poll after a change)", library.snapshot, folder)
            ]
            try:
                library.request("passages", {"ids": []})
                has_passages = True
            except ValueError:  # a library from before the passages action
                has_passages = False
            if has_passages:
                rows.append(
                    measure(
                        "passages, every document (first load)",
                        lambda: library.request("passages", {"ids": ids}),
                        folder,
                    )
                )
                rows.append(
                    measure(
                        "passages, one changed document",
                        lambda: library.request("passages", {"ids": ids[:1]}),
                        folder,
                    )
                )
            print(
                json.dumps(
                    {
                        "documents": len(docs),
                        "passages": sum(len(doc["blocks"]) for doc in docs),
                        "storedVersions": sum(len(doc["versions"]) for doc in docs),
                        "indexSeconds": round(indexed_s, 1),
                        "rows": rows,
                    },
                    indent=2,
                )
            )
        finally:
            library.close()


if __name__ == "__main__":
    main()
