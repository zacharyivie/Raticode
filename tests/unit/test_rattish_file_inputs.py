"""File inputs: compiling them, copying the file into the run folder, and reusing the copy."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.file_inputs import (
    DEFAULT_MAX_SIZE,
    FileInputError,
    FileInputSpec,
    accepts,
    check_file,
    file_input_specs,
    new_upload_path,
    parse_size,
    remove_orphaned_files,
    run_files_dir,
    safe_file_name,
    sweep_uploads,
    uploads_dir,
)
from gofer.rattish.preflight import run_preflight
from gofer.rattish.run_service import (
    RattishResumeRefused,
    rerun_rattish_run,
    resume_rattish_run,
    run_rattish_file,
)
from gofer.rattish.run_values import delete_run_files, saved_values_path
from gofer.rattish.runtime import DEFAULT_NODE_HANDLERS, HandlerResult, NodeHandlerRegistry
from gofer.rattish.workspaces import RegisteredWorkflow, create_registered_workflow

SOURCE = """Rattish: 1
Workflow:
  name: Summarize a file
  inputs:
    document:
      schema: {"type": "string", "format": "file"}
      required: true
      accept: [".txt", "application/pdf"]
      max-size: 1 KB
Node read:
  type: bash-command
  command: cat "$DOCUMENT"
  with:
    document: input.document
  to: report
Node report:
  type: bash-command
  command: report
  needs: read
"""


class Fake:
    """Records the file each node saw, and fails nodes on demand."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str]] = []
        self.fail: set[str] = set()

    async def __call__(self, node, context, bindings) -> HandlerResult:
        path = str(context.workflow_inputs.get("document"))
        self.seen.append((node["id"], path, Path(path).read_text(encoding="utf-8")))
        result = {"stdout": node["id"], "stderr": "", "exit_code": 0}
        if node["id"] in self.fail:
            self.fail.discard(node["id"])
            return HandlerResult(False, {**result, "exit_code": 1})
        return HandlerResult(True, result)

    def handlers(self) -> NodeHandlerRegistry:
        return NodeHandlerRegistry(
            {**DEFAULT_NODE_HANDLERS._handlers, "raticode.bash_command": self}
        )


def workflow(tmp_path: Path, source: str = SOURCE) -> tuple[Path, RegisteredWorkflow]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    base = tmp_path / "data"
    registered = create_registered_workflow(project, "Summarize", registry_dir=base)
    registered.entrypoint.write_text(source, encoding="utf-8")
    return base, registered


async def run(base: Path, registered: RegisteredWorkflow, fake: Fake, **options: Any):
    return await run_rattish_file(
        registered.entrypoint, data_dir=base, handlers=fake.handlers(), **options
    )


def compile_source(tmp_path: Path, inputs: str) -> Any:
    path = tmp_path / "check.rattish"
    path.write_text(
        "Rattish: 1\nWorkflow:\n  name: Check\n  inputs:\n"
        + inputs
        + "Node a:\n  type: bash-command\n  command: echo\n",
        encoding="utf-8",
    )
    return compile_rattish_file(path, data_dir=tmp_path / "data")


def test_a_file_input_compiles_with_its_accept_list_and_size(tmp_path: Path) -> None:
    _base, registered = workflow(tmp_path)
    compiled = compile_rattish_file(registered.entrypoint, data_dir=tmp_path / "data")
    (declaration,) = compiled.ir["workflow"]["inputs"]
    assert declaration["file"] == {"accept": [".txt", "application/pdf"], "max_size": 1024}
    assert file_input_specs(compiled.ir) == {
        "document": FileInputSpec("document", (".txt", "application/pdf"), 1024)
    }


def test_a_file_input_defaults_to_any_file_up_to_20_mb(tmp_path: Path) -> None:
    compiled = compile_source(
        tmp_path, '    doc:\n      schema: {"type": "string", "format": "file"}\n'
    )
    assert compiled.ir["workflow"]["inputs"][0]["file"] == {
        "accept": [],
        "max_size": 20 * 1024 * 1024,
    }
    assert DEFAULT_MAX_SIZE == 20 * 1024 * 1024


@pytest.mark.parametrize(
    ("inputs", "message"),
    [
        (
            '    doc:\n      schema: {"type": "string"}\n      accept: [".pdf"]\n',
            "accept applies only to file inputs",
        ),
        (
            '    doc:\n      schema: {"type": "string", "format": "file"}\n'
            '      default: "a.txt"\n',
            "can't have a default",
        ),
        (
            '    doc:\n      schema: {"type": "string", "format": "file"}\n      accept: ["pdf"]\n',
            "isn't a file extension or a MIME pattern",
        ),
        (
            '    doc:\n      schema: {"type": "string", "format": "file"}\n      max-size: 2 GB\n',
            "at most 1 GB",
        ),
        (
            '    doc:\n      schema: {"type": "array", "items": '
            '{"type": "string", "format": "file"}}\n',
            "works only on the input's own schema",
        ),
    ],
)
def test_file_input_mistakes_are_compile_errors(tmp_path: Path, inputs: str, message: str) -> None:
    with pytest.raises(RattishError) as caught:
        compile_source(tmp_path, inputs)
    assert any(message in item.message for item in caught.value.diagnostics)


def test_sizes_and_accept_patterns() -> None:
    assert parse_size("5 MB") == (5 * 1024 * 1024, None)
    assert parse_size(2048) == (2048, None)
    assert parse_size("1.5kb") == (1536, None)
    assert parse_size("lots")[1]
    spec = FileInputSpec("doc", (".md", "image/*"))
    assert accepts(spec, "Notes.MD")
    assert accepts(spec, "photo.png")
    assert not accepts(spec, "report.pdf")
    assert accepts(spec, "upload.bin", "image/heic")
    assert safe_file_name("../../etc/passwd") == "passwd"
    assert safe_file_name("..hidden\x00.txt") == "hidden.txt"
    assert safe_file_name("") == "file"


def test_check_file_explains_each_refusal(tmp_path: Path) -> None:
    spec = FileInputSpec("doc", (".txt",), 10)
    (tmp_path / "ok.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "big.txt").write_text("x" * 11, encoding="utf-8")
    (tmp_path / "pic.png").write_bytes(b"png")
    assert check_file(spec, "ok.txt", base=tmp_path) == (tmp_path / "ok.txt").resolve()
    with pytest.raises(FileInputError, match="doesn't exist"):
        check_file(spec, "gone.txt", base=tmp_path)
    with pytest.raises(FileInputError, match="is not a file"):
        check_file(spec, str(tmp_path))
    with pytest.raises(FileInputError, match="11 bytes, more than the 10 bytes"):
        check_file(spec, "big.txt", base=tmp_path)
    with pytest.raises(FileInputError, match=r"isn't a kind of file this input takes \(\.txt\)"):
        check_file(spec, "pic.png", base=tmp_path)


@pytest.mark.anyio
async def test_a_run_copies_the_file_owner_only_and_binds_the_copy(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    original = tmp_path / "notes.txt"
    original.write_text("first", encoding="utf-8")
    fake = Fake()
    result = await run(base, registered, fake, workflow_inputs={"document": str(original)})
    assert result.status == "passed", result.document.get("error")
    folder = run_files_dir(result.path)
    copy = folder / "document" / "notes.txt"
    assert fake.seen[0] == ("read", str(copy), "first")
    assert copy.read_text(encoding="utf-8") == "first"
    if sys.platform != "win32":
        assert stat.S_IMODE(copy.stat().st_mode) == 0o600
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        assert stat.S_IMODE((folder / "document").stat().st_mode) == 0o700
    saved = json.loads(saved_values_path(result.path).read_text(encoding="utf-8"))
    assert saved["inputs"] == {"document": str(copy)}
    # The run list reads *.json records; the files folder is not one of them.
    assert sorted(p.name for p in result.path.parent.glob("*.json")) == [result.path.name]


@pytest.mark.anyio
async def test_unsuitable_files_end_the_run_as_invalid_inputs(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 2000, encoding="utf-8")
    (tmp_path / "pic.png").write_bytes(b"png")
    fake = Fake()
    for value, message in (
        (str(tmp_path / "missing.txt"), "doesn't exist"),
        (str(tmp_path / "big.txt"), "more than the 1 KB"),
        (str(tmp_path / "pic.png"), "isn't a kind of file"),
    ):
        result = await run(base, registered, fake, workflow_inputs={"document": value})
        assert result.status == "invalid_inputs"
        assert message in result.document["error"]["message"]
        assert not run_files_dir(result.path).exists()
    assert fake.seen == []


@pytest.mark.anyio
async def test_resume_and_run_again_reuse_the_copy_not_the_original(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    original = tmp_path / "notes.txt"
    original.write_text("first", encoding="utf-8")
    fake = Fake()
    fake.fail.add("report")
    failed = await run(base, registered, fake, workflow_inputs={"document": str(original)})
    assert failed.status == "failed"
    # Later edits to the original can't change the run, and neither can deleting it.
    original.write_text("second", encoding="utf-8")
    original.unlink()
    fake.seen.clear()
    resumed = await resume_rattish_run(
        registered.entrypoint, failed.document, data_dir=base, handlers=fake.handlers()
    )
    assert resumed.status == "passed"
    resumed_copy = run_files_dir(resumed.path) / "document" / "notes.txt"
    assert fake.seen == [("report", str(resumed_copy), "first")]

    fake.seen.clear()
    again = await rerun_rattish_run(
        registered.entrypoint, failed.document, data_dir=base, handlers=fake.handlers()
    )
    assert again.status == "passed"
    again_copy = run_files_dir(again.path) / "document" / "notes.txt"
    assert [item[1:] for item in fake.seen] == [(str(again_copy), "first")] * 2

    # Each run owns its copy, so deleting the first run keeps the others' files.
    delete_run_files(failed.path)
    assert not run_files_dir(failed.path).exists()
    assert resumed_copy.read_text(encoding="utf-8") == "first"
    assert again_copy.read_text(encoding="utf-8") == "first"


@pytest.mark.anyio
async def test_a_run_whose_copy_is_gone_says_so(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    original = tmp_path / "notes.txt"
    original.write_text("first", encoding="utf-8")
    fake = Fake()
    fake.fail.add("read")
    failed = await run(base, registered, fake, workflow_inputs={"document": str(original)})
    for path in run_files_dir(failed.path).rglob("*.txt"):
        path.unlink()
    with pytest.raises(RattishResumeRefused, match="copy of the file for input 'document'"):
        await resume_rattish_run(
            registered.entrypoint, failed.document, data_dir=base, handlers=fake.handlers()
        )
    with pytest.raises(RattishResumeRefused, match="choose the file again"):
        await rerun_rattish_run(
            registered.entrypoint, failed.document, data_dir=base, handlers=fake.handlers()
        )


@pytest.mark.anyio
async def test_a_phone_upload_is_used_once_and_stale_ones_are_swept(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    upload = new_upload_path(base, "u1", "shared.txt")
    upload.write_text("from phone", encoding="utf-8")
    fake = Fake()
    result = await run(base, registered, fake, workflow_inputs={"document": str(upload)})
    assert result.status == "passed"
    assert not upload.parent.exists()
    assert fake.seen[0][2] == "from phone"

    stale = new_upload_path(base, "u2", "old.txt")
    stale.write_text("old", encoding="utf-8")
    fresh = new_upload_path(base, "u3", "new.txt")
    fresh.write_text("new", encoding="utf-8")
    old = time.time() - 7200
    os.utime(stale.parent, (old, old))
    sweep_uploads(base)
    assert not stale.parent.exists() and fresh.exists()
    assert uploads_dir(base).exists()


def test_orphaned_files_folders_are_swept_after_a_grace_period(tmp_path: Path) -> None:
    (tmp_path / "kept.json").write_text("{}", encoding="utf-8")
    (tmp_path / "kept.files").mkdir()
    (tmp_path / "gone.files" / "doc").mkdir(parents=True)
    (tmp_path / "new.files").mkdir()
    old = time.time() - 3600
    os.utime(tmp_path / "gone.files", (old, old))
    assert remove_orphaned_files(tmp_path) == ["gone"]
    assert (tmp_path / "kept.files").exists() and (tmp_path / "new.files").exists()


def test_preflight_for_a_trigger_names_a_required_file_input(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    compiled = compile_rattish_file(registered.entrypoint, data_dir=base)
    manual = run_preflight(compiled.ir, data_dir=base)
    assert not any(
        item.code == "RATTISH_PREFLIGHT_FILE_INPUT_UNFILLED" for item in manual.diagnostics
    )
    triggered = run_preflight(compiled.ir, data_dir=base, triggered=True)
    (problem,) = [
        item
        for item in triggered.diagnostics
        if item.code == "RATTISH_PREFLIGHT_FILE_INPUT_UNFILLED"
    ]
    assert "can't fill the required file input 'document'" in problem.message
    assert not triggered.ready


def test_a_trigger_refuses_file_inputs(tmp_path: Path) -> None:
    from gofer.rattish.triggers import TriggerError, _clean_inputs

    base, registered = workflow(tmp_path)
    ir = compile_rattish_file(registered.entrypoint, data_dir=base).ir
    with pytest.raises(TriggerError, match="a trigger has nobody to choose one"):
        _clean_inputs({}, ir)
    with pytest.raises(TriggerError, match="can't fill file input 'document'"):
        _clean_inputs({"document": "/tmp/a.txt"}, ir)
    optional = json.loads(json.dumps(ir))
    optional["workflow"]["inputs"][0]["required"] = False
    assert _clean_inputs({}, optional) == {}
