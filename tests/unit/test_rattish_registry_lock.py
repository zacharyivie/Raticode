"""Workflow registry changes made at the same time keep every registration."""

from __future__ import annotations

import json
import multiprocessing
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gofer.rattish.workspaces import (
    RegisteredWorkflow,
    create_registered_workflow,
    discover_project_workflows,
)


def _registry_entries(registry: Path) -> list[dict[str, Any]]:
    document = json.loads(
        (registry / "radish" / "workspace-registry.json").read_text(encoding="utf-8")
    )
    return list(document["workflows"])


def _run_together(calls: list[Callable[[], Any]]) -> list[Any]:
    """Run each call on its own thread, released at the same moment."""
    barrier = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)
    errors: list[BaseException] = []

    def run(index: int) -> None:
        barrier.wait()
        try:
            results[index] = calls[index]()
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    return results


def _assert_both_registered(
    registry: Path, projects: list[Path], created: list[tuple[Path, str]]
) -> None:
    entries = _registry_entries(registry)
    assert sorted(entry["project_root"] for entry in entries) == sorted(map(str, projects))
    ids = [entry["id"] for entry in entries]
    assert len(set(ids)) == 2, ids
    by_project = {entry["project_root"]: entry["id"] for entry in entries}
    for project, workflow_id in created:
        assert by_project[str(project)] == workflow_id
        assert (project / ".raticode" / workflow_id / "workflow.rattish").is_file()


def _two_projects(tmp_path: Path) -> list[Path]:
    projects = [tmp_path / "alpha", tmp_path / "beta"]
    for project in projects:
        project.mkdir(parents=True)
    return projects


def test_two_threads_creating_one_name_in_two_projects_keep_both(tmp_path: Path) -> None:
    projects = _two_projects(tmp_path)
    registry = tmp_path / "app-data"

    def create(project: Path) -> Callable[[], RegisteredWorkflow]:
        return lambda: create_registered_workflow(project, "Nightly report", registry_dir=registry)

    created: list[RegisteredWorkflow] = _run_together([create(project) for project in projects])

    _assert_both_registered(
        registry,
        projects,
        [(workflow.project_root, workflow.workflow_id) for workflow in created],
    )


def _create_in_process(
    project: str, registry: str, barrier: Any, results: Any
) -> None:  # pragma: no cover - runs in a child process
    barrier.wait()
    workflow = create_registered_workflow(
        Path(project), "Nightly report", registry_dir=Path(registry)
    )
    results.put((project, workflow.workflow_id))


def test_two_processes_creating_one_name_in_two_projects_keep_both(tmp_path: Path) -> None:
    # rattish new runs in the CLI's own process, beside the app's backend.
    projects = _two_projects(tmp_path)
    registry = tmp_path / "app-data"
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(len(projects))
    results = context.Queue()
    processes = [
        context.Process(
            target=_create_in_process, args=(str(project), str(registry), barrier, results)
        )
        for project in projects
    ]
    for process in processes:
        process.start()
    created = [results.get(timeout=120) for _ in processes]
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0

    _assert_both_registered(
        registry,
        projects,
        [(Path(project), workflow_id) for project, workflow_id in created],
    )


def test_projects_discovered_together_keep_both_registrations(tmp_path: Path) -> None:
    # Opening several projects at once discovers them on separate request threads.
    for attempt in range(20):
        projects = _two_projects(tmp_path / str(attempt))
        for project in projects:
            workflow_root = project / ".raticode" / "nightly-report"
            workflow_root.mkdir(parents=True)
            (workflow_root / "workflow.rattish").write_text(
                'Rattish: 1\n\nWorkflow:\n  name: "Nightly report"\n', encoding="utf-8"
            )
        registry = tmp_path / str(attempt) / "app-data"

        def discover(project: Path) -> Callable[[], Any]:
            return lambda: discover_project_workflows(project, registry_dir=registry)

        discoveries = _run_together([discover(project) for project in projects])

        entries = _registry_entries(registry)
        assert sorted(entry["project_root"] for entry in entries) == sorted(map(str, projects)), (
            f"run {attempt}"
        )
        assert len({entry["id"] for entry in entries}) == 2, f"run {attempt}"
        for discovery in discoveries:
            assert len(discovery.workflows) == 1
            (workflow,) = discovery.workflows
            assert any(
                entry["id"] == workflow.workflow_id
                and entry["project_root"] == str(workflow.project_root)
                for entry in entries
            ), f"run {attempt}"
