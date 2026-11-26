"""The backend's route table: which handler method answers a request's method and path.

``server.py`` creates one ``RouteTable`` and registers each handler with
``@ROUTES.route("POST", "/api/rattish/triggers/{trigger_id}/approve", body=0)``. The
table keeps the method's name, not the function, so the server looks the handler up
on every request and a test can patch the real handler.

Matching works on path segments. A route matches only a path with the same number of
segments; a literal segment matches only itself and ``{name}`` matches one non-empty
segment, percent-decoded after the split so ``%2F`` stays inside one value. When two
routes match the same path, the one with a literal at the first position where they
differ wins, whatever order they were declared in. Registering two routes with the
same key raises at import.

A ``Mount`` groups the routes under a prefix. It gives any request under the prefix
that no route matches the answer the old hand-written dispatcher gave, and it maps
the exceptions its handlers raise to answers. OPTIONS never matches: the preflight
answer stays the same for every path.

The CLI parity check (``tests/helpers/cli_parity.py``) reads ``Route.key`` from the
table, so a route declared here is checked against ``docs/cli-parity.json``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import unquote

METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

# A status answered with {"error": str(exc)}, or the name of a handler method that
# takes the exception and sends its own answer (for answers with extra fields).
Answer = int | str
ErrorMap = tuple[tuple[type[Exception], Answer], ...]

Handler = TypeVar("Handler", bound=Callable[..., Any])


@dataclass(frozen=True)
class Route:
    method: str
    path: str  # "/api/rattish/triggers/{trigger_id}/approve"
    handler: str  # the request handler's method name, looked up per request
    body: int | None = None  # None: reads no body; 0: the default limit; n: limit in bytes
    status: int = 200  # status of a successful answer

    @property
    def segments(self) -> tuple[str | None, ...]:
        """Each path segment: its literal text, or None for a ``{name}`` parameter."""
        return tuple(None if _is_param(part) else part for part in self.path.split("/"))

    @property
    def params(self) -> tuple[str, ...]:
        return tuple(part[1:-1] for part in self.path.split("/") if _is_param(part))

    @property
    def key(self) -> str:
        """The CLI parity manifest's key: ``POST /api/rattish/triggers/*/approve``."""
        path = "/".join("*" if _is_param(part) else part for part in self.path.split("/"))
        return f"{self.method} {path}"


@dataclass(frozen=True)
class Mount:
    prefix: str  # "/api/workflow-shortcuts"
    miss: tuple[int, str]  # what a request under the prefix that no route matches gets
    errors: ErrorMap = ()  # shared by the routes under it, checked in order

    def covers(self, path: str) -> bool:
        return path == self.prefix or path.startswith(self.prefix + "/")


@dataclass(frozen=True)
class RouteRequest:
    params: dict[str, str]  # {"trigger_id": "trg_0f3c..."}
    query: dict[str, list[str]]  # parse_qs of the query string
    body: dict[str, Any] | None  # the JSON body when the route reads one


@dataclass(frozen=True)
class Found:
    """What ``RouteTable.match`` found: a route and its parameters, or a Mount's miss."""

    route: Route | None
    params: dict[str, str]
    miss: tuple[int, str] | None = None
    errors: ErrorMap = ()

    def answer_for(self, exc: BaseException) -> Answer | None:
        for kind, answer in self.errors:
            if isinstance(exc, kind):
                return answer
        return None


def _is_param(part: str) -> bool:
    return len(part) > 2 and part.startswith("{") and part.endswith("}")


class RouteTable:
    def __init__(self) -> None:
        self._routes: dict[str, Route] = {}
        self._mounts: list[Mount] = []

    @property
    def routes(self) -> tuple[Route, ...]:
        return tuple(self._routes.values())

    def mount(self, mount: Mount) -> Mount:
        if any(mount.covers(other.prefix) or other.covers(mount.prefix) for other in self._mounts):
            raise ValueError(f"Mount {mount.prefix} overlaps another mount")
        self._mounts.append(mount)
        return mount

    def add(self, route: Route) -> Route:
        if route.method not in METHODS:
            raise ValueError(f"Unknown route method {route.method!r}")
        if not route.path.startswith("/") or route.path.endswith("/"):
            raise ValueError(f"Route path must start, and not end, with '/': {route.path}")
        for part in route.path.split("/")[1:]:
            if not part or (("{" in part or "}" in part) and not _is_param(part)):
                raise ValueError(f"Route path has an unreadable segment: {route.path}")
        if len(set(route.params)) != len(route.params):
            raise ValueError(f"Route path repeats a parameter name: {route.path}")
        if route.key in self._routes:
            raise ValueError(f"Two routes have the key {route.key}")
        self._routes[route.key] = route
        return route

    def route(
        self, method: str, path: str, *, body: int | None = None, status: int = 200
    ) -> Callable[[Handler], Handler]:
        """Register the decorated handler method for ``method`` and ``path``."""

        def register(handler: Handler) -> Handler:
            self.add(Route(method, path, handler.__name__, body=body, status=status))
            return handler

        return register

    def match(self, method: str, path: str) -> Found | None:
        """The route for a request, a Mount's miss answer, or None to fall through."""
        if method == "OPTIONS":
            return None
        mount = next((m for m in self._mounts if m.covers(path)), None)
        parts = path.split("/")
        best: tuple[tuple[int, ...], Route] | None = None
        for route in self._routes.values():
            segments = route.segments
            if route.method != method or len(segments) != len(parts):
                continue
            if all(
                part == literal if literal is not None else bool(part)
                for literal, part in zip(segments, parts, strict=True)
            ):
                # Literal segments sort before parameters, position by position.
                rank = tuple(int(literal is None) for literal in segments)
                if best is None or rank < best[0]:
                    best = (rank, route)
        if best is not None:
            route = best[1]
            values = [
                unquote(part)
                for literal, part in zip(route.segments, parts, strict=True)
                if literal is None
            ]
            return Found(
                route,
                dict(zip(route.params, values, strict=True)),
                errors=mount.errors if mount else (),
            )
        if mount is not None:
            return Found(None, {}, miss=mount.miss)
        return None
