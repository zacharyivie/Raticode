import pytest

from gofer.utils.run_state import (
    clear_workflow_stop,
    request_workflow_run_stop,
    request_workflow_stop,
    workflow_run_stop_path,
    workflow_stop_path,
)


@pytest.mark.parametrize("per_run", [False, True])
@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_stop_request_does_not_overwrite_link_target(tmp_path, per_run, link_kind):
    private = tmp_path / "private.txt"
    private.write_bytes(b"private contents")
    marker = (
        workflow_run_stop_path("workflow", "run", tmp_path)
        if per_run
        else workflow_stop_path("workflow", tmp_path)
    )
    marker.parent.mkdir(parents=True)
    if link_kind == "symlink":
        marker.symlink_to(private)
    else:
        marker.hardlink_to(private)

    if per_run:
        request_workflow_run_stop("workflow", "run", tmp_path)
    else:
        request_workflow_stop("workflow", tmp_path)

    assert private.read_bytes() == b"private contents"
    assert marker.read_bytes() == b"stop requested\n"
    assert not marker.is_symlink()
    assert marker.stat().st_ino != private.stat().st_ino


@pytest.mark.parametrize("per_run", [False, True])
def test_stop_request_rejects_linked_parent(tmp_path, per_run):
    outside = tmp_path / "outside"
    outside.mkdir()
    private = outside / ("run.stop" if per_run else "workflow.stop")
    private.write_bytes(b"private contents")
    state = tmp_path / "run-state"
    if per_run:
        state.mkdir()
        (state / "workflow").symlink_to(outside, target_is_directory=True)
    else:
        state.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        if per_run:
            request_workflow_run_stop("workflow", "run", tmp_path)
        else:
            request_workflow_stop("workflow", tmp_path)
    assert private.read_bytes() == b"private contents"


def test_clear_stop_does_not_remove_file_through_linked_parent(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    private = outside / "workflow.stop"
    private.write_bytes(b"private contents")
    (tmp_path / "run-state").symlink_to(outside, target_is_directory=True)
    clear_workflow_stop("workflow", tmp_path)
    assert private.read_bytes() == b"private contents"


@pytest.mark.parametrize("workflow_id", ["", ".", ".."])
def test_run_stop_rejects_directory_identifiers(tmp_path, workflow_id):
    with pytest.raises(ValueError):
        request_workflow_run_stop(workflow_id, "run", tmp_path)
    assert not (tmp_path / "run-state").exists()
    assert not list(tmp_path.rglob("*.stop"))


def test_stop_markers_create_parents_and_can_be_replaced_and_cleared(tmp_path):
    for _ in range(2):
        marker = request_workflow_stop("workflow", tmp_path)
        assert marker.read_bytes() == b"stop requested\n"
        run_marker = request_workflow_run_stop("workflow", "run", tmp_path)
        assert run_marker.read_bytes() == b"stop requested\n"
    clear_workflow_stop("workflow", tmp_path)
    clear_workflow_stop("workflow", tmp_path)
    assert not marker.exists()
    assert run_marker.exists()
