"""Live code never imports a TOML-era module.

A forbidden contract in the style of import-linter, read with the standard
library's ast module: every import counts, including ones inside functions and
``TYPE_CHECKING`` blocks. The TOML-only list is the 24 Nov 2026 retirement map;
when a module on it is deleted, drop it here too.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import gofer

SRC = Path(gofer.__file__).resolve().parents[1]

TOML_ONLY_MODULES = frozenset(
    {
        "gofer.core.executor",
        "gofer.core.planner",
        "gofer.core.validation",
        "gofer.core.bundles",
        "gofer.core.runner",
        "gofer.core.bindings",
        "gofer.core.watcher",
        "gofer.core.revisions",
        "gofer.core.scheduler",
        "gofer.core.subflow_components",
        "gofer.core.workflow",
        "gofer.core.templates",
        "gofer.core.authoring",
        "gofer.core.operations",
        "gofer.core.references",
        "gofer.core.secrets",
        "gofer.core.runtime_values",
        "gofer.core.graph",
        "gofer.core.run_outputs",
        "gofer.utils.registry",
        "gofer.utils.run_state",
        "gofer.utils.agent_helpers",
        "gofer.cli.tui_editor",
        "gofer.cli.dag_renderer",
        "gofer.cli.commands.agent",
        "gofer.cli.commands.builder",
        "gofer.cli.commands.runner",
        "gofer.cli.commands.schedule",
        "gofer.cli.commands.watch",
    }
)

LIVE_PACKAGES = ("gofer/rattish", "gofer/devices", "gofer/subscriptions", "gofer/social")
LIVE_FILES = ("gofer/ui/brain_library.py", "gofer/core/approvals.py", "gofer/core/agent.py")


def _module_name(path: Path, src: Path) -> str:
    parts = list(path.relative_to(src).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(tree: ast.AST, module: str, is_package: bool) -> list[tuple[int, str]]:
    """Every module an import names, with its line. ``from a import b`` names a and a.b."""
    package = module if is_package else module.rpartition(".")[0]
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                target = ".".join([*base, *([node.module] if node.module else [])])
            else:
                target = node.module or ""
            found.append((node.lineno, target))
            found.extend((node.lineno, f"{target}.{alias.name}") for alias in node.names)
    return found


def _forbidden(name: str) -> str | None:
    for module in TOML_ONLY_MODULES:
        if name == module or name.startswith(module + "."):
            return module
    return None


def live_files(src: Path) -> list[Path]:
    files = [path for package in LIVE_PACKAGES for path in (src / package).rglob("*.py")]
    files.extend(src / name for name in LIVE_FILES if (src / name).is_file())
    return sorted(files)


def boundary_violations(src: Path) -> list[str]:
    violations: list[str] = []
    for path in live_files(src):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = _module_name(path, src)
        for line, name in _imported_modules(tree, module, path.name == "__init__.py"):
            forbidden = _forbidden(name)
            if forbidden is not None:
                where = path.relative_to(src)
                violations.append(f"{where}:{line} imports {forbidden}")
    return sorted(set(violations))


def test_live_code_imports_no_toml_only_module() -> None:
    assert len(live_files(SRC)) > 100, "the live code wasn't found; the scan checked nothing"
    assert boundary_violations(SRC) == []


def _fake_tree(tmp_path: Path, package: str, name: str, source: str) -> Path:
    folder = tmp_path / "gofer" / package
    folder.mkdir(parents=True)
    (tmp_path / "gofer" / "__init__.py").write_text("", encoding="utf-8")
    (folder / "__init__.py").write_text("", encoding="utf-8")
    (folder / name).write_text(source, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "source",
    [
        "import gofer.core.executor\n",
        "from gofer.core.executor import WorkflowExecutor\n",
        "from gofer.core import executor\n",
        "def run():\n    from gofer.core.executor import WorkflowExecutor\n    return 1\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import gofer.core.executor\n",
        "from ..core import executor\n",
    ],
    ids=["import", "from-module", "from-package", "inside-function", "type-checking", "relative"],
)
def test_a_rattish_module_importing_the_executor_fails(tmp_path: Path, source: str) -> None:
    src = _fake_tree(tmp_path, "rattish", "runner_bridge.py", source)
    assert boundary_violations(src) == [
        f"gofer/rattish/runner_bridge.py:{_line_of(source)} imports gofer.core.executor"
    ]


def _line_of(source: str) -> int:
    return next(i for i, line in enumerate(source.splitlines(), 1) if "executor" in line)


def test_live_modules_with_similar_names_pass(tmp_path: Path) -> None:
    source = (
        "from gofer.core.structured_output import validate_schema\n"
        "from gofer.core.http_retry import HttpRetryPolicy\n"
        "from gofer.rattish.workflow_id import validate_workflow_id\n"
        "from gofer.subscriptions.registry import shared_subscriptions\n"
        "import gofer.core.executor_notes\n"
    )
    src = _fake_tree(tmp_path, "rattish", "fine.py", source)
    assert boundary_violations(src) == []
