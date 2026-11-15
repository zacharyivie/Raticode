"""Organization read costs when nothing changed, using synthetic organizations only.

Times the Organizations tab's unchanged read, one paused organization's dispatch pass,
the Kanban board, the Auto mode review check, Rem's organization requests and a
read-only open of the usage ledger. Each figure is the median of repeated warm calls.
"""

import json
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from gofer.core.usage_ledger import usage_db
from gofer.ui.organizations import OrganizationManager
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_store import RemHomeStore
from gofer.ui.rem_kanban import RemKanban

ORGANIZATIONS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
TASKS = int(sys.argv[2]) if len(sys.argv) > 2 else 300


async def no_stream(*_args, **_kwargs):  # pragma: no cover - never called
    yield {}


def median_ms(function, repeat):
    function()
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        function()
        samples.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(samples), 3)


def build(root: Path) -> tuple[OrganizationManager, list[dict]]:
    data = root / "data"
    manager = OrganizationManager(data, stream=no_stream, start_runtime=False)
    organizations = []
    for index in range(ORGANIZATIONS):
        project = root / f"project-{index}"
        project.mkdir()
        tasks = [
            {
                "title": f"Ticket {n}",
                "description": "Synthetic ticket description. " * 20,
                "assignee": "builder",
                "status": "done" if n % 3 == 0 else "todo",
                "evidence": "Shipped" if n % 3 == 0 else "",
                "comments": [
                    {"actor": "builder", "at": "2026-11-14T00:00:00+00:00", "body": "Note " * 30}
                    for _ in range(4)
                ],
            }
            for n in range(TASKS)
        ]
        config = {
            "name": f"Studio {index}",
            "employees": [
                {"id": "lead", "name": "Avery", "role": "Coordinate"},
                {"id": "builder", "name": "Mira", "reportsTo": "lead", "role": "Build"},
            ],
        }
        org = manager.store.create(str(project), config, "user", "Benchmark", tasks)

        def attempts(current: dict) -> dict:
            current["runtime"]["attempts"] = {
                f"run-{index}-{n}": {
                    "id": f"run-{index}-{n}",
                    "taskId": task["id"],
                    "status": "completed",
                    "finishedAt": "2026-11-14T00:00:00+00:00",
                }
                for n, task in enumerate(current["runtime"]["tasks"])
            }
            return {"source": "benchmark"}

        organizations.append(
            manager.store.mutate(str(project), org["id"], "user", "seed", attempts)
        )
    with usage_db(data) as db:
        db.executemany(
            "INSERT INTO usage_invocations (id, provider, observed_at, status, tokens, run_id) "
            "VALUES (?, 'claude_code', '2026-11-14T00:00:00+00:00', 'completed', ?, ?)",
            [
                (
                    uuid.uuid4().hex,
                    json.dumps({"total_tokens": 100}),
                    f"run-{i % ORGANIZATIONS}-{i}",
                )
                for i in range(ORGANIZATIONS * TASKS * 3)
            ],
        )
    return manager, organizations


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="raticode-org-reads-") as directory:
        root = Path(directory)
        manager, organizations = build(root)
        org = organizations[0]
        project = org["projectRoot"]
        version = manager.call(project, "user", {"action": "read", "organizationId": org["id"]})[
            "version"
        ]
        kanban = RemKanban(RemHomeStore(root / "data"), organizations=lambda: manager)
        home = SimpleNamespace(organizations=manager)
        manager.auto_review = lambda *_args: None

        def auto_reviews() -> None:
            manager._next_auto = 0.0
            manager._auto_reviews()

        def usage_open() -> None:
            with usage_db(root / "data") as db:
                db.execute("SELECT 1").fetchone()

        results = {
            "organizations": ORGANIZATIONS,
            "tasks_per_organization": TASKS,
            "read_unchanged_ms": median_ms(
                lambda: manager.call(
                    project,
                    "user",
                    {
                        "action": "read",
                        "organizationId": org["id"],
                        "params": {"sinceVersion": version},
                    },
                ),
                20,
            ),
            "dispatch_paused_ms": median_ms(lambda: manager._dispatch_org(project, org["id"]), 50),
            "kanban_board_ms": median_ms(kanban.board, 10),
            "auto_reviews_ms": median_ms(auto_reviews, 10),
            "rem_organization_requests_ms": median_ms(
                lambda: RemHome._organization_requests(home), 10
            ),
            "usage_db_open_ms": median_ms(usage_open, 200),
        }
        manager.close()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
