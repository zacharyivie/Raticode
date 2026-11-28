"""Wall time of a workflow run whose count loop runs N bash-command steps.

Runs a Loop node with ``source: {"type": "count", "count": N}`` feeding one
``bash-command`` step that runs ``echo hi``, through the same ``run_rattish_file``
the app and CLI use, against a throwaway data folder. Every step really starts a shell.
Prints one JSON object: the step count, the wall time, the node runs the saved record
lists and its status.

    PYTHONPATH=src .venv/bin/python benchmarks/rattish_loop_run.py [N]
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import anyio

from gofer.rattish.run_service import run_rattish_file

STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 1000

SOURCE = """Rattish: 1
Workflow:
  name: Loop benchmark
Node loop:
  type: loop
  source: {{"type": "count", "count": {steps}}}
  to: say
Node say:
  type: bash-command
  command: echo hi
  needs: loop
"""


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rattish-loop-") as folder:
        root = Path(folder)
        source = root / "workflow.rattish"
        source.write_text(SOURCE.format(steps=STEPS), encoding="utf-8")
        start = time.perf_counter()
        result = await run_rattish_file(source, data_dir=root / "data")
        seconds = time.perf_counter() - start
        saved = json.loads(result.path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "steps": STEPS,
                    "seconds": round(seconds, 2),
                    "status": saved["status"],
                    "nodeRuns": len(saved["runs"]),
                    "recordBytes": result.path.stat().st_size,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    anyio.run(main)
