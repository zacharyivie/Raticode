from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]

# A small checkout where everything is used: each test adds one thing to it.
CLEAN_TREE = {
    "src/gofer/__init__.py": "",
    "src/gofer/__main__.py": "from gofer.server import Handler, serve\n\nserve(Handler)\n",
    "src/gofer/server.py": (
        "import functools\n"
        "\n"
        "\n"
        "def serve():\n"
        "    return _port()\n"
        "\n"
        "\n"
        "def _port():\n"
        "    return 8000\n"
        "\n"
        "\n"
        "class Handler:\n"
        "    def do_GET(self):\n"
        "        return None\n"
        "\n"
        "    def log_message(self, *args):\n"
        "        return None\n"
        "\n"
        "    def __repr__(self):\n"
        "        return 'Handler'\n"
        "\n"
        "\n"
        "@functools.cache\n"
        "def registered():\n"
        "    return None\n"
    ),
    "tests/test_server.py": (
        "from gofer.server import Handler, serve\n\n\ndef test_serve():\n"
        "    assert serve() and Handler()\n"
    ),
    "frontend/package.json": '{"main": "electron/main.cjs", "scripts": {}}\n',
    "frontend/index.html": '<script type="module" src="/src/main.jsx"></script>\n',
    "frontend/src/main.jsx": 'import { greet } from "./lib/greet.js";\n\ngreet();\n',
    "frontend/src/lib/greet.js": "export function greet() {\n  return 'hi';\n}\n",
    "frontend/electron/main.cjs": "const path = require('node:path');\n",
}


@pytest.fixture(scope="module")
def check_dead_code() -> Iterator[Any]:
    spec = importlib.util.spec_from_file_location(
        "check_dead_code", ROOT / "scripts/check-dead-code.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look up their module while the script loads.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(sys.modules, "check_dead_code", module)
    spec.loader.exec_module(module)
    yield module
    monkeypatch.undo()


def _tree(root: Path, extra: dict[str, str] | None = None) -> Path:
    for relative, text in {**CLEAN_TREE, **(extra or {})}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _run(module: Any, root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = module.main(["--root", str(root)])
    return code, capsys.readouterr().out


def _append(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_a_tree_where_everything_is_used_passes(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Also covers the skips: do_GET, log_message, __repr__ and a decorated function.
    code, out = _run(check_dead_code, _tree(tmp_path), capsys)
    assert (code, out.strip()) == (0, "check-dead-code: nothing unused")


def test_an_uncalled_private_function_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path)
    _append(root, "src/gofer/server.py", "\n\ndef _forgotten():\n    return 1\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "src/gofer/server.py:28 _forgotten"


def test_an_uncalled_method_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path)
    _append(root, "src/gofer/server.py", "\n\nclass Store:\n    def stale(self):\n        pass\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[:2] == ["src/gofer/server.py:28 Store", "src/gofer/server.py:29 stale"]


def test_an_unused_module_constant_fails_until_another_file_names_it(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path)
    _append(root, "src/gofer/server.py", "\n\nRETRY_LIMIT = 3\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "src/gofer/server.py:28 RETRY_LIMIT"

    (root / "src/gofer/client.py").write_text(
        "from gofer.server import RETRY_LIMIT\n\nprint(RETRY_LIMIT)\n", encoding="utf-8"
    )
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_every_upper_case_module_assignment_counts_as_a_constant(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path)
    _append(
        root,
        "src/gofer/server.py",
        "\n\nNAMES: tuple[str, ...] = ('a',)\n"
        "LOW, HIGH = 1, 2\n"
        "_SECRET = FIRST = 'x'\n"
        "lowercase = 1\n"
        "Mixed = 2\n"
        "LATER: int\n"
        "if lowercase:\n"
        "    NESTED = 3\n",
    )
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[:5] == [
        "src/gofer/server.py:28 NAMES",
        "src/gofer/server.py:29 HIGH",
        "src/gofer/server.py:29 LOW",
        "src/gofer/server.py:30 FIRST",
        "src/gofer/server.py:30 _SECRET",
    ]
    assert not {"lowercase", "Mixed", "LATER", "NESTED"} & set(out.split())


def test_a_constant_only_tests_read_needs_a_test_seam_reason(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    test_file = {"tests/test_limit.py": "from gofer.server import SUNSET\n\nassert SUNSET\n"}
    root = _tree(tmp_path / "tested", test_file)
    _append(root, "src/gofer/server.py", "\n\nSUNSET = 2027\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "src/gofer/server.py:28 SUNSET (only tests use it)"

    seam = {"scripts/dead-code-allowlist.txt": "SUNSET  # test seam: a test warns before it\n"}
    root = _tree(tmp_path / "seam", {**test_file, **seam})
    _append(root, "src/gofer/server.py", "\n\nSUNSET = 2027\n")
    assert _run(check_dead_code, root, capsys)[0] == 0


def test_an_unused_frontend_export_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path)
    _append(root, "frontend/src/lib/greet.js", "\nexport const FAREWELL = 'bye';\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "frontend/src/lib/greet.js:5 FAREWELL"


def test_an_export_only_its_own_file_uses_fails_until_the_export_goes(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path)
    _append(root, "frontend/src/lib/greet.js", "\nexport const NAME = 'x';\ngreet(NAME);\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "frontend/src/lib/greet.js:5 NAME (export only its own file uses)"

    (root / "frontend/src/lib/greet.js").write_text(
        "export function greet() {\n  return 'hi';\n}\n\nconst NAME = 'x';\ngreet(NAME);\n",
        encoding="utf-8",
    )
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_an_export_another_file_names_is_not_only_its_own(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A test, an HTML page or a doc naming the export counts; so does an export in a
    # test file, which the kind leaves out.
    uses = {
        "frontend/src/lib/greet.test.mjs": 'import { NAME } from "./greet.js";\n\nNAME;\n',
        "frontend/electron/page.html": "<p>STAMP</p>\n",
        "frontend/src/lib/helpers.test.mjs": "export const LOCAL = 1;\nLOCAL;\n",
    }
    root = _tree(tmp_path, uses)
    _append(
        root,
        "frontend/src/lib/greet.js",
        "\nexport const NAME = 'x';\ngreet(NAME);\nexport const STAMP = 1;\ngreet(STAMP);\n",
    )
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_an_allowlisted_export_only_its_own_file_uses_passes(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allow = {
        "scripts/dead-code-allowlist.txt": (
            "frontend/src/lib/greet.js:NAME  # an add-on outside the repository imports it\n"
        )
    }
    root = _tree(tmp_path, allow)
    _append(root, "frontend/src/lib/greet.js", "\nexport const NAME = 'x';\ngreet(NAME);\n")
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_raticode_has_no_export_only_its_own_file_uses(check_dead_code: Any) -> None:
    files = dict(check_dead_code.corpus(ROOT))
    exports = check_dead_code.frontend_exports(files, check_dead_code.frontend_modules(files))
    allowed = check_dead_code.read_allowlist(ROOT / check_dead_code.ALLOWLIST)
    found = [
        str(finding)
        for finding in check_dead_code.exports_only_their_file_uses(exports, files)
        if f"{finding.path}:{finding.name}" not in allowed and finding.name not in allowed
    ]
    assert found == []


def test_a_frontend_module_nothing_imports_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, {"frontend/src/lib/orphan.js": "const x = 1;\nconsole.log(x);\n"})
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "frontend/src/lib/orphan.js:1 orphan.js"


def test_an_extensionless_import_counts_as_an_import(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, {"frontend/src/components/Panel.jsx": "export default 1;\n"})
    _append(root, "frontend/src/main.jsx", 'import Panel from "./components/Panel";\n')
    assert _run(check_dead_code, root, capsys)[0] == 0


def test_a_name_only_a_browser_test_uses_needs_a_test_seam_reason(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = {"frontend/src/lib/remHomeSample.js": "export const SAMPLE_PROJECTS = [];\n"}
    browser_test = {
        "frontend/browser-tests/rem-home.browser.cjs": (
            "// loads ../src/lib/remHomeSample.js\nconst { SAMPLE_PROJECTS } = globalThis;\n"
        ),
        "frontend/package.json": (
            '{"main": "electron/main.cjs", '
            '"scripts": {"test:rem-home": "electron browser-tests/rem-home.browser.cjs"}}\n'
        ),
    }
    code, out = _run(check_dead_code, _tree(tmp_path / "with", {**sample, **browser_test}), capsys)
    assert code == 1
    assert out.splitlines()[0] == (
        "frontend/src/lib/remHomeSample.js:1 SAMPLE_PROJECTS (only tests use it)"
    )

    seam = {
        "scripts/dead-code-allowlist.txt": (
            "SAMPLE_PROJECTS  # test seam: the Rem home browser test loads them\n"
        )
    }
    root = _tree(tmp_path / "seam", {**sample, **browser_test, **seam})
    assert _run(check_dead_code, root, capsys)[0] == 0

    code, out = _run(check_dead_code, _tree(tmp_path / "without", sample), capsys)
    assert code == 1
    assert "frontend/src/lib/remHomeSample.js:1 SAMPLE_PROJECTS" in out.splitlines()


def test_names_in_electron_tests_and_package_json_scripts_count_as_used(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(
        tmp_path,
        {
            "frontend/electron/helper.cjs": "module.exports = {};\n",
            "frontend/electron/tests/helper.test.cjs": "require('../helper.cjs');\n",
            "frontend/electron/smoke.cjs": "process.exit(0);\n",
            "frontend/package.json": '{"scripts": {"smoke": "node electron/smoke.cjs"}}\n',
            "frontend/electron/main.cjs": "export const SMOKE_TIMEOUT = 1;\n",
            "frontend/electron/tests/main.test.cjs": "// main.cjs SMOKE_TIMEOUT\n",
            "scripts/dead-code-allowlist.txt": "SMOKE_TIMEOUT  # test seam: the smoke test\n",
        },
    )
    assert _run(check_dead_code, root, capsys)[0] == 0


def test_an_allowlisted_name_passes(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(
        tmp_path,
        {
            "scripts/dead-code-allowlist.txt": (
                "# kept on purpose\n"
                "_called_by_plugins  # plugins outside the repository call it by name\n"
                "frontend/src/lib/greet.js:LEGACY  # read by an old saved layout\n"
            )
        },
    )
    _append(root, "src/gofer/server.py", "\n\ndef _called_by_plugins():\n    return 1\n")
    _append(root, "frontend/src/lib/greet.js", "\nexport const LEGACY = 1;\n")
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_a_path_scoped_allowlist_entry_covers_only_that_file(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(
        tmp_path,
        {"scripts/dead-code-allowlist.txt": "src/gofer/other.py:_spare  # reason\n"},
    )
    _append(root, "src/gofer/server.py", "\n\ndef _spare():\n    return 1\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    # The line for a file that doesn't exist matches nothing, so it is reported too.
    assert out.splitlines()[:2] == [
        "scripts/dead-code-allowlist.txt:1 src/gofer/other.py:_spare "
        "(allowlist line matches nothing)",
        "src/gofer/server.py:28 _spare",
    ]


def test_an_allowlist_line_for_a_name_its_file_lacks_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allowlist = "src/gofer/ui/server.py:no_such_name # test\n"
    root = _tree(tmp_path, {"scripts/dead-code-allowlist.txt": allowlist})
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == (
        "scripts/dead-code-allowlist.txt:1 src/gofer/ui/server.py:no_such_name "
        "(allowlist line matches nothing)"
    )
    # The same line passes once its file names it, and fails again when the file goes.
    (root / "src/gofer/ui").mkdir(parents=True)
    (root / "src/gofer/ui/server.py").write_text("# no_such_name\n", encoding="utf-8")
    assert _run(check_dead_code, root, capsys)[0] == 0
    (root / "src/gofer/ui/server.py").unlink()
    assert _run(check_dead_code, root, capsys)[0] == 1


def test_stale_route_and_css_allowlist_lines_fail_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _route_tree(tmp_path, {"POST /api/hooks/*/trigger": {"excluded": "webhook-ingress"}})
    (root / "frontend/src/styles").mkdir(parents=True)
    (root / "frontend/src/styles/app.css").write_text(".xterm-viewport { gap: 0; }\n", "utf-8")
    allowlist = root / "scripts/dead-code-allowlist.txt"
    allowlist.parent.mkdir(exist_ok=True)
    live = (
        "docs/cli-parity.json:POST /api/hooks/*/trigger  # outside services call it\n"
        ".xterm-*  # xterm.js builds the terminal's DOM\n"
        "frontend/src/styles/app.css:.xterm-view*  # xterm.js again\n"
        "_never_written  # a bare name isn't checked\n"
    )
    allowlist.write_text(live, encoding="utf-8")
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")

    stale = (
        "docs/cli-parity.json:POST /api/workflows/*/webhooks/*/trigger  # gone\n"
        ".lucide-*  # no icon styles left\n"
        "frontend/src/styles/other.css:.xterm-*  # stylesheet gone\n"
        "frontend/src/styles/app.css:.lucide  # not in this stylesheet\n"
    )
    allowlist.write_text(live + stale, encoding="utf-8")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[:4] == [
        f"scripts/dead-code-allowlist.txt:{number} {line.split('  #')[0]} "
        "(allowlist line matches nothing)"
        for number, line in enumerate(stale.splitlines(), 5)
    ]


def test_an_allowlist_line_without_a_reason_is_an_error(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, {"scripts/dead-code-allowlist.txt": "_spare\n"})
    assert check_dead_code.main(["--root", str(root)]) == 2
    assert "needs a name and '# reason'" in capsys.readouterr().err


def test_the_repository_allowlist_gives_a_reason_on_every_line(check_dead_code: Any) -> None:
    check_dead_code.read_allowlist(ROOT / check_dead_code.ALLOWLIST)


def test_a_src_function_only_tests_call_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(
        tmp_path,
        {"tests/test_parse.py": "from gofer.server import parse_port\n\nparse_port('1')\n"},
    )
    _append(root, "src/gofer/server.py", "\n\ndef parse_port(text):\n    return int(text)\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "src/gofer/server.py:28 parse_port (only tests use it)"
    assert "test seam:" in out


def test_a_call_from_src_makes_a_tested_function_used(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/test_parse.py": "from gofer.server import parse_port\n\nparse_port('1')\n",
            "src/gofer/config.py": (
                "from gofer.server import parse_port\n\nprint(parse_port('80'))\n"
            ),
        },
    )
    _append(root, "src/gofer/server.py", "\n\ndef parse_port(text):\n    return int(text)\n")
    assert _run(check_dead_code, root, capsys)[0] == 0


def test_only_a_test_seam_reason_excuses_code_only_tests_call(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    test_file = {"tests/test_cache.py": "from gofer.server import clear_cache\n\nclear_cache()\n"}
    seam = {"scripts/dead-code-allowlist.txt": "clear_cache  # Test seam: tests reset it\n"}
    root = _tree(tmp_path / "seam", {**test_file, **seam})
    _append(root, "src/gofer/server.py", "\n\ndef clear_cache():\n    return None\n")
    assert _run(check_dead_code, root, capsys)[0] == 0

    other = {"scripts/dead-code-allowlist.txt": "clear_cache  # plugins call it by name\n"}
    root = _tree(tmp_path / "other", {**test_file, **other})
    _append(root, "src/gofer/server.py", "\n\ndef clear_cache():\n    return None\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "src/gofer/server.py:28 clear_cache (only tests use it)"


def test_uses_inside_the_definition_itself_do_not_count(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(
        tmp_path,
        {"tests/test_walk.py": "from gofer.server import walk\n\nwalk([1, [2]])\n"},
    )
    _append(
        root,
        "src/gofer/server.py",
        "\n\ndef walk(items):\n"
        '    """Flatten items; walk calls itself for nested lists."""\n'
        "    return [x for i in items for x in (walk(i) if isinstance(i, list) else [i])]\n",
    )
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "src/gofer/server.py:28 walk (only tests use it)"

    # With no test either, it is plain unused code.
    (root / "tests/test_walk.py").unlink()
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "src/gofer/server.py:28 walk"


def test_a_frontend_export_only_unit_tests_use_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(
        tmp_path,
        {
            "frontend/src/lib/greet.test.mjs": (
                'import { greet, shout } from "./greet.js";\n\nshout(greet());\n'
            )
        },
    )
    shout = "\nexport function shout(text) {\n  return text;\n}\n"
    _append(root, "frontend/src/lib/greet.js", shout)
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "frontend/src/lib/greet.js:5 shout (only tests use it)"


PYPROJECT = """\
[project]
name = "example"
dependencies = [
    "PyYAML>=6.0",
    "typer[all]>=0.27",
    "tzdata>=2026.1",
    "unused-thing>=1.0",
]

[project.optional-dependencies]
devices = [
    "keyring==25.7.0",
    "tink==1.16.1",
]
dev = [
    "pytest>=9",
    "never-imported>=1",
]

[dependency-groups]
dev = ["pyinstaller>=6"]
"""
PYTHON_USES = {
    "src/gofer/config.py": "import yaml\nfrom typer import Typer\n\nTyper(yaml)\n",
    "scripts/secrets.py": "import keyring.backend\n",
    "README.md": "`python3 scripts/secrets.py` stores the keys.\n",
    "raticode.spec": 'datas = collect_data_files("tzdata")\n',
    "tests/test_tink.py": "import tink\n",
}


def test_a_python_dependency_nothing_imports_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The dev extra and the dev group are left out; raticode.spec naming a package counts.
    root = _tree(tmp_path, {"pyproject.toml": PYPROJECT, **PYTHON_USES})
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[:2] == [
        "pyproject.toml:7 unused-thing (dependency nothing uses)",
        "pyproject.toml:13 tink (dependency only tests import)",
    ]
    assert "never-imported" not in out and "pyinstaller" not in out

    allowlist = {
        "scripts/dead-code-allowlist.txt": (
            "pyproject.toml:unused-thing  # loaded by a plugin outside the repository\n"
            "pyproject.toml:tink  # the phone pairing tests need it in release builds\n"
        )
    }
    root = _tree(tmp_path / "allowed", {"pyproject.toml": PYPROJECT, **PYTHON_USES, **allowlist})
    assert _run(check_dead_code, root, capsys)[0] == 0


def test_a_dependency_imported_only_by_name_still_counts(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = '[project]\ndependencies = ["sherpa-onnx==2.0.1"]\n'
    loader = "import importlib\n\nspeech = importlib.import_module('sherpa_onnx')\n"
    root = _tree(tmp_path, {"pyproject.toml": pyproject, "src/gofer/speech.py": loader})
    assert _run(check_dead_code, root, capsys)[0] == 0

    # A mention in a comment or an ordinary string isn't an import.
    mention = "# sherpa_onnx would go here\nNAME = 'sherpa_onnx'\n"
    root = _tree(
        tmp_path / "mention", {"pyproject.toml": pyproject, "src/gofer/speech.py": mention}
    )
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "pyproject.toml:2 sherpa-onnx (dependency nothing uses)"


def test_a_frontend_dependency_nothing_names_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = """\
{
  "main": "electron/main.cjs",
  "scripts": {"dev": "wait-on http://localhost && vite", "types": "tsc --noEmit"},
  "dependencies": {
    "react": "^19.0.0",
    "monaco-editor": "^0.57.0"
  },
  "devDependencies": {
    "autoprefixer": "^10.6.2",
    "tailwindcss": "^4.0.0",
    "typescript": "^6.0.0",
    "vite": "^7.0.0",
    "wait-on": "^9.0.0"
  }
}
"""
    lock = (
        '{"packages": {"": {}, "node_modules/typescript": {"bin": {"tsc": "bin/tsc"}},'
        ' "node_modules/vendored": {"bin": {"autoprefixer": "x"}}}}\n'
    )
    root = _tree(
        tmp_path,
        {
            "frontend/package.json": manifest,
            "frontend/package-lock.json": lock,
            "frontend/src/main.jsx": (
                'import React from "react";\nimport "./lib/greet.js";\n'
                "import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';\n"
                "// autoprefixer used to run here\n"
            ),
            "frontend/src/styles/index.css": '@import "tailwindcss";\n',
            "frontend/vendor/autoprefixer/index.js": 'module.exports = "autoprefixer";\n',
        },
    )
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "frontend/package.json:9 autoprefixer (dependency nothing uses)"
    assert len(out.splitlines()) == 3


def test_raticode_lists_no_dependency_nothing_uses(check_dead_code: Any) -> None:
    """Every runtime package Raticode lists is imported, named by the spec, or run."""
    files = {
        path: text for path, text in check_dead_code.corpus(ROOT) if path.startswith("frontend/")
    }
    assert check_dead_code.unused_python_dependencies(ROOT) == []
    assert check_dead_code.unused_frontend_dependencies(ROOT, files) == []


KOTLIN_MAIN = "mobile/android/app/src/main"
KOTLIN_APP = f"{KOTLIN_MAIN}/java/dev/raticode/mobile"
# A phone app where every declaration is used, including the ones the check skips.
PHONE_TREE = {
    f"{KOTLIN_MAIN}/AndroidManifest.xml": (
        '<manifest><application><activity android:name=".MainActivity" />'
        '<receiver android:name=".live.AlertReceiver" /></application></manifest>\n'
    ),
    f"{KOTLIN_MAIN}/res/xml/settings.xml": '<Preference android:fragment="SettingsPane" />\n',
    "mobile/android/app/build.gradle.kts": 'android { namespace = "dev.raticode.mobile" }\n',
    f"{KOTLIN_APP}/MainActivity.kt": (
        "package dev.raticode.mobile\n"
        "\n"
        "class MainActivity : ComponentActivity() {\n"
        "    override fun onCreate(state: Bundle?) { Store.load(state); count(JSONObject()) }\n"
        "}\n"
        "\n"
        "internal object Store {\n"
        "    fun load(state: Bundle?) = state?.let(::parse)\n"
        "    private fun parse(state: Bundle) = state.size\n"
        "    operator fun invoke() = Unit\n"
        "}\n"
        "\n"
        "class SettingsPane\n"
        "\n"
        "@Composable\n"
        "@Preview(showBackground = true)\n"
        "private fun MainPreview() {}\n"
        "\n"
        "internal fun JSONObject.integer(key: String): Long = getLong(key)\n"
        'fun count(json: JSONObject) = json.integer("n")\n'
    ),
    f"{KOTLIN_APP}/live/AlertReceiver.kt": (
        "package dev.raticode.mobile.live\n\nclass AlertReceiver : BroadcastReceiver()\n"
    ),
    "mobile/android/app/src/test/java/dev/raticode/mobile/StoreTest.kt": (
        "class StoreTest { @Test fun counts() = assertEquals(1, count(JSONObject())) }\n"
    ),
}


def test_a_phone_tree_where_everything_is_used_passes(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Covers the skips: override, operator, @Preview, manifest and XML names.
    code, out = _run(check_dead_code, _tree(tmp_path, PHONE_TREE), capsys)
    assert (code, out.strip()) == (0, "check-dead-code: nothing unused")


def test_an_uncalled_phone_function_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, PHONE_TREE)
    _append(root, f"{KOTLIN_APP}/MainActivity.kt", "\ninternal fun deadPhoneHelper() = 1\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == f"{KOTLIN_APP}/MainActivity.kt:22 deadPhoneHelper"


def test_an_unused_phone_class_and_object_member_fail_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, PHONE_TREE)
    _append(
        root,
        f"{KOTLIN_APP}/live/AlertReceiver.kt",
        "\ndata class Stale(val n: Int)\n\nobject Keys {\n    const val A = 1\n"
        "    fun <T> List<T>.second(): T = this[1]\n}\n",
    )
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[:3] == [
        f"{KOTLIN_APP}/live/AlertReceiver.kt:5 Stale",
        f"{KOTLIN_APP}/live/AlertReceiver.kt:7 Keys",
        f"{KOTLIN_APP}/live/AlertReceiver.kt:9 second",
    ]


def test_a_phone_function_only_tests_call_needs_a_test_seam_reason(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    helper = '\nfun syncText(n: Int) = "$n to push"\n'
    uses = {
        "mobile/android/app/src/androidTest/java/dev/raticode/mobile/SyncTest.kt": (
            'class SyncTest { @Test fun words() = assertEquals("1 to push", syncText(1)) }\n'
        )
    }
    root = _tree(tmp_path / "with", {**PHONE_TREE, **uses})
    _append(root, f"{KOTLIN_APP}/MainActivity.kt", helper)
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == f"{KOTLIN_APP}/MainActivity.kt:22 syncText (only tests use it)"

    seam = {"scripts/dead-code-allowlist.txt": "syncText  # test seam: the sync test reads it\n"}
    root = _tree(tmp_path / "seam", {**PHONE_TREE, **uses, **seam})
    _append(root, f"{KOTLIN_APP}/MainActivity.kt", helper)
    assert _run(check_dead_code, root, capsys)[0] == 0


def test_phone_build_output_does_not_count_as_a_use(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    generated = {"mobile/android/app/build/generated/Use.kt": "deadPhoneHelper()\n"}
    root = _tree(tmp_path, {**PHONE_TREE, **generated})
    _append(root, f"{KOTLIN_APP}/MainActivity.kt", "\ninternal fun deadPhoneHelper() = 1\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == f"{KOTLIN_APP}/MainActivity.kt:22 deadPhoneHelper"


# A preload with the shapes the bridge pass must read past: strings, a regex and a
# template literal holding braces, a comment, a nested object and a method.
BRIDGE_PRELOAD = """const { contextBridge, ipcRenderer } = require("electron");
const pattern = /^[{}]+$/;
contextBridge.exposeInMainWorld("goferApiBaseUrl", "http://127.0.0.1:8765");
contextBridge.exposeInMainWorld("goferUpdates", {
  check: () => ipcRenderer.invoke("gofer:check-for-updates"),
  // a closing brace in a comment: }
  label: `ready ${ { a: "}" }.a }`,
  match: (value) => /[}]/.test(value) && pattern.test("{"),
  nested: { inner: 1 },
  async getState() { return ipcRenderer.invoke("gofer:get-update-state"); },
  openRelease: () => ipcRenderer.invoke("gofer:open-update-release"),
});
"""
BRIDGE_TREE = {
    "frontend/electron/main.cjs": (
        "require('./ipc-handlers.cjs');\nconst pages = ['preload.cjs', 'error-preload.cjs'];\n"
    ),
    "frontend/electron/preload.cjs": BRIDGE_PRELOAD,
    "frontend/electron/ipc-handlers.cjs": (
        "const updateIpcHandlers = [\n"
        '  ["gofer:check-for-updates", "checkForUpdates"],\n'
        '  ["gofer:get-update-state", "getUpdateState"],\n'
        '  ["gofer:open-update-release", "openUpdateRelease"],\n'
        "];\nmodule.exports = { updateIpcHandlers };\n"
    ),
    "frontend/electron/error-preload.cjs": (
        'contextBridge.exposeInMainWorld("goferBackend", { restart: () => 1 });\n'
    ),
    "frontend/electron/backend-error.html": "<button onclick='goferBackend.restart()'>\n",
    "frontend/src/main.jsx": (
        'import { greet } from "./lib/greet.js";\n\ngreet();\n'
        "const u = window.goferUpdates;\n"
        "u.check(); u.label; u.match(''); u.nested; u.getState();\n"
    ),
    # A test that lists the bridge's keys doesn't count as calling them.
    "frontend/src/pages/App.test.mjs": '["check", "getState", "openRelease"];\n',
}


def test_a_bridge_key_only_a_test_names_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(check_dead_code, _tree(tmp_path, BRIDGE_TREE), capsys)
    assert code == 1
    assert out.splitlines()[0] == (
        "frontend/electron/preload.cjs:11 openRelease (bridge key the app never calls)"
    )
    assert out.count("(bridge key") == 1


def test_a_bridge_key_the_page_calls_passes(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, BRIDGE_TREE)
    _append(root, "frontend/src/main.jsx", "u.openRelease();\n")
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_an_ipc_channel_no_preload_names_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    preload = BRIDGE_PRELOAD.replace(
        '  openRelease: () => ipcRenderer.invoke("gofer:open-update-release"),\n', ""
    )
    root = _tree(tmp_path, {**BRIDGE_TREE, "frontend/electron/preload.cjs": preload})
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == (
        "frontend/electron/ipc-handlers.cjs:4 gofer:open-update-release "
        "(IPC channel no preload names)"
    )


def _electron_files() -> dict[str, str]:
    files = {}
    for folder in ("frontend/src", "frontend/electron"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "node_modules" not in path.parts:
                files[path.relative_to(ROOT).as_posix()] = path.read_text("utf-8", "replace")
    return files


def test_raticode_bridge_has_no_key_or_channel_nothing_uses(check_dead_code: Any) -> None:
    files = _electron_files()
    keys = check_dead_code.bridge_keys(check_dead_code.preload_files(files))
    assert {"goferUpdates", "onState", "check", "restart", "run"} - {k.name for k in keys} == {
        "goferUpdates"
    }
    assert [str(finding) for finding in check_dead_code.unused_bridge(files)] == []

    preload = "frontend/electron/preload.cjs"
    anchor = 'contextBridge.exposeInMainWorld("goferUpdates", {\n'
    assert anchor in files[preload]
    files[preload] = files[preload].replace(anchor, anchor + "  neverCalledKey: () => 1,\n")
    assert [f.name for f in check_dead_code.unused_bridge(files)] == ["neverCalledKey"]


# Routes: each one without a CLI command must be named by app code outside the server.
ROUTES = {
    "GET /api/workflows": {"cli": "list"},
    "GET /api/greetings/*": {"excluded": "window-ui"},
    "POST /api/swarms/*/start": {"excluded": "runs-agents"},
    "GET /api/workflows/*/logs/*": {"excluded": "window-ui"},
    "POST /api/devices#pair": {"excluded": "pairing"},
}
ROUTE_TREE = {
    "frontend/src/lib/greetings.js": (
        'import { apiUrl } from "./api.js";\n\n'
        "export const greeting = id => fetch(apiUrl(`/greetings/${id}`));\n"
        "const swarm = (id, suffix) => fetch(apiUrl(`/swarms/${id}${suffix}`));\n"
        'export const start = id => swarm(id, "/start");\n'
    ),
    "frontend/src/lib/api.js": "export const apiUrl = path => `/api${path}`;\n",
    "frontend/src/main.jsx": (
        'import { greet } from "./lib/greet.js";\n'
        'import { greeting, start } from "./lib/greetings.js";\n\n'
        "greet(); greeting(1); start(2);\n"
    ),
    "frontend/electron/main.cjs": "const path = require('node:path');\nsend('pair');\n",
    "src/gofer/client.py": 'logs_path = "/workflows/{workflow_id}/logs/{run_id}"\n',
    "tests/test_client.py": "PATHS = []\n",
}


def _route_tree(root: Path, routes: dict[str, Any]) -> Path:
    manifest = json.dumps({"routes": routes}, indent=2) + "\n"
    return _tree(root, {**ROUTE_TREE, "docs/cli-parity.json": manifest})


def test_routes_the_apps_call_pass(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A whole path, apiUrl's /api, path pieces a helper joins and a quoted action.
    root = _route_tree(tmp_path, ROUTES)
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_a_planted_route_nothing_calls_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    planted = {
        **ROUTES,
        "GET /api/session": {"excluded": "desktop-auth"},
        "POST /api/devices#unpair": {"excluded": "pairing"},
    }
    root = _route_tree(tmp_path, planted)
    # The server's own route table and a test don't count as callers.
    (root / "src/gofer/ui").mkdir(parents=True)
    (root / "src/gofer/ui/server.py").write_text(
        'ROUTES = ["/api/session", "unpair"]\n', encoding="utf-8"
    )
    _append(root, "tests/test_client.py", 'PATHS = ["/api/session", "unpair"]\n')
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[:2] == [
        "docs/cli-parity.json:18 GET /api/session (route nothing calls)",
        "docs/cli-parity.json:21 POST /api/devices#unpair (route nothing calls)",
    ]


def test_a_route_needs_both_path_pieces_from_the_apps(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # "/start" alone isn't enough: nothing joins it to /swarms, and Python pieces don't count.
    root = _route_tree(tmp_path, {"POST /api/runs/*/start": {"excluded": "runs-agents"}})
    _append(root, "src/gofer/client.py", 'runs_path = "/runs"\n')
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == (
        "docs/cli-parity.json:3 POST /api/runs/*/start (route nothing calls)"
    )


def test_a_route_outside_api_fails_even_when_the_apps_name_its_path(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The app's `/reports/${id}/summary` goes through apiUrl, so it calls the /api twin;
    # the page outside /api/ is reported anyway, and only the allowlist keeps it.
    root = _route_tree(
        tmp_path,
        {
            "GET /api/reports/*/summary": {"excluded": "window-ui"},
            "GET /reports/*/summary": {"excluded": "window-ui"},
        },
    )
    _append(
        root,
        "frontend/src/lib/greetings.js",
        "fetch(apiUrl(`/reports/${greetingId}/summary`));\n",
    )
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == (
        "docs/cli-parity.json:6 GET /reports/*/summary (route outside /api/)"
    )
    assert "GET /api/reports/*/summary" not in out

    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/dead-code-allowlist.txt").write_text(
        "docs/cli-parity.json:GET /reports/*/summary  # a browser opens the page directly\n",
        encoding="utf-8",
    )
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_an_allowlisted_route_passes_even_with_an_action(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _route_tree(
        tmp_path,
        {"GET /api/hooks/*/trigger": {"excluded": "webhook-ingress"}, "POST /api/x#go": {}},
    )
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/dead-code-allowlist.txt").write_text(
        "docs/cli-parity.json:GET /api/hooks/*/trigger  # outside services call it\n"
        "docs/cli-parity.json:POST /api/x#go  # a reason after an action key\n",
        encoding="utf-8",
    )
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


# CSS classes: each class selector in a frontend stylesheet must be named by page code.
CSS_TREE = {
    "frontend/src/styles/app.css": (
        "/* .commented-out { color: red; } */\n"
        ".greeting, .hover\\:bg-sky-200:hover { margin: .5rem; }\n"
        '.greeting[data-tone="a.b"] { content: ".quoted {"; }\n'
        "@media (min-width: 40.5rem) {\n"
        "  .greeting { padding: 0; }\n"
        "}\n"
        ".bg-\\[\\#fff\\] { color: #fff; }\n"
    ),
    "frontend/src/main.jsx": (
        'import { greet } from "./lib/greet.js";\n'
        'import "./styles/app.css";\n\n'
        'greet();\nconst view = <p className="greeting hover:bg-sky-200 bg-[#fff]" />;\n'
    ),
}


def test_stylesheet_classes_the_page_names_pass(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Comments, strings, attribute values, at-rule preludes and values such as .5rem
    # aren't classes; escaped variant and arbitrary-value classes match as written.
    root = _tree(tmp_path, CSS_TREE)
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_a_css_class_nothing_uses_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, CSS_TREE)
    _append(root, "frontend/src/styles/app.css", ".greeting .stale-card { gap: 2px; }\n")
    # A test, another stylesheet and a backslash escape before ${ don't count as uses.
    (root / "frontend/src/main.test.mjs").write_text('const name = "stale-card";\n', "utf-8")
    (root / "frontend/src/other.css").write_text(".stale-card + .greeting {}\n", "utf-8")
    _append(root, "frontend/src/main.jsx", "const word = new RegExp(`\\\\stale-${name}`);\n")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[:2] == [
        "frontend/src/other.css:1 .stale-card (CSS class nothing uses)",
        "frontend/src/styles/app.css:8 .stale-card (CSS class nothing uses)",
    ]


def test_a_css_class_built_from_a_template_passes(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path, CSS_TREE)
    _append(root, "frontend/src/styles/app.css", ".oh-b3, .st-running { color: red; }\n")
    _append(
        root,
        "frontend/src/main.jsx",
        "const block = i => <i className={`oh-b${i % 6} st-${state}`} />;\n",
    )
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_an_allowlisted_css_class_passes(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(
        tmp_path,
        {
            **CSS_TREE,
            "scripts/dead-code-allowlist.txt": (
                ".xterm-*  # xterm.js builds the terminal's DOM\n"
                "frontend/src/styles/app.css:.lucide  # lucide-react puts it on each icon\n"
            ),
        },
    )
    _append(root, "frontend/src/styles/app.css", ".xterm-viewport, svg.lucide { gap: 0; }\n")
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")
    # A path-scoped line covers only that stylesheet.
    (root / "frontend/src/icons.css").write_text("svg.lucide { width: 1em; }\n", "utf-8")
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "frontend/src/icons.css:1 .lucide (CSS class nothing uses)"


def test_a_script_no_other_file_names_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Its own usage line and the allowlist don't count as naming it.
    root = _tree(tmp_path, {"scripts/x.py": '"""Run: python3 scripts/x.py"""\n'})
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == "scripts/x.py:1 x.py (script nothing runs)"
    # A longer name that contains it isn't it either.
    (root / "README.md").write_text("Run `python3 scripts/old-x.py`.\n", "utf-8")
    assert _run(check_dead_code, root, capsys)[0] == 1
    # The root Markdown, docs/ and benchmarks/ count, ending a sentence too.
    for relative in ("README.md", "docs/guide.md", "benchmarks/README.md"):
        for other in ("README.md", "docs/guide.md", "benchmarks/README.md"):
            (root / other).unlink(missing_ok=True)
        (root / relative).parent.mkdir(exist_ok=True)
        (root / relative).write_text("Run `python3 scripts/x.py`. Or x.py.\n", "utf-8")
        assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_a_browser_test_nothing_runs_fails_the_check(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests = "frontend/browser-tests"
    root = _tree(
        tmp_path,
        {
            f"{tests}/x.browser.cjs": "// Run: electron x.browser.cjs\n",
            # A unit test naming it doesn't run it.
            "frontend/electron/tests/x.test.cjs": 'require("../../browser-tests/x.browser.cjs");\n',
            "tests/test_x.py": 'X = "x.browser.cjs"\n',
            # Nothing runs a run-*.cjs runner either; its own name doesn't count.
            f"{tests}/run-y.cjs": 'run(path.join(__dirname, "y.browser.cjs")); // run-y.cjs\n',
            f"{tests}/y.browser.cjs": "",
            # *.test.cjs files aren't checked: run-tests.cjs picks them all up.
            "frontend/electron/tests/run-z.test.cjs": "",
        },
    )
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[:2] == [
        f"{tests}/run-y.cjs:1 run-y.cjs (script nothing runs)",
        f"{tests}/x.browser.cjs:1 x.browser.cjs (script nothing runs)",
    ]
    # A package.json script runs the runner; a browser test it runs requires x.
    scripts = {
        "test:y": "node browser-tests/run-y.cjs",
        "test:studio": "electron browser-tests/studio.browser.cjs",
    }
    manifest = {"main": "electron/main.cjs", "scripts": scripts}
    (root / "frontend/package.json").write_text(json.dumps(manifest), "utf-8")
    (root / tests / "studio.browser.cjs").write_text('require("./x.browser.cjs");\n', "utf-8")
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")


def test_a_browser_test_a_runner_lists_by_short_name_passes(
    check_dead_code: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = (
        'for (const script of ["file-media", "y"]) {\n'
        "  await runBrowserTest(path.join(__dirname, `${script}.browser.cjs`));\n"
        "}\n"
    )
    root = _tree(
        tmp_path,
        {
            "frontend/package.json": (
                '{"main": "electron/main.cjs", '
                '"scripts": {"test:browser": "node ./browser-tests/run-studio-browser.cjs"}}\n'
            ),
            "frontend/browser-tests/run-studio-browser.cjs": runner,
            "frontend/browser-tests/y.browser.cjs": "",
        },
    )
    assert _run(check_dead_code, root, capsys) == (0, "check-dead-code: nothing unused\n")
    # The short name has to be a whole quoted string: "y-other" or a bare y doesn't count.
    (root / "frontend/browser-tests/run-studio-browser.cjs").write_text(
        runner.replace('"y"', '"y-other", y'), "utf-8"
    )
    code, out = _run(check_dead_code, root, capsys)
    assert code == 1
    assert out.splitlines()[0] == (
        "frontend/browser-tests/y.browser.cjs:1 y.browser.cjs (script nothing runs)"
    )


def test_raticode_has_no_script_nothing_runs(check_dead_code: Any) -> None:
    found = [
        str(finding)
        for finding in check_dead_code.unrun_files(ROOT, dict(check_dead_code.corpus(ROOT)))
    ]
    assert found == []
