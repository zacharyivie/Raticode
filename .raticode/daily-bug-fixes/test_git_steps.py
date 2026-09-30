"""Run only against disposable repositories and a fake agent, never a provider CLI."""

import shutil
from pathlib import Path
from typing import Any

import git_steps as steps
import pytest

from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.provider_contracts import load_provider_contracts
from gofer.rattish.runtime import (
    DEFAULT_NODE_HANDLERS,
    HandlerResult,
    NodeHandlerRegistry,
    RuntimeErrorInfo,
)
from gofer.rattish.workflow_runtime import execute_workflow

HERE = Path(__file__).parent
ASSETS = HERE.parents[1] / "rattish"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    steps.git(tmp_path, "init", "-b", "main")
    steps.git(tmp_path, "config", "user.name", "Workflow Test")
    steps.git(tmp_path, "config", "user.email", "workflow@example.invalid")
    steps.git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "tracked.txt").write_text("base\n")
    steps.git(tmp_path, "add", ".")
    steps.git(tmp_path, "commit", "-m", "base")
    return tmp_path


@pytest.mark.parametrize("collision", ["local", "remote", "directory", "worktree", "stale"])
def test_refuses_existing_branch_or_worktree(repo: Path, collision: str) -> None:
    target = repo / ".worktrees" / steps.BRANCH
    if collision == "local":
        steps.git(repo, "branch", steps.BRANCH)
    elif collision == "remote":
        steps.git(repo, "update-ref", "refs/remotes/origin/" + steps.BRANCH, "HEAD")
    elif collision == "directory":
        target.mkdir(parents=True)
        (target / "keep.txt").write_text("keep")
    else:
        target = repo / "elsewhere" / steps.BRANCH
        steps.git(repo, "worktree", "add", "--detach", str(target), "HEAD")
        if collision == "stale":
            shutil.rmtree(target)
    before = steps.git(repo, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="already exists"):
        steps.create(repo)
    assert steps.git(repo, "rev-parse", "HEAD") == before
    if collision == "directory":
        assert (target / "keep.txt").read_text() == "keep"


def test_25_commits_squash_to_one_preserving_all_changes(repo: Path) -> None:
    base = steps.git(repo, "rev-parse", "HEAD")
    (repo / "tracked.txt").write_text("user's uncommitted changes\n")
    worktree = steps.create(repo)
    assert (worktree / "tracked.txt").read_text() == "base\n"
    for index in range(25):
        if index != 12:  # An empty scan must still commit and advance.
            (worktree / f"fix-{index}.txt").write_text(f"fix {index}\n")
        steps.commit_pass(worktree, index)
    assert steps.git(worktree, "rev-list", "--count", f"{base}..HEAD") == "25"
    assert set(steps.git(worktree, "log", "--format=%s", f"{base}..HEAD").splitlines()) == {
        "wip: but fix"
    }
    tree = steps.git(worktree, "rev-parse", "HEAD^{tree}")
    steps.soft_reset(worktree)
    assert steps.git(worktree, "rev-parse", "HEAD") == base
    assert steps.git(worktree, "write-tree") == tree
    steps.final_commit(worktree, "fix: address release bugs and security issues")
    assert steps.git(worktree, "rev-list", "--count", f"{base}..HEAD") == "1"
    assert steps.git(worktree, "rev-parse", "HEAD^{tree}") == tree
    assert steps.git(repo, "rev-parse", "HEAD") == base
    assert (repo / "tracked.txt").read_text() == "user's uncommitted changes\n"


def test_refuses_early_reset_and_out_of_order_commit(repo: Path) -> None:
    worktree = steps.create(repo)
    with pytest.raises(RuntimeError, match="All 25"):
        steps.soft_reset(worktree)
    with pytest.raises(RuntimeError, match="in order"):
        steps.commit_pass(worktree, 1)
    steps.git(worktree, "commit", "--allow-empty", "-m", "unexpected agent commit")
    with pytest.raises(RuntimeError, match="HEAD changed"):
        steps.commit_pass(worktree, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", [None, 3, "summary"])
async def test_workflow_order_and_fail_fast(repo: Path, fail_at: Any) -> None:
    workflow_dir = repo / ".raticode" / steps.BRANCH
    workflow_dir.mkdir(parents=True)
    shutil.copy(HERE / "git_steps.py", workflow_dir)
    compiler = RattishCompiler.from_paths(
        schema_root=ASSETS / "schemas",
        contract_paths=sorted((ASSETS / "contracts").glob("*.json")),
    )
    providers = load_provider_contracts(
        ASSETS / "schemas" / "provider-contract.schema.json",
        sorted((ASSETS / "providers").glob("*.json")),
    )
    ir = compiler.compile(
        (HERE / "workflow.rattish").read_text(),
        CompileContext("daily-bug-fixes-test", repo, provider_contracts=providers),
    ).ir
    seen: list[str] = []
    count = 0
    base = steps.git(repo, "rev-parse", "HEAD")

    async def fake_agent(node: Any, context: Any, bindings: Any) -> HandlerResult:
        nonlocal count
        seen.append(node["id"])
        worktree = Path(node["configuration"]["working_dir"])
        assert node["configuration"]["model"] == "gpt-6-astra"
        assert node["configuration"]["effort"] == "high"
        if node["id"] == "scan-and-fix":
            # Each next agent must observe the previous agent's commit.
            assert steps.git(worktree, "rev-list", "--count", f"{base}..HEAD") == str(count)
            if count == fail_at:
                return HandlerResult(False, "", RuntimeErrorInfo("provider", "TEST", "failure"))
            (worktree / "fix.txt").write_text(f"fixed {count}\n")
            count += 1
            return HandlerResult(True, "fixed")
        assert count == 25
        assert steps.git(worktree, "rev-parse", "HEAD") == base
        assert steps.git(worktree, "diff", "--cached", "--name-only") == "fix.txt"
        if fail_at == "summary":
            return HandlerResult(False, "", RuntimeErrorInfo("provider", "TEST", "failure"))
        return HandlerResult(True, {"message": "fix: correct the test bug"})

    handlers = NodeHandlerRegistry(
        {
            "raticode.agent": fake_agent,
            "raticode.loop": DEFAULT_NODE_HANDLERS.require("raticode.loop"),
            "raticode.bash_command": DEFAULT_NODE_HANDLERS.require("raticode.bash_command"),
        }
    )
    result = await execute_workflow(ir, handlers=handlers, subscriptions={})
    assert result.outcome == ("pass" if fail_at is None else "failure"), result.error
    worktree = repo / ".worktrees" / steps.BRANCH
    if fail_at is None:
        assert seen == ["scan-and-fix"] * 25 + ["summarize-fixes"]
        assert steps.git(worktree, "rev-list", "--count", f"{base}..HEAD") == "1"
        assert (worktree / "fix.txt").read_text() == "fixed 24\n"
    elif fail_at == 3:
        assert seen == ["scan-and-fix"] * 4
        assert steps.git(worktree, "rev-list", "--count", f"{base}..HEAD") == "3"
    else:
        assert steps.git(worktree, "rev-parse", "HEAD") == base
        assert steps.git(worktree, "diff", "--cached", "--name-only") == "fix.txt"
