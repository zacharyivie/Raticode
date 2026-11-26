#!/usr/bin/env python3
"""Fail when a function, class, constant, export, module, dependency or CSS class has no users.

The check looks for ten kinds of unused code:

- module-level functions, classes and methods in ``src/gofer`` whose names appear
  nowhere except on the line that defines them
- UPPER_CASE module-level constants in ``src/gofer`` (an assignment or an annotated
  assignment such as ``LIMIT = 5`` or ``NAMES: tuple[str, ...] = ...``) whose names
  appear nowhere except in that assignment
- names exported from ``frontend/src`` and ``frontend/electron`` (test files left
  out) with ``export function``, ``export const`` or ``export class`` whose names
  appear nowhere except in that declaration
- modules in ``frontend/src`` and ``frontend/electron`` that no other file imports
- production code only tests call: a ``src/gofer`` definition or constant, or a frontend export
  whose name appears outside its own definition only in test files (``tests/``,
  ``*.test.*``, ``*.browser.*``, ``frontend/browser-tests``, ``frontend/electron/tests``).
  These print with ``(only tests use it)``. A definition only its own body names,
  such as a recursive function nothing else calls, prints as unused.
- dependencies nothing uses. A package in ``[project].dependencies`` or the
  ``devices`` and ``xlsx`` extras of ``pyproject.toml`` must be imported, or named
  in ``raticode.spec``, somewhere under ``src/gofer``, ``scripts`` or ``packaging``;
  imports in ``tests/`` don't count, so a package only tests import prints with
  ``(dependency only tests import)`` and belongs in the dev extra. Distribution
  names map to module names through ``importlib.metadata.packages_distributions()``
  and ``IMPORT_NAMES``. A package in ``frontend/package.json`` ``dependencies`` or
  ``devDependencies`` must be named as a quoted module in a frontend source, test
  or config file, or run (by its name or a command it installs) in a
  ``package.json`` script. The dev extra and the dev group aren't checked.
- the phone's Kotlin: a ``fun``, ``class`` or ``object`` in
  ``mobile/android/app/src/main``, top-level or member, whose name appears
  nowhere but its declaration in the Kotlin, XML, Gradle and R8 rules (``.pro``)
  files under ``mobile/android``. A name only the test source sets
  (``src/test``, ``src/androidTest``, ``src/testShared``) mention prints with
  ``(only tests use it)``. An ``override`` or ``operator`` fun, an ``@Preview``
  function and a name ``AndroidManifest.xml`` mentions are skipped, since Android,
  Kotlin syntax or Android Studio call those.
- the Electron bridge. Each top-level key of an object a
  ``frontend/electron/*preload*.cjs`` file passes to
  ``contextBridge.exposeInMainWorld`` must appear as a word in a non-test file under
  ``frontend/src`` or an ``.html`` file under ``frontend/electron``; a test that lists
  the bridge's keys doesn't count. These print with
  ``(bridge key the app never calls)``. Each channel in the ``ipc-handlers.cjs`` table
  must be named, quoted, in a preload file, or it prints with
  ``(IPC channel no preload names)``.
- backend routes no app calls. Each route in ``docs/cli-parity.json`` without a ``cli``
  entry must be named in non-test code under ``frontend/src``, ``frontend/electron``,
  ``mobile/android/app/src/main`` or ``src/gofer`` (``ui/server.py`` and ``ui/routes.py``,
  which define the routes, left out): a ``#action`` key by its quoted action, a path by its last two
  literal segments (``workflows/${id}/logs`` for ``GET /api/workflows/*/logs/*``). A
  leading ``api`` counts as written, since ``apiUrl`` adds it, and the apps' request
  helpers may write the two segments as separate path pieces (`` `/swarms${path}` ``
  and ``"/start"``). These print with ``(route nothing calls)``. A route whose path
  doesn't start with ``/api/`` prints with ``(route outside /api/)`` unless it's
  allowlisted: its calls look the same as calls to its ``/api`` twin, since
  ``apiUrl`` adds ``/api``.
- CSS classes nothing uses. Each class selector in a ``.css`` file under
  ``frontend/src`` or ``frontend/electron`` must appear as a word in a non-test,
  non-CSS file under those folders or in ``frontend/index.html``, or be built from
  a template there: some prefix of the name followed by ``${``, as ``st-`` is in
  ``st-${state}``. These print as ``path:line .name (CSS class nothing uses)``.

A name counts as used when it appears as a word in any file under ``SEARCH_PATHS``:
code, tests (including ``frontend/browser-tests`` and ``frontend/electron/tests``),
scripts, ``package.json`` scripts, packaging, Rattish, skills and the website docs.
Comments and strings count too, so a name reached only by ``getattr`` or a route
table still counts once it is written down somewhere.

Framework overrides (``do_*``, ``handle_*``, ``on_any_event``, ``log_message``,
``redirect_request``), ``__dunder__`` methods and decorated functions are skipped.
Any other deliberate exception goes in ``scripts/dead-code-allowlist.txt``, one per
line with a reason, as Vulture does with its whitelist::

    name  # reason
    src/gofer/module.py:name  # reason, for one file only
    pyproject.toml:package-name  # reason, for a Python dependency
    frontend/package.json:package-name  # reason, for a frontend dependency
    docs/cli-parity.json:GET /api/route/*  # reason, for a backend route
    .xterm-*  # reason, for CSS classes; a CSS line may use * and [...] patterns

A line excuses code only tests call when its reason starts with ``test seam:``, so
each helper kept for tests says so::

    reset_for_tests  # test seam: tests reset the module's state between cases

Each finding prints as ``path:line name``. The script needs only the standard library.

Usage:
    python3 scripts/check-dead-code.py
    python3 scripts/check-dead-code.py --root path/to/checkout

Exit codes: 0 when nothing is unused, 1 when anything is, 2 when the allowlist has a
line without a reason.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import sys
import tomllib
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = "scripts/dead-code-allowlist.txt"

# Where a name counts as used. Missing paths are skipped.
SEARCH_PATHS = (
    "src",
    "tests",
    "scripts",
    "frontend",
    "packaging",
    "rattish",
    "skills",
    "website/docs",
    "website/package.json",
    "pyproject.toml",
    "raticode.spec",
    ".github",
)
SKIP_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "cache",
        "dist",
        "node_modules",
        "release",
    }
)
BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".icns", ".woff", ".woff2", ".ttf"}
    | {".otf", ".mp3", ".mp4", ".wav", ".ogg", ".webm", ".zip", ".gz", ".pdf", ".onnx"}
)

FRAMEWORK_PREFIXES = ("do_", "handle_")
FRAMEWORK_NAMES = frozenset({"on_any_event", "log_message", "redirect_request"})

FRONTEND_DIRS = ("frontend/src", "frontend/electron")
MODULE_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"})
TEST_DIRS = frozenset({"tests", "browser-tests", "__tests__"})
TEST_SEAM = "test seam:"
ONLY_TESTS = "(only tests use it)"

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A module constant's name: capitals, digits and underscores, with at least one capital.
CONSTANT_NAME = re.compile(r"_*[A-Z][A-Z0-9_]*")
EXPORT = re.compile(
    r"^[ \t]*export[ \t]+(?:async[ \t]+)?(?:function\*?|const|let|var|class)[ \t]+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
# A file name with a script extension anywhere in a file: an import, require(),
# new URL(), a preload path, an HTML <script>, an electron-builder entry.
MODULE_FILE_NAME = re.compile(r"([\w.-]+\.(?:mjs|cjs|jsx|tsx|js|ts))(?![\w.])")
# The last part of an import path written without an extension (Vite resolves those).
IMPORT_PATH = re.compile(r"""(?:\bfrom|\bimport|\brequire)\s*\(?\s*["'`]([^"'`\n]+)["'`]""")

# The dependency pass. Python packages in [project].dependencies and these extras must
# be imported (or named by raticode.spec) outside tests; the dev extra and the dev
# group are left out, since only tests and tools use them.
PYTHON_EXTRAS = ("devices", "xlsx")
PYTHON_USE_PATHS = ("src/gofer", "scripts", "packaging", "raticode.spec")
# Modules a distribution installs, when it isn't installed for the check to ask and
# its module isn't its name with '-' as '_'. Keys are normalized distribution names.
IMPORT_NAMES = {"pyyaml": ("yaml",), "pyopenssl": ("OpenSSL",)}
# Calls that load a module named by their first argument.
DYNAMIC_IMPORTS = frozenset({"import_module", "find_spec", "__import__"})
REQUIREMENT_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
FRONTEND_MANIFEST = "frontend/package.json"
FRONTEND_LOCK = "frontend/package-lock.json"
FRONTEND_VENDOR = "frontend/vendor/"
SHELL_WORD = re.compile(r"""[\s;&|()"'`]+""")
DEPENDENCY_UNUSED = "(dependency nothing uses)"
DEPENDENCY_TESTS_ONLY = "(dependency only tests import)"

# The phone's Kotlin pass. Declarations come from KOTLIN_MAIN; a name counts as used
# when it appears in a Kotlin, XML, Gradle or R8 rules file anywhere under KOTLIN_ROOT.
KOTLIN_ROOT = "mobile/android"
KOTLIN_MAIN = "mobile/android/app/src/main"
KOTLIN_USE_SUFFIXES = frozenset({".kt", ".kts", ".xml", ".gradle", ".pro"})
KOTLIN_SKIP_DIRS = SKIP_DIRS | {"build", ".gradle", ".idea", ".kotlin"}
# Android and Kotlin call these by convention rather than by a name in the source.
KOTLIN_SKIP_MODIFIERS = frozenset({"override", "operator"})
KOTLIN_MODIFIER = (
    r"(?:public|private|protected|internal|open|abstract|final|override|operator|infix"
    r"|inline|tailrec|suspend|external|data|enum|sealed|inner|value|annotation|companion)"
)
KOTLIN_ANNOTATION = r"@[\w.:]+(?:\((?:[^()]|\([^()]*\))*\))?"
KOTLIN_DECLARATION = re.compile(
    rf"^\s*(?P<prefix>(?:(?:{KOTLIN_ANNOTATION}|{KOTLIN_MODIFIER})\s+)*)"
    r"(?P<kind>fun|class|object)\s+(?:<[^<>]*>\s*)?"
    r"(?:[\w.]+(?:<[^<>]*>)?\??\.)?(?P<name>[A-Za-z_]\w*)"
)
KOTLIN_ANNOTATION_NAME = re.compile(r"@(?:[\w]+\.)*(\w+)")

# The Electron bridge pass. Each key of an object a preload file exposes with
# contextBridge.exposeInMainWorld must be named by the page: a non-test file under
# frontend/src, or an HTML page under frontend/electron. Each channel in the
# ipc-handlers.cjs table must be named by a preload file.
ELECTRON_DIR = "frontend/electron"
PRELOAD_NAME = re.compile(r"[^/]*preload[^/]*\.cjs")
IPC_TABLE = "frontend/electron/ipc-handlers.cjs"
IPC_ROW = re.compile(r"""^\s*\[\s*["']([\w:.-]+)["']\s*,\s*["']\w+["']\s*\]""", re.MULTILINE)
EXPOSE_CALL = re.compile(r"\bcontextBridge\s*\.\s*exposeInMainWorld\s*\(\s*([\"'])[^\"']*\1\s*,\s*")
BRIDGE_UNUSED = "(bridge key the app never calls)"
CHANNEL_UNUSED = "(IPC channel no preload names)"
# A '/' after one of these characters (or a keyword) starts a regex literal, not a division.
REGEX_AFTER = frozenset("(,=:[!&|?{};+-*%<>~^")
REGEX_KEYWORDS = frozenset({"return", "typeof", "case", "in", "of", "void", "delete", "new"})

# The route pass. Each route in the parity manifest that no CLI command covers must be
# named by a caller: non-test code in ROUTE_CALLER_DIRS, the route tables left out.
PARITY_MANIFEST = "docs/cli-parity.json"
ROUTE_CALLER_DIRS = ("frontend/src", "frontend/electron", KOTLIN_MAIN, "src/gofer")
# The files that define the routes, whose own paths don't count as calls.
ROUTE_TABLES = frozenset({"src/gofer/ui/server.py", "src/gofer/ui/routes.py"})
ROUTE_UNUSED = "(route nothing calls)"
# A route outside /api/ is written the same way as its /api twin, which apiUrl reaches,
# so no caller can show it's used; only the allowlist keeps one.
ROUTE_OUTSIDE_API = "(route outside /api/)"
# What can stand for a '*' segment between two literal ones: an id written into the
# path, a ${...} template part, or a string concatenation, on one line.
ROUTE_WILDCARD = r"[^\n]{1,160}?"

# The CSS class pass. Each class selector in a stylesheet under FRONTEND_DIRS must be
# named by a page file: non-test, non-CSS code under FRONTEND_DIRS or CSS_PAGE_FILES.
CSS_PAGE_FILES = frozenset({"frontend/index.html"})
CSS_UNUSED = "(CSS class nothing uses)"
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
CSS_STRING = re.compile(r""""(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'""")
# An attribute selector; an escaped bracket belongs to a class name such as `.bg-\[\#fff\]`.
CSS_ATTRIBUTE = re.compile(r"(?<!\\)\[[^\]\n]*\]")
CSS_CLASS = re.compile(r"\.(-?[A-Za-z_](?:[\w-]|\\.)*)")
# A class name as code writes it: a run of word characters and hyphens, or a whole
# space- or quote-delimited token for a utility such as `hover:bg-sky-200` or `bg-white/70`.
CLASS_WORD = re.compile(r"[\w-]+")
CLASS_TOKEN = re.compile(r"""[^\s"'`{}()<>,;]+""")
# The literal part of a class built from a template, such as `st-` in `st-${state}`.
# A run after a backslash is an escape (`\\b${word}` in a RegExp), not a class.
TEMPLATE_PREFIX = re.compile(r"(?<![\w\\-])([\w-]+)\$\{")


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    name: str
    note: str = ""
    # The last line of a Python definition; uses inside it don't count for the
    # production pass. Zero for frontend exports, whose declaration line is the span.
    end: int = field(default=0, compare=False)

    def __str__(self) -> str:
        text = f"{self.path}:{self.line} {self.name}"
        return f"{text} {self.note}" if self.note else text


class AllowlistError(ValueError):
    pass


def _is_text(path: Path, data: bytes) -> bool:
    return path.suffix.lower() not in BINARY_SUFFIXES and b"\0" not in data[:4096]


def corpus(root: Path, skip: Iterable[Path] = ()) -> Iterator[tuple[str, str]]:
    """Yield (relative path, text) for every text file under SEARCH_PATHS."""
    skipped = {path.resolve() for path in skip}
    for entry in SEARCH_PATHS:
        start = root / entry
        if start.is_file():
            files: Iterable[Path] = [start]
        elif start.is_dir():
            files = _walk(start)
        else:
            continue
        for path in files:
            if path.resolve() in skipped:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if _is_text(path, data):
                yield path.relative_to(root).as_posix(), data.decode("utf-8", "replace")


def _walk(start: Path) -> Iterator[Path]:
    # os.walk doesn't follow symlinked folders, such as a worktree's node_modules link.
    for folder, dirs, files in os.walk(start):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            yield Path(folder, name)


def _skipped_python_name(node: ast.AST) -> bool:
    if isinstance(node, ast.ClassDef):
        return False
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    name = node.name
    return (
        bool(node.decorator_list)
        or (name.startswith("__") and name.endswith("__"))
        or name.startswith(FRAMEWORK_PREFIXES)
        or name in FRAMEWORK_NAMES
    )


def _constant_names(node: ast.stmt) -> list[str]:
    """UPPER_CASE names a module-level assignment binds, ``A = B = 1`` and ``A, B = 1, 2`` too."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets = [node.target]
    else:
        return []
    names = []
    for target in targets:
        items = target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]
        names += [
            item.id
            for item in items
            if isinstance(item, ast.Name) and CONSTANT_NAME.fullmatch(item.id)
        ]
    return names


def python_definitions(root: Path) -> list[Finding]:
    """Module-level functions, classes and constants, and the methods of those classes."""
    found: list[Finding] = []
    for path in sorted((root / "src/gofer").rglob("*.py")):
        if SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        definition = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        for node in tree.body:
            end = node.end_lineno or node.lineno
            found += [
                Finding(relative, node.lineno, name, end=end) for name in _constant_names(node)
            ]
            if not isinstance(node, definition):
                continue
            nodes = [node]
            if isinstance(node, ast.ClassDef):
                nodes += [child for child in node.body if isinstance(child, definition[:2])]
            found += [
                Finding(relative, item.lineno, item.name, end=item.end_lineno or item.lineno)
                for item in nodes
                if not _skipped_python_name(item)
            ]
    return found


def _is_frontend_test(relative: str) -> bool:
    parts = relative.split("/")
    name = parts[-1]
    return bool(TEST_DIRS & set(parts[:-1])) or ".test." in name or ".browser." in name


def is_test_file(relative: str) -> bool:
    """A test file anywhere in the corpus: ``tests/``, browser tests or ``*.test.*``."""
    return relative.split("/", 1)[0] == "tests" or _is_frontend_test(relative)


def frontend_modules(files: dict[str, str]) -> list[str]:
    return sorted(
        path
        for path in files
        if path.startswith(tuple(f"{folder}/" for folder in FRONTEND_DIRS))
        and Path(path).suffix in MODULE_SUFFIXES
        and not _is_frontend_test(path)
    )


def frontend_exports(files: dict[str, str], modules: Sequence[str]) -> list[Finding]:
    found = []
    for path in modules:
        text = files[path]
        for match in EXPORT.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append(Finding(path, line, match.group(1)))
    return found


def _module_references(files: dict[str, str]) -> dict[str, set[str]]:
    """Map each file name or extensionless import name to the files that mention it."""
    references: dict[str, set[str]] = {}
    for path, text in files.items():
        names = set(MODULE_FILE_NAME.findall(text))
        for target in IMPORT_PATH.findall(text):
            names.add(target.rstrip("/").rsplit("/", 1)[-1])
        for name in names:
            references.setdefault(name, set()).add(path)
    return references


def unimported_modules(files: dict[str, str], modules: Sequence[str]) -> list[Finding]:
    references = _module_references(files)
    found = []
    for path in modules:
        module = Path(path)
        names = {module.name, module.stem}
        if module.stem == "index":
            names.add(module.parent.name)
        if not any(references.get(name, set()) - {path} for name in names):
            found.append(Finding(path, 1, module.name))
    return found


def _requirement_name(requirement: str) -> str:
    match = REQUIREMENT_NAME.match(requirement)
    return match.group(1) if match else requirement.strip()


def _normalize(distribution: str) -> str:
    return re.sub(r"[-_.]+", "-", distribution).lower()


def _section_lines(lines: Sequence[str], header: re.Pattern[str]) -> Iterator[tuple[int, str]]:
    """Numbered lines from the first line matching ``header`` to its closing bracket."""
    start = next((n for n, line in enumerate(lines) if header.match(line)), None)
    if start is None:
        return
    for number in range(start, len(lines)):
        if number > start and lines[number].strip().startswith(("]", "}")):
            return
        yield number + 1, lines[number]


def _line_of(text: str, header: str, name: str) -> int:
    pattern = re.compile(rf"^\s*{re.escape(header)}\s*=?\s*:?\s*[\[{{]")
    quoted = re.compile(rf"""["']{re.escape(name)}(?![\w.-])""")
    for number, line in _section_lines(text.splitlines(), pattern):
        if quoted.search(line):
            return number
    return 1


def import_names(distributions: Iterable[str]) -> dict[str, set[str]]:
    """Map each distribution to the modules it installs.

    Installed distributions answer for themselves; ``IMPORT_NAMES`` covers the ones
    whose module name differs from the distribution's when they aren't installed.
    """
    installed: dict[str, set[str]] = {}
    for module, owners in metadata.packages_distributions().items():
        for owner in owners:
            installed.setdefault(_normalize(owner), set()).add(module)
    names = {}
    for distribution in distributions:
        key = _normalize(distribution)
        fallback = IMPORT_NAMES.get(key, (key.replace("-", "_"),))
        names[distribution] = installed.get(key, set()) | set(fallback)
    return names


def _imported_modules(tree: ast.AST, *, every_string: bool) -> set[str]:
    """Top-level modules a Python file imports, or loads by a name in a string."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and every_string:
            found.add(node.value.split(".")[0])
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            called = (
                function.attr
                if isinstance(function, ast.Attribute)
                else (function.id if isinstance(function, ast.Name) else "")
            )
            first = node.args[0]
            if called in DYNAMIC_IMPORTS and isinstance(first, ast.Constant):
                found.add(str(first.value).split(".")[0])
    return found


def _modules_used_by(root: Path, folders: Sequence[str]) -> set[str]:
    used: set[str] = set()
    for folder in folders:
        start = root / folder
        files = [start] if start.is_file() else sorted(start.rglob("*.py"))
        for path in files:
            if not path.is_file() or SKIP_DIRS & set(path.relative_to(root).parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            used |= _imported_modules(tree, every_string=path.suffix == ".spec")
    return used


def unused_python_dependencies(root: Path) -> list[Finding]:
    """Distributions in ``[project].dependencies`` and ``PYTHON_EXTRAS`` nothing imports.

    Imports in tests don't count: a package only tests import belongs in the dev extra.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return []
    text = pyproject.read_text(encoding="utf-8")
    project = tomllib.loads(text).get("project", {})
    sections = [("dependencies", project.get("dependencies", []))]
    extras = project.get("optional-dependencies", {})
    sections += [(extra, extras.get(extra, [])) for extra in PYTHON_EXTRAS]
    listed = [(header, _requirement_name(item)) for header, items in sections for item in items]
    names = import_names(name for _, name in listed)
    used = _modules_used_by(root, PYTHON_USE_PATHS)
    tested = _modules_used_by(root, ["tests"])
    found = []
    for header, name in listed:
        if names[name] & used:
            continue
        note = DEPENDENCY_TESTS_ONLY if names[name] & tested else DEPENDENCY_UNUSED
        found.append(Finding("pyproject.toml", _line_of(text, header, name), name, note))
    return found


def _script_words(scripts: Mapping[str, str]) -> set[str]:
    return {word for script in scripts.values() for word in SHELL_WORD.split(script) if word}


def _package_bins(lock_path: Path) -> dict[str, set[str]]:
    """Commands each package installs, read from ``package-lock.json``."""
    try:
        packages = json.loads(lock_path.read_text(encoding="utf-8")).get("packages", {})
    except (OSError, ValueError):
        return {}
    bins: dict[str, set[str]] = {}
    for location, entry in packages.items():
        name = location.rpartition("node_modules/")[2]
        if location.count("node_modules/") == 1 and isinstance(entry.get("bin"), dict):
            bins[name] = set(entry["bin"])
    return bins


def unused_frontend_dependencies(root: Path, files: Mapping[str, str]) -> list[Finding]:
    """Packages in ``frontend/package.json`` no frontend file or script names.

    A package counts as used when a frontend source, test or config file names it
    as a quoted module (``"react"``, ``'monaco-editor/esm/...'``, ``@import
    "tailwindcss"``), or a ``package.json`` script runs it or a command it installs.
    """
    manifest_path = root / FRONTEND_MANIFEST
    if not manifest_path.is_file():
        return []
    text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(text)
    sources = [
        content
        for path, content in files.items()
        if path.startswith("frontend/")
        and path not in {FRONTEND_MANIFEST, FRONTEND_LOCK}
        and not path.startswith(FRONTEND_VENDOR)
    ]
    words = _script_words(manifest.get("scripts", {}))
    bins = _package_bins(root / FRONTEND_LOCK)
    found = []
    for section in ("dependencies", "devDependencies"):
        for name in manifest.get(section, {}):
            if ({name} | bins.get(name, set())) & words:
                continue
            quoted = re.compile(rf"""["'`]{re.escape(name)}(?:/[^"'`\s]*)?["'`]""")
            if any(quoted.search(content) for content in sources):
                continue
            line = _line_of(text, f'"{section}"', name)
            found.append(Finding(FRONTEND_MANIFEST, line, name, DEPENDENCY_UNUSED))
    return found


def _word_counts(texts: Iterable[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(WORD.findall(text))
    return counts


def read_allowlist(path: Path) -> dict[str, str]:
    """Allowlisted ``name`` and ``path:name`` keys and their reasons; each needs one."""
    if not path.is_file():
        return {}
    keys: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # A route key can hold '#' (POST /api/devices#pair), so the reason starts at ' #'.
        key, _, reason = re.sub(r"\s+#", " #", line).partition(" #")
        if not key.strip() or not reason.strip():
            raise AllowlistError(f"{path.name}:{number} needs a name and '# reason': {raw!r}")
        keys[key.strip()] = reason.strip()
    return keys


def _own_uses(definition: Finding, files: Mapping[str, str]) -> int:
    """How often a definition's name appears inside the definition itself."""
    if not definition.end:
        return 1
    lines = files[definition.path].splitlines()[definition.line - 1 : definition.end]
    return sum(WORD.findall(line).count(definition.name) for line in lines)


def only_tests_use(definitions: Sequence[Finding], files: Mapping[str, str]) -> list[Finding]:
    """Definitions whose name appears outside its own definitions only in tests.

    A name no test mentions either comes back without a note: only its own
    definition uses it.
    """
    production = _word_counts(text for path, text in files.items() if not is_test_file(path))
    tests = _word_counts(text for path, text in files.items() if is_test_file(path))
    own: Counter[str] = Counter()
    for definition in definitions:
        own[definition.name] += _own_uses(definition, files)
    return [
        Finding(d.path, d.line, d.name, ONLY_TESTS if tests[d.name] else "")
        for d in definitions
        if production[d.name] <= own[d.name]
    ]


def is_kotlin_test(relative: str) -> bool:
    """A file in a Gradle test source set: ``src/test``, ``src/androidTest``, ``src/testShared``."""
    parts = relative.split("/")
    if "src" not in parts[:-1]:
        return False
    source_set = parts[parts.index("src") + 1]
    return source_set.startswith(("test", "androidTest"))


def kotlin_corpus(root: Path) -> dict[str, str]:
    """Kotlin, XML, Gradle and R8 rules files under ``KOTLIN_ROOT``, build output left out."""
    start = root / KOTLIN_ROOT
    if not start.is_dir():
        return {}
    files = {}
    for folder, dirs, names in os.walk(start):
        dirs[:] = sorted(d for d in dirs if d not in KOTLIN_SKIP_DIRS)
        for name in sorted(names):
            path = Path(folder, name)
            if path.suffix in KOTLIN_USE_SUFFIXES:
                relative = path.relative_to(root).as_posix()
                files[relative] = path.read_bytes().decode("utf-8", "replace")
    return files


def kotlin_declarations(files: Mapping[str, str]) -> list[Finding]:
    """Each ``fun``, ``class`` and ``object`` in ``KOTLIN_MAIN``, top-level, member or local.

    An ``override`` or ``operator`` fun is left out, since Android or Kotlin syntax calls
    it, and so is an ``@Preview`` function, which only Android Studio calls.
    """
    found = []
    for path, text in files.items():
        if not path.startswith(f"{KOTLIN_MAIN}/") or not path.endswith(".kt"):
            continue
        annotations: set[str] = set()
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            match = KOTLIN_DECLARATION.match(line)
            if match is None or match.group("name") == "interface":
                if stripped.startswith("@") and not stripped.startswith("@file:"):
                    annotations |= set(KOTLIN_ANNOTATION_NAME.findall(stripped))
                elif stripped and not stripped.startswith(("//", "/*", "*")):
                    annotations = set()
                continue
            prefix = match.group("prefix")
            modifiers = set(WORD.findall(KOTLIN_ANNOTATION_NAME.sub("", prefix)))
            annotations |= set(KOTLIN_ANNOTATION_NAME.findall(prefix))
            if not modifiers & KOTLIN_SKIP_MODIFIERS and "Preview" not in annotations:
                found.append(Finding(path, number, match.group("name")))
            annotations = set()
    return found


def kotlin_unused(root: Path, files: Mapping[str, str] | None = None) -> list[Finding]:
    """Phone declarations no other Kotlin, XML or Gradle text names, or only tests name.

    A name the app manifest refers to, such as an activity or a receiver, is used.
    """
    if files is None:
        files = kotlin_corpus(root)
    declarations = kotlin_declarations(files)
    defined = Counter(finding.name for finding in declarations)
    production = _word_counts(text for path, text in files.items() if not is_kotlin_test(path))
    tests = _word_counts(text for path, text in files.items() if is_kotlin_test(path))
    manifest = _word_counts(
        text for path, text in files.items() if path.endswith("/AndroidManifest.xml")
    )
    return [
        Finding(d.path, d.line, d.name, ONLY_TESTS if tests[d.name] else "")
        for d in declarations
        if production[d.name] <= defined[d.name] and not manifest[d.name]
    ]


def _skip_string(text: str, i: int) -> int:
    """The index after the quoted string that starts at ``i``."""
    quote = text[i]
    i += 1
    while i < len(text) and text[i] not in (quote, "\n"):
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _skip_template(text: str, i: int) -> int:
    """The index after the template literal at ``i``, ``${...}`` parts included."""
    i += 1
    while i < len(text) and text[i] != "`":
        if text[i] == "\\":
            i += 2
        elif text.startswith("${", i):
            i = _scan_braces(text, i + 1)
        else:
            i += 1
    return i + 1


def _skip_regex(text: str, i: int) -> int:
    """The index after the regex literal at ``i`` and its flags."""
    i += 1
    in_class = False
    while i < len(text) and text[i] != "\n":
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "/" and not in_class:
            break
        in_class = (in_class or char == "[") and char != "]"
        i += 1
    i += 1
    while i < len(text) and text[i].isalnum():
        i += 1
    return i


JS_WORD = re.compile(r"[A-Za-z_$][\w$]*")
# A property at the start of an object entry: ``name:``, ``name(``, ``name,``,
# ``async name(``, ``get name()``, ``"quoted":``. A spread or computed key doesn't match.
JS_KEY = re.compile(
    r"(?:(?:async|get|set)\s+(?=[\w$*\"']))?\*?\s*"
    r"(?:(?P<name>[A-Za-z_$][\w$]*)|(?P<q>[\"'])(?P<quoted>[^\"'\n]*)(?P=q))\s*[:(,}]"
)


def _scan_braces(text: str, start: int, keys: list[tuple[str, int]] | None = None) -> int:
    """The index after the brace that closes the one at ``start``.

    With ``keys``, appends each top-level property name of that object literal and
    its offset. Strings, template literals, comments and regex literals are skipped,
    so braces inside them don't count.
    """
    depth = 0
    expect_key = False
    previous = "("
    i = start
    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        if expect_key and keys is not None:
            expect_key = False
            match = JS_KEY.match(text, i)
            if match:
                keys.append((match.group("name") or match.group("quoted"), i))
        if char in "'\"":
            i, previous = _skip_string(text, i), '"'
        elif char == "`":
            i, previous = _skip_template(text, i), '"'
        elif char == "/":
            regex = previous in REGEX_AFTER or previous in REGEX_KEYWORDS
            i, previous = (_skip_regex(text, i), '"') if regex else (i + 1, "/")
        elif char in "{[(":
            depth += 1
            expect_key = depth == 1
            i, previous = i + 1, char
        elif char in "}])":
            depth -= 1
            i, previous = i + 1, char
            if depth == 0:
                return i
        elif match := JS_WORD.match(text, i):
            i, previous = match.end(), match.group()
        else:
            expect_key = char == "," and depth == 1
            i, previous = i + 1, char
    return i


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def preload_files(files: Mapping[str, str]) -> dict[str, str]:
    """``frontend/electron/*preload*.cjs``: the scripts that build the page's bridge."""
    return {
        path: text
        for path, text in files.items()
        if path.rpartition("/")[0] == ELECTRON_DIR
        and PRELOAD_NAME.fullmatch(path.rpartition("/")[2])
    }


def bridge_keys(preloads: Mapping[str, str]) -> list[Finding]:
    """Each top-level key of an object passed to ``contextBridge.exposeInMainWorld``."""
    found = []
    for path, text in sorted(preloads.items()):
        for call in EXPOSE_CALL.finditer(text):
            if not text.startswith("{", call.end()):
                continue
            keys: list[tuple[str, int]] = []
            _scan_braces(text, call.end(), keys)
            found += [Finding(path, _line_at(text, offset), key) for key, offset in keys]
    return found


def _page_file(relative: str) -> bool:
    """Files that count as the page calling the bridge: not tests, not preloads."""
    if _is_frontend_test(relative):
        return False
    return relative.startswith("frontend/src/") or (
        relative.startswith(f"{ELECTRON_DIR}/") and relative.endswith(".html")
    )


def unused_bridge(files: Mapping[str, str]) -> list[Finding]:
    """Bridge keys the page never names, and IPC channels no preload file names.

    A test that lists the bridge's keys doesn't count: only ``frontend/src`` code
    (tests left out) and the HTML pages under ``frontend/electron`` call the bridge.
    """
    preloads = preload_files(files)
    used = _word_counts(text for path, text in files.items() if _page_file(path))
    found = [
        Finding(key.path, key.line, key.name, BRIDGE_UNUSED)
        for key in bridge_keys(preloads)
        if not used[key.name]
    ]
    table = files.get(IPC_TABLE, "")
    for row in IPC_ROW.finditer(table):
        channel = row.group(1)
        quoted = re.compile(rf"""["'`]{re.escape(channel)}["'`]""")
        if not any(quoted.search(text) for text in preloads.values()):
            found.append(Finding(IPC_TABLE, _line_at(table, row.start(1)), channel, CHANNEL_UNUSED))
    return found


def _path_piece(segment: str, *, more: bool) -> re.Pattern[str]:
    """A string that is ``/segment`` or starts a path with it: ``"/start"``, `` `/swarms${path}` ``.

    The string ends, or goes on with a query or a ``${...}`` part, right after the
    segment; with ``more`` (a ``*`` segment follows in the route) it may go on with ``/``.
    """
    after = r"""["'`?/]""" if more else r"""["'`?]"""
    return re.compile(rf"""["'`]/{re.escape(segment)}(?={after}|\$\{{)""")


@dataclass(frozen=True)
class RouteCall:
    """How code names a route: ``whole`` in any caller, or every one of ``pieces`` in the apps."""

    whole: re.Pattern[str]
    pieces: tuple[re.Pattern[str], ...] = ()


def route_call(route: str) -> RouteCall:
    """What code writes to call ``route``.

    A ``#action`` key needs its action quoted: ``POST /api/devices#pair`` needs
    ``"pair"``. A path needs its last two literal segments written together
    (``workflows/${id}/logs`` for ``GET /api/workflows/*/logs/*``), or, in the apps' own
    code, each as a path piece a request helper joins (`` `/swarms${path}` `` and
    ``"/start"`` for ``POST /api/swarms/*/start``). A leading ``api`` segment counts as
    written, since ``apiUrl`` adds it: ``GET /api/session`` needs ``api/session`` or
    ``"/session"``.
    """
    key, _, action = route.partition("#")
    if action:
        return RouteCall(re.compile(rf"""["'`]{re.escape(action)}["'`]"""))
    segments = key.split(" ", 1)[-1].strip("/").split("/")
    literal = [n for n, segment in enumerate(segments) if segment != "*"][-2:]
    if len(literal) == 2 and literal[0] == 0 and segments[0] == "api":
        last = segments[literal[1]]
        piece = _path_piece(last, more=segments[literal[1] + 1 :][:1] == ["*"])
        return RouteCall(re.compile(rf"\bapi/{re.escape(last)}(?![\w-])"), (piece,))
    if len(literal) == 1:
        only = segments[literal[0]]
        return RouteCall(re.compile(rf"(?<![\w-]){re.escape(only)}(?![\w-])"))
    parts = [
        ROUTE_WILDCARD if segment == "*" else re.escape(segment)
        for segment in segments[literal[0] : literal[1] + 1]
    ]
    whole = re.compile(rf"(?<![\w-]){'/'.join(parts)}(?![\w-])")
    pieces = (_path_piece(segments[n], more=segments[n + 1 :][:1] == ["*"]) for n in literal)
    return RouteCall(whole, tuple(pieces))


def _route_caller(relative: str) -> bool:
    if relative in ROUTE_TABLES or is_test_file(relative) or is_kotlin_test(relative):
        return False
    return relative.startswith(tuple(f"{folder}/" for folder in ROUTE_CALLER_DIRS))


def unused_routes(root: Path, files: Mapping[str, str], kotlin: Mapping[str, str]) -> list[Finding]:
    """Routes in the parity manifest with no ``cli`` entry that no caller names.

    Callers are non-test code under ``ROUTE_CALLER_DIRS``, ``ROUTE_TABLES`` left out.
    Path pieces count only in the apps (``frontend``, the phone), whose request
    helpers join them; Python callers write a path whole. A route whose path doesn't
    start with ``/api/`` is always reported: ``route_call`` can't tell a call to it
    from a call to its ``/api`` twin, since ``apiUrl`` adds ``/api``.
    """
    manifest_path = root / PARITY_MANIFEST
    if not manifest_path.is_file():
        return []
    text = manifest_path.read_text(encoding="utf-8")
    routes = json.loads(text).get("routes", {})
    callers = {
        path: content for path, content in {**files, **kotlin}.items() if _route_caller(path)
    }
    apps = [content for path, content in callers.items() if not path.startswith("src/")]
    found = []
    for route, entry in routes.items():
        if "cli" in entry:
            continue
        line = _line_at(text, text.find(json.dumps(route)))
        if not route.partition("#")[0].split(" ", 1)[-1].startswith("/api/"):
            found.append(Finding(PARITY_MANIFEST, line, route, ROUTE_OUTSIDE_API))
            continue
        call = route_call(route)
        if any(call.whole.search(content) for content in callers.values()):
            continue
        if call.pieces and all(any(p.search(c) for c in apps) for p in call.pieces):
            continue
        found.append(Finding(PARITY_MANIFEST, line, route, ROUTE_UNUSED))
    return found


def _blank(match: re.Match[str]) -> str:
    """The match with everything but its line breaks turned to spaces, so offsets hold."""
    return re.sub(r"[^\n]", " ", match.group())


def css_class_selectors(text: str) -> list[tuple[str, int]]:
    """Each class a stylesheet's selectors name, and its line.

    Comments, strings and attribute selectors are blanked first. A selector is the
    text before a ``{``; at-rule preludes (``@media``, ``@container``) and
    declarations (which end with ``;`` or ``}``) are left out, so a value such as
    ``.5rem`` isn't read as a class. Nested selectors count like top-level ones.
    """
    text = CSS_COMMENT.sub(_blank, text)
    text = CSS_STRING.sub(_blank, text)
    found = []
    start = 0
    for brace in re.finditer(r"[{};]", text):
        prelude, start = text[start : brace.start()], brace.end()
        if brace.group() != "{" or prelude.lstrip().startswith("@"):
            continue
        offset = brace.start() - len(prelude)
        prelude = CSS_ATTRIBUTE.sub(_blank, prelude)
        for match in CSS_CLASS.finditer(prelude):
            name = match.group(1).replace("\\", "")
            found.append((name, _line_at(text, offset + match.start())))
    return found


def _css_page_file(relative: str) -> bool:
    """Files that count as naming a CSS class: page code and HTML, not tests or stylesheets."""
    if relative in CSS_PAGE_FILES:
        return True
    return (
        relative.startswith(tuple(f"{folder}/" for folder in FRONTEND_DIRS))
        and not relative.endswith(".css")
        and not _is_frontend_test(relative)
    )


def unused_css_classes(files: Mapping[str, str]) -> list[Finding]:
    """Class selectors in ``FRONTEND_DIRS`` stylesheets that no page file names.

    A class counts as used when it appears as a word (bounded by anything but word
    characters and ``-``) in a page file, or when a page file builds it from a
    template: some prefix of the name is followed by ``${``, as ``st-`` is in
    ``st-${state}``.
    """
    pages = [text for path, text in files.items() if _css_page_file(path)]
    words = {word for text in pages for word in CLASS_WORD.findall(text)}
    words |= {token for text in pages for token in CLASS_TOKEN.findall(text)}
    prefixes = {prefix for text in pages for prefix in TEMPLATE_PREFIX.findall(text)}
    found = []
    for path, text in sorted(files.items()):
        if not path.endswith(".css") or not path.startswith(
            tuple(f"{folder}/" for folder in FRONTEND_DIRS)
        ):
            continue
        for name, line in css_class_selectors(text):
            if name in words or any(name[:n] in prefixes for n in range(1, len(name) + 1)):
                continue
            found.append(Finding(path, line, f".{name}", CSS_UNUSED))
    return found


def _allowed(finding: Finding, allowed: Mapping[str, str]) -> bool:
    keys = (f"{finding.path}:{finding.name}", finding.name)
    reason = allowed.get(keys[0], allowed.get(keys[1]))
    if reason is None and finding.note == CSS_UNUSED:
        # A CSS class line may be a pattern, such as `.xterm-*` for a library's classes.
        reason = next(
            (r for k, r in allowed.items() if any(fnmatch.fnmatchcase(key, k) for key in keys)),
            None,
        )
    if reason is None:
        return False
    return finding.note != ONLY_TESTS or reason.lower().startswith(TEST_SEAM)


def find_unused(root: Path) -> list[Finding]:
    allowlist_path = root / ALLOWLIST
    allowed = read_allowlist(allowlist_path)
    files = dict(corpus(root, skip=[allowlist_path]))
    counts = _word_counts(files.values())

    modules = frontend_modules(files)
    definitions = python_definitions(root) + frontend_exports(files, modules)
    # Each definition writes its name once; any further mention is a use.
    defined = Counter(finding.name for finding in definitions)
    unused = [d for d in definitions if counts[d.name] <= defined[d.name]]
    unreported = {(d.path, d.name) for d in unused}
    unused += [
        finding
        for finding in only_tests_use(definitions, files)
        if (finding.path, finding.name) not in unreported
    ]
    unused += unimported_modules(files, modules)
    unused += unused_python_dependencies(root)
    unused += unused_frontend_dependencies(root, files)
    kotlin = kotlin_corpus(root)
    unused += kotlin_unused(root, kotlin)
    unused += unused_bridge(files)
    unused += unused_routes(root, files, kotlin)
    unused += unused_css_classes(files)

    return sorted(finding for finding in set(unused) if not _allowed(finding, allowed))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="checkout to check")
    args = parser.parse_args(argv)
    try:
        findings = find_unused(args.root.resolve())
    except AllowlistError as error:
        print(f"check-dead-code: {error}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    if findings:
        print(
            f"\ncheck-dead-code: {len(findings)} unused. Remove them, or add a line with "
            f"a reason to {ALLOWLIST} when one is kept on purpose. Code {ONLY_TESTS} "
            f"needs a reason that starts with '{TEST_SEAM}', or its tests rewritten "
            "against the production path. Take an unused dependency out of "
            "pyproject.toml or frontend/package.json and relock; move one only tests "
            "import to the dev extra."
        )
        return 1
    print("check-dead-code: nothing unused")
    return 0


if __name__ == "__main__":
    sys.exit(main())
