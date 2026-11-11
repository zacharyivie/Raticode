"""Read the app's surfaces that the CLI must keep up with, and the CLI's own commands.

* Backend HTTP routes. The backend is a hand-written ``BaseHTTPRequestHandler`` with
  no route table, so this reads the dispatch code itself. Each ``if`` that tests the
  request path, the Rem path parts, a swarm's ID and action, or a trigger action
  becomes one route key such as ``GET /api/workflows/*/logs``. A path check nested in
  another route's body narrows that route and becomes its own key. Action APIs, where
  a field of the request body picks what happens (Organizations, Second Brain, the
  skill library, devices, ticket changes, swarm control), get one key per action:
  ``POST /api/organizations#read``.
* Phone operations (``desktop.read``/``desktop.action`` ops) and every other phone
  protocol message type, from ``desktop_control`` and ``event.schema.json``.
* Desktop-only features: the ``gofer:*`` requests the Electron window's renderer
  sends to its main process.

A condition that tests the path in a shape this reader doesn't know raises
``UnreadableRoute`` instead of being skipped, so a new route can't hide from the
parity test by being written differently.
"""

from __future__ import annotations

import ast
import itertools
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "src/gofer/ui/server.py"
BRAIN_LIBRARY = ROOT / "src/gofer/ui/brain_library.py"
ORGANIZATIONS = ROOT / "src/gofer/ui/organizations.py"
KANBAN = ROOT / "src/gofer/ui/rem_kanban.py"
DEVICES = ROOT / "src/gofer/devices/control.py"
SWARMS = ROOT / "src/gofer/ui/swarms.py"
PROTOCOL = ROOT / "src/gofer/devices/protocol/v2/event.schema.json"
ELECTRON = ROOT / "frontend/electron"
MANIFEST = ROOT / "docs/cli-parity.json"

PATH_EXPRESSIONS = {"parsed.path", "path", "urlparse(self.path).path"}
# Sub-dispatchers read by their own functions below.
DELEGATED = {
    "_dispatch_second_brain_request",
    "_dispatch_rem_resources_request",
    "_dispatch_rem_home_request",
    "_dispatch_organization_request",
    "_dispatch_swarm_request",
    "_dispatch_rattish_triggers",
}


class UnreadableRoute(AssertionError):
    pass


@dataclass
class _Match:
    method: str = "*"
    exact: str | None = None
    prefix: str = ""
    contains: list[str] = field(default_factory=list)
    suffix: str = ""

    def render(self) -> str:
        if self.exact is not None:
            return self.exact
        parts = [self.prefix, *self.contains, self.suffix]
        route = "*".join(part for part in parts if part)
        if not self.suffix:
            route += "*"
        return route


def _src(node: ast.AST) -> str:
    return ast.unparse(node)


def _is_path(node: ast.AST) -> bool:
    return _src(node) in PATH_EXPRESSIONS


def _constants(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        values = [_constants(item) for item in node.elts]
        return [value for group in values for value in group]
    raise UnreadableRoute(f"Expected string constants, found: {_src(node)}")


def _conjuncts(test: ast.expr) -> list[ast.expr]:
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return [item for value in test.values for item in _conjuncts(value)]
    return [test]


def _mentions(node: ast.AST, names: set[str]) -> bool:
    return any(_src(child) in names for child in ast.walk(node))


def _path_alternatives(term: ast.expr) -> list[dict[str, object]]:
    """Each alternative is a partial match: exact, prefix, contains, suffix or method."""
    if isinstance(term, ast.BoolOp) and isinstance(term.op, ast.Or):
        return [alt for value in term.values for alt in _path_alternatives(value)]
    if isinstance(term, ast.BoolOp) and isinstance(term.op, ast.And):
        merged: list[dict[str, object]] = [{}]
        for value in term.values:
            merged = [_merge(left, right) for left in merged for right in _path_alternatives(value)]
        return merged
    if isinstance(term, ast.Compare) and len(term.ops) == 1:
        left, op, right = term.left, term.ops[0], term.comparators[0]
        if _is_path(left) and isinstance(op, ast.Eq):
            return [{"exact": value} for value in _constants(right)]
        if _is_path(left) and isinstance(op, ast.In):
            return [{"exact": value} for value in _constants(right)]
        if isinstance(op, ast.In) and _is_path(right):
            return [{"contains": [value]} for value in _constants(left)]
        if _src(left) == "method" and isinstance(op, ast.Eq):
            return [{"method": value} for value in _constants(right)]
        if _src(left) == "method":
            return [{}]
    if (
        isinstance(term, ast.Call)
        and isinstance(term.func, ast.Attribute)
        and _is_path(term.func.value)
        and term.func.attr in {"startswith", "endswith"}
    ):
        key = "prefix" if term.func.attr == "startswith" else "suffix"
        return [{key: value} for value in _constants(term.args[0])]
    if _mentions(term, PATH_EXPRESSIONS):
        raise UnreadableRoute(f"Unknown path condition: {_src(term)}")
    return [{}]


def _merge(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    merged = dict(left)
    for key, value in right.items():
        if key == "contains":
            merged["contains"] = [*left.get("contains", []), *value]  # type: ignore[misc]
        else:
            merged[key] = value
    return merged


def _matches(test: ast.expr) -> list[_Match]:
    alternatives: list[dict[str, object]] = [{}]
    for term in _conjuncts(test):
        alternatives = [
            _merge(left, right) for left in alternatives for right in _path_alternatives(term)
        ]
    result = []
    for alt in alternatives:
        match = _Match(
            method=str(alt.get("method", "*")),
            exact=alt.get("exact"),  # type: ignore[arg-type]
            prefix=str(alt.get("prefix", "")),
            contains=list(alt.get("contains", [])),  # type: ignore[call-overload]
            suffix=str(alt.get("suffix", "")),
        )
        if match.exact is None and not (match.prefix or match.contains or match.suffix):
            continue
        result.append(match)
    return result


def _function(tree: ast.Module, name: str, owner: str | None = None) -> ast.FunctionDef:
    scopes: list[ast.AST] = [tree]
    if owner:
        scopes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == owner]
    for scope in scopes:
        for node in ast.walk(scope):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
    raise UnreadableRoute(f"Function {name} is missing" + (f" from {owner}" if owner else ""))


def _ifs(node: ast.AST) -> Iterator[ast.If]:
    """Each ``if`` in a function, including ones nested in another ``if``'s body."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.If):
            yield child
            yield from _ifs(ast.Module(body=child.body, type_ignores=[]))
            yield from _ifs(ast.Module(body=child.orelse, type_ignores=[]))
        elif not isinstance(child, (ast.expr, ast.FunctionDef, ast.Lambda)):
            yield from _ifs(child)


def _delegates(node: ast.If) -> bool:
    for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
        if isinstance(child, ast.Attribute) and child.attr in DELEGATED:
            return True
    return False


def _narrow(outer: _Match, inner: _Match) -> _Match | None:
    """The route a path check nested in ``outer``'s body selects, or None if it can't match."""
    if "*" not in (outer.method, inner.method) and outer.method != inner.method:
        return None
    method = inner.method if inner.method != "*" else outer.method
    if outer.exact is not None or inner.exact is not None:
        exact = inner.exact if inner.exact is not None else outer.exact
        assert exact is not None
        for match in (outer, inner):
            if match.exact is not None and match.exact != exact:
                return None
            if not (
                exact.startswith(match.prefix)
                and exact.endswith(match.suffix)
                and all(part in exact for part in match.contains)
            ):
                return None
        return _Match(method=method, exact=exact)
    prefixes = sorted((outer.prefix, inner.prefix), key=len)
    suffixes = sorted((outer.suffix, inner.suffix), key=len)
    if not prefixes[1].startswith(prefixes[0]) or not suffixes[1].endswith(suffixes[0]):
        return None
    contains = [*outer.contains, *(part for part in inner.contains if part not in outer.contains)]
    return _Match(method=method, prefix=prefixes[1], contains=contains, suffix=suffixes[1])


def _path_routes(function: ast.AST, method: str) -> set[str]:
    routes: set[str] = set()

    def visit(node: ast.AST, outer: list[_Match] | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If):
                inner = outer
                if _mentions(child.test, PATH_EXPRESSIONS) and not _delegates(child):
                    matches = _matches(child.test)
                    if outer is not None:
                        narrowed = [_narrow(o, m) for o in outer for m in matches]
                        matches = [match for match in narrowed if match is not None]
                        if not matches:
                            raise UnreadableRoute(
                                f"Nested path check can never match its route: {_src(child.test)}"
                            )
                    for match in matches:
                        verb = match.method if match.method != "*" else method
                        routes.add(f"{verb} {match.render()}")
                    inner = matches
                visit(ast.Module(body=child.body, type_ignores=[]), inner)
                visit(ast.Module(body=child.orelse, type_ignores=[]), outer)
            elif not isinstance(child, (ast.expr, ast.FunctionDef, ast.Lambda)):
                visit(child, outer)

    visit(function, None)
    return routes


def _top_level_routes(tree: ast.Module) -> set[str]:
    dispatch = _function(tree, "_dispatch_request", "GoferUiRequestHandler")
    # Only the routing inside the try block; the code above it reads the path to
    # decide concurrency limits, not to route.
    tries = [
        node
        for node in dispatch.body
        if isinstance(node, ast.Try) and any(_delegates(child) for child in _ifs(node))
    ]
    if len(tries) != 1:
        raise UnreadableRoute("_dispatch_request should route inside one try block")
    routes = _path_routes(ast.Module(body=tries[0].body, type_ignores=[]), "*")
    for verb in ("GET", "POST", "PUT", "DELETE"):
        routes |= _path_routes(_function(tree, f"_dispatch_{verb}", "GoferUiRequestHandler"), verb)
    return routes


def _parts_route(test: ast.expr, prefix: str, parts_name: str = "parts") -> list[str]:
    method = "*"
    length: int | None = None
    fixed: list[str] | None = None
    segments: dict[int, list[str]] = {}
    for term in _conjuncts(test):
        text = _src(term)
        if isinstance(term, ast.Compare) and len(term.ops) == 1:
            left, op, right = term.left, term.ops[0], term.comparators[0]
            if _src(left) == "method" and isinstance(op, ast.Eq):
                method = _constants(right)[0]
                continue
            if _src(left) == parts_name and isinstance(op, ast.Eq):
                fixed = _constants(right)
                continue
            if _src(left) == f"len({parts_name})" and isinstance(op, ast.Eq):
                assert isinstance(right, ast.Constant)
                length = int(right.value)
                continue
            if (
                isinstance(left, ast.Subscript)
                and _src(left.value) == parts_name
                and isinstance(op, (ast.Eq, ast.In))
            ):
                assert isinstance(left.slice, ast.Constant)
                segments[int(left.slice.value)] = _constants(right)
                continue
        if text == f"not {parts_name}":
            fixed = []
            continue
        raise UnreadableRoute(f"Unknown Rem path condition: {text}")
    if fixed is not None:
        return [f"{method} {prefix}" + "".join(f"/{part}" for part in fixed)]
    if length is None:
        length = max(segments) + 1
    choices = [segments.get(index, ["*"]) for index in range(length)]
    return [
        f"{method} {prefix}/" + "/".join(combination) for combination in itertools.product(*choices)
    ]


def _rem_routes(tree: ast.Module) -> set[str]:
    routes = set()
    home = _function(tree, "_dispatch_rem_home_request", "GoferUiRequestHandler")
    for node in _ifs(home):
        if not _mentions(node.test, {"parts", "parts[0]"}):
            continue
        if _src(node.test) == "parts[0] == 'kanban'":
            continue  # Read by _rem_kanban_request below.
        routes.update(_parts_route(node.test, "/api/rem"))
    kanban = _function(tree, "_rem_kanban_request", "GoferUiRequestHandler")
    for node in _ifs(kanban):
        if _mentions(node.test, {"parts"}):
            routes.update(_parts_route(node.test, "/api/rem/kanban"))
    return routes


def _swarm_routes(tree: ast.Module) -> set[str]:
    routes = set()
    function = _function(tree, "_dispatch_swarm_request", "GoferUiRequestHandler")
    for node in _ifs(function):
        terms = _conjuncts(node.test)
        if not any(
            _src(t) in {"swarm_id", "not swarm_id"}
            or "action ==" in _src(t)
            or _src(t) == "not action"
            for t in terms
        ):
            continue
        method, has_id, action = "*", False, ""
        for term in terms:
            text = _src(term)
            if text.startswith("method == "):
                method = _constants(term.comparators[0])[0]  # type: ignore[attr-defined]
            elif text == "swarm_id":
                has_id = True
            elif text.startswith("action == "):
                action = _constants(term.comparators[0])[0]  # type: ignore[attr-defined]
                has_id = True
            elif text == "not action":
                has_id = has_id or not any(_src(t) == "not swarm_id" for t in terms)
            elif text == "not swarm_id":
                pass
            else:
                raise UnreadableRoute(f"Unknown swarm route condition: {text}")
        route = "/api/swarms" + ("/*" if has_id else "") + (f"/{action}" if action else "")
        routes.add(f"{method} {route}")
    return routes


def _trigger_routes(tree: ast.Module) -> set[str]:
    routes = set()
    function = _function(tree, "_dispatch_rattish_triggers", "GoferUiRequestHandler")
    for node in _ifs(function):
        if not _mentions(node.test, {"action"}):
            continue
        method, route = "*", "/api/rattish/triggers"
        for term in _conjuncts(node.test):
            text = _src(term)
            if text.startswith("method == "):
                method = _constants(term.comparators[0])[0]  # type: ignore[attr-defined]
            elif text in {"not action", "action"}:
                pass
            elif text.startswith("action == "):
                route += "/" + _constants(term.comparators[0])[0]  # type: ignore[attr-defined]
            elif text.startswith("action.endswith("):
                route += "/*" + _constants(term.args[0])[0]  # type: ignore[attr-defined]
            elif text == "'/' not in action":
                route += "/*"
            else:
                raise UnreadableRoute(f"Unknown trigger route condition: {text}")
        routes.add(f"{method} {route}")
    return routes


ACTION_NAMES = {"action", "body.get('action')", "payload.get('action')"}


def _compared_actions(function: ast.FunctionDef, names: set[str] | None = None) -> set[str]:
    """Every string the body's ``action`` field is compared with in ``function``."""
    names = names or {"action"}
    actions = set()
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and _src(node.left) in names
            and isinstance(node.ops[0], (ast.Eq, ast.In))
        ):
            try:
                actions.update(_constants(node.comparators[0]))
            except UnreadableRoute:
                continue
    return actions


def _action_routes(server: ast.Module) -> set[str]:
    from gofer.ui.organization_tools import ACTION_HELP

    library = ast.parse(BRAIN_LIBRARY.read_text())
    organizations = ast.parse(ORGANIZATIONS.read_text())
    routes = {"GET /api/rem-resources"}
    resources = _function(server, "_dispatch_rem_resources_request", "GoferUiRequestHandler")
    routes |= {f"POST /api/rem-resources#{a}" for a in _compared_actions(resources)}
    brains = _compared_actions(_function(library, "request", "BrainLibrary"))
    routes |= {f"POST /api/second-brains#{action}" for action in brains}
    org_call = _compared_actions(_function(organizations, "call", "OrganizationManager"))
    # "import_preview" style names only; skip values that aren't action names.
    org_actions = set(ACTION_HELP) | {a for a in org_call if a.replace("_", "").isalpha()}
    routes |= {f"POST /api/organizations#{action}" for action in org_actions}
    routes.add("GET /api/organizations#availability")
    return routes


# Routes whose request body's ``action`` field picks what happens: the route key, then
# the file, class and method that compare that field. Each action gets its own key.
BODY_ACTIONS: dict[str, tuple[Path, str, str]] = {
    "POST /api/devices": (DEVICES, "DeviceControl", "action"),
    "POST /api/rem/kanban/ticket": (KANBAN, "RemKanban", "act"),
    "POST /api/swarms/*/control": (SWARMS, "SwarmManager", "control"),
    "POST /api/swarms/*/execution": (SWARMS, "SwarmManager", "execution"),
    "POST /api/swarms/*/deliveries": (SWARMS, "SwarmManager", "resolve_delivery"),
}


def body_actions(route: str, source: str | None = None) -> set[str]:
    """The ``action`` values the handler of ``route`` accepts in its request body."""
    path, owner, name = BODY_ACTIONS[route]
    tree = ast.parse(source if source is not None else path.read_text())
    actions = _compared_actions(_function(tree, name, owner), ACTION_NAMES)
    if route == "POST /api/devices":
        # The HTTP handler prepares some device actions before handing the body over.
        server = ast.parse(SERVER.read_text())
        actions |= {
            action
            for action in _compared_actions(
                _function(server, "_dispatch_POST", "GoferUiRequestHandler"), ACTION_NAMES
            )
            if action.replace("_", "").isalpha()
        }
    if not actions:
        raise UnreadableRoute(f"No body actions found for {route} in {path.name}:{name}")
    return actions


def _expand_body_actions(routes: set[str]) -> set[str]:
    expanded = set(routes)
    for route in BODY_ACTIONS:
        if route in expanded:
            expanded.discard(route)
            expanded |= {f"{route}#{action}" for action in body_actions(route)}
    return expanded


def backend_routes(server_source: str | None = None) -> set[str]:
    """Every route key; ``server_source`` replaces server.py for tests of this reader."""
    tree = ast.parse(server_source if server_source is not None else SERVER.read_text())
    routes = (
        _top_level_routes(tree)
        | _rem_routes(tree)
        | _swarm_routes(tree)
        | _trigger_routes(tree)
        | _action_routes(tree)
    )
    return routes if server_source is not None else _expand_body_actions(routes)


def _protocol(schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return schema if schema is not None else dict(json.loads(PROTOCOL.read_text()))


def _payload_properties(schema: dict[str, Any], message_type: str) -> dict[str, Any]:
    for rule in schema.get("allOf", []):
        if rule.get("if", {}).get("properties", {}).get("type", {}).get("const") == message_type:
            payload = rule.get("then", {}).get("properties", {}).get("payload", {})
            return dict(payload.get("properties", {}))
    return {}


def phone_operations(schema: dict[str, Any] | None = None) -> set[str]:
    """Desktop operations a phone can ask for, from the desktop's tables and the protocol."""
    from gofer.devices.desktop_control import ACTION_OPS, READ_OPS

    protocol = _protocol(schema)
    declared = set()
    for message_type in ("desktop.read", "desktop.action"):
        declared |= set(_payload_properties(protocol, message_type).get("op", {}).get("enum", []))
    return set(READ_OPS) | set(ACTION_OPS) | declared


# Message types whose payload's ``op`` is listed one by one in phone_operations().
OP_MESSAGES = {"desktop.read", "desktop.action", "desktop.result"}
# Payload fields that pick what a message does; each value gets its own key.
MESSAGE_ACTIONS = ("action", "decision")


def phone_messages(schema: dict[str, Any] | None = None) -> set[str]:
    """Every phone protocol message type, one key per action for multi-action messages.

    ``thread.manage`` becomes ``thread.manage#pin``, ``thread.manage#archive`` and so on.
    """
    protocol = _protocol(schema)
    types = protocol.get("properties", {}).get("type", {}).get("enum")
    if not types:
        raise UnreadableRoute("event.schema.json lists no message types")
    keys = set()
    for message_type in types:
        if message_type in OP_MESSAGES:
            continue
        properties = _payload_properties(protocol, message_type)
        field_name = next((f for f in MESSAGE_ACTIONS if properties.get(f, {}).get("enum")), None)
        if field_name is None:
            keys.add(message_type)
        else:
            keys |= {f"{message_type}#{value}" for value in properties[field_name]["enum"]}
    return keys


def electron_requests(root: Path | None = None) -> set[str]:
    """Every ``gofer:*`` request the desktop window's renderer can send to Electron's main.

    Handlers are registered from the table in ``ipc-handlers.cjs`` or by a literal
    ``ipcMain.handle/on/once("gofer:...")`` call. Any other registration shape raises.
    """
    import re

    base = root or ELECTRON
    table = (base / "ipc-handlers.cjs").read_text(encoding="utf-8")
    channels = set(re.findall(r'\[\s*"(gofer:[a-z0-9-]+)"\s*,\s*"\w+"\s*\]', table))
    if not channels:
        raise UnreadableRoute("ipc-handlers.cjs registers no gofer:* handlers")
    call = re.compile(r"ipcMain\.(?:handle|on|once)\(\s*([^,\s)]+)")
    for path in sorted([*base.rglob("*.js"), *base.rglob("*.cjs")]):
        if "tests" in path.relative_to(base).parts or "node_modules" in path.parts:
            continue
        for first in call.findall(path.read_text(encoding="utf-8")):
            literal = re.fullmatch(r"[\"'`](gofer:[a-z0-9-]+)[\"'`]", first)
            if literal:
                channels.add(literal.group(1))
            elif not (path.name == "ipc-handlers.cjs" and first == "channel"):
                raise UnreadableRoute(
                    f"{path.relative_to(base)} registers an IPC handler this reader can't read: "
                    f"ipcMain.*({first}, ...)"
                )
    return channels


def cli_commands() -> dict[str, bool]:
    """Every leaf command path, mapped to whether it is hidden from help."""
    import typer

    from gofer.cli.main import app

    root = typer.main.get_command(app)
    commands: dict[str, bool] = {}

    def walk(command: Any, path: list[str], hidden: bool) -> None:
        hidden = hidden or bool(getattr(command, "hidden", False))
        children = getattr(command, "commands", None)
        if isinstance(children, dict):
            for name, child in children.items():
                walk(child, [*path, name], hidden)
        else:
            commands[" ".join(path)] = hidden

    walk(root, [], False)
    return commands


def load_manifest() -> dict[str, Any]:
    return dict(json.loads(MANIFEST.read_text()))


def mapped_commands(entries: Iterable[dict[str, object]]) -> set[str]:
    commands = set()
    for entry in entries:
        value = entry.get("cli")
        if isinstance(value, str):
            commands.add(value)
        elif isinstance(value, list):
            commands.update(str(item) for item in value)
    return commands
