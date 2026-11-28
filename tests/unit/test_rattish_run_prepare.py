"""run_rattish_file's planning refusals: bad combinations stop before any run starts.

Each refusal happens while the run is planned, so it must leave no run record and no
saved values behind. The signature check pins the run entry point while its body is
split into helpers.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish.approved_files import ApprovedFiles
from gofer.rattish.model_overrides import ModelOverride
from gofer.rattish.run_service import RattishResumeRefused, run_rattish_file
from gofer.rattish.run_values import SavedRunValues
from gofer.rattish.workspaces import RegisteredWorkflow
from tests.unit.test_rattish_resume import Fake, run, workflow

OVERRIDE = [ModelOverride("build", "codex", "gpt-5")]
OVERRIDE_REFUSED = (
    "A run with another model can't resume, rerun, start from a node or run from a trigger."
)


def run_files(tmp_path: Path) -> set[Path]:
    """Every run record, journal and saved-values file under ``tmp_path``."""
    return {
        path
        for path in tmp_path.rglob("*")
        if path.is_file()
        and (path.suffix == ".inputs" or "logs" in path.parts or "runs" in path.parts)
    }


async def refused(
    tmp_path: Path,
    error: type[Exception],
    message: str,
    *,
    before: set[Path] | None = None,
    registered: RegisteredWorkflow | None = None,
    **options: Any,
) -> None:
    """Run with ``options``, expect ``error`` with exactly ``message``, and no new run files."""
    if registered is None:
        base, registered = workflow(tmp_path)
    else:
        base = tmp_path / "data"
    with pytest.raises(error) as caught:
        await run_rattish_file(
            registered.entrypoint, data_dir=base, handlers=Fake().handlers(), **options
        )
    assert type(caught.value) is error
    assert str(caught.value) == message
    assert run_files(tmp_path) == (before or set())


@pytest.mark.anyio
@pytest.mark.parametrize(
    "combined",
    [
        {"resume_from": {"run_id": "earlier"}},
        {"rerun_of": "earlier"},
        {"from_node": "test"},
        {"approved_files": "empty"},
    ],
    ids=["resume", "rerun", "start-node", "approved-files"],
)
async def test_another_model_refuses_resume_rerun_start_node_and_approved_files(
    tmp_path: Path, combined: dict[str, Any]
) -> None:
    if combined.get("approved_files") == "empty":
        # Covers no file, so the approval check passes and the override check refuses.
        combined = {
            "approved_files": ApprovedFiles(digests={}, data_dir=tmp_path / "data", run_id="t")
        }
    await refused(tmp_path, ValueError, OVERRIDE_REFUSED, model_overrides=OVERRIDE, **combined)


@pytest.mark.anyio
async def test_a_run_from_a_node_refuses_to_also_rerun(tmp_path: Path) -> None:
    await refused(
        tmp_path,
        ValueError,
        "A run from a node can't also resume or rerun a run.",
        from_node="test",
        rerun_of="earlier",
    )


@pytest.mark.anyio
async def test_a_run_from_a_node_refuses_to_also_resume(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    fake.fail["test"] = 1
    first = await run(base, registered, fake)
    assert first.status == "failed"
    earlier = run_files(tmp_path)
    # The helper sees the earlier run's files, so an empty result elsewhere means something.
    assert first.path in earlier

    await refused(
        tmp_path,
        ValueError,
        "A run from a node can't also resume or rerun a run.",
        before=earlier,
        registered=registered,
        resume_from=first.document,
        from_node="test",
    )


@pytest.mark.anyio
async def test_reuse_run_id_needs_a_run_from_a_node(tmp_path: Path) -> None:
    await refused(
        tmp_path,
        ValueError,
        "reuse_run_id only applies to a run from a node.",
        reuse_run_id="earlier",
    )


@pytest.mark.anyio
async def test_resume_inputs_need_a_resume_or_a_run_from_a_node(tmp_path: Path) -> None:
    await refused(
        tmp_path,
        ValueError,
        "resume_inputs only applies to a resumed run or a run from a node.",
        resume_inputs={"token": "secret"},
    )


@pytest.mark.anyio
async def test_resume_values_need_a_resume(tmp_path: Path) -> None:
    await refused(
        tmp_path,
        ValueError,
        "resume_values only applies to a resumed run.",
        resume_values=SavedRunValues(),
    )


@pytest.mark.anyio
async def test_a_resume_with_new_inputs_is_refused_before_anything_is_saved(
    tmp_path: Path,
) -> None:
    await refused(
        tmp_path,
        RattishResumeRefused,
        "A resumed run uses the saved run's inputs only.",
        resume_from={"run_id": "earlier"},
        trigger_events=[{"kind": "manual"}],
    )


# Written out so a change to the run entry point's parameters fails here first.
SIGNATURE = [
    ("source_path", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty, "Path"),
    ("workflow_inputs", "KEYWORD_ONLY", None, "Mapping[str, Any] | None"),
    (
        "trigger_events",
        "KEYWORD_ONLY",
        None,
        "list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None",
    ),
    ("data_dir", "KEYWORD_ONLY", None, "Path | None"),
    ("handlers", "KEYWORD_ONLY", None, "NodeHandlerRegistry | None"),
    ("subscriptions", "KEYWORD_ONLY", None, "Mapping[str, Subscription] | None"),
    ("notification_adapter", "KEYWORD_ONLY", None, "NotificationAdapter | None"),
    ("run_id", "KEYWORD_ONLY", None, "str | None"),
    ("cancel_event", "KEYWORD_ONLY", None, "threading.Event | None"),
    ("on_started", "KEYWORD_ONLY", None, "Callable[[RattishRunResult], None] | None"),
    ("expected_revision", "KEYWORD_ONLY", None, "str | None"),
    ("resume_from", "KEYWORD_ONLY", None, "Mapping[str, Any] | None"),
    ("started_by", "KEYWORD_ONLY", None, "StartedBy | None"),
    ("resume_inputs", "KEYWORD_ONLY", None, "Mapping[str, Any] | None"),
    ("resume_values", "KEYWORD_ONLY", None, "SavedRunValues | None"),
    ("rerun_of", "KEYWORD_ONLY", None, "str | None"),
    ("use_result_cache", "KEYWORD_ONLY", True, "bool"),
    ("daily_max_cost_usd", "KEYWORD_ONLY", None, "float | None"),
    ("daily_max_tokens", "KEYWORD_ONLY", None, "int | None"),
    ("from_node", "KEYWORD_ONLY", None, "str | None"),
    ("reuse_run_id", "KEYWORD_ONLY", None, "str | None"),
    ("approved_files", "KEYWORD_ONLY", None, "ApprovedFiles | None"),
    ("use_worktree", "KEYWORD_ONLY", True, "bool"),
    ("file_inputs_from", "KEYWORD_ONLY", None, "Path | None"),
    ("model_overrides", "KEYWORD_ONLY", None, "Sequence[ModelOverride] | None"),
]


def test_the_run_entry_point_keeps_its_25_parameters() -> None:
    signature = inspect.signature(run_rattish_file)
    assert [
        (name, parameter.kind.name, parameter.default, parameter.annotation)
        for name, parameter in signature.parameters.items()
    ] == SIGNATURE
    assert len(SIGNATURE) == 25
    assert signature.return_annotation == "RattishRunResult"
    assert inspect.iscoroutinefunction(run_rattish_file)
