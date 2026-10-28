"""Compact, provider-neutral task and resource context."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SkillReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    enabled: bool = True


class McpReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    type: Literal["http", "stdio"] = "http"
    url: str = Field(default="", max_length=2048)
    command: str = Field(default="", max_length=4096)
    args: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True
    # Resolve the definition from the Rem resource library when the turn starts.
    library: bool = False

    @model_validator(mode="after")
    def validate_connection(self) -> McpReference:
        if self.type == "stdio":
            if not self.command.strip():
                raise ValueError("Enter an executable for the stdio server")
        else:
            endpoint = urlsplit(self.url)
            if (
                endpoint.scheme not in {"http", "https"}
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.fragment
            ):
                raise ValueError("Use an HTTP endpoint without credentials or fragments")
        return self


class AgentResources(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Defaults match the desktop menu so a partial selection runs as displayed.
    shell: bool = True
    web: bool = True
    agents: bool = True
    browser: bool = False
    skills: list[SkillReference] = Field(default_factory=list, max_length=100)
    mcpServers: list[McpReference] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_mcp_names(self) -> AgentResources:
        names = [server.name for server in self.mcpServers]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique")
        return self

    @classmethod
    def none(cls) -> AgentResources:
        """No built-in tools, for narrow provider calls such as drafting or review."""
        return cls(shell=False, web=False, agents=False, browser=False)


BROWSER_MCP_NAME = "browser"
# Pinned so a browser turn never resolves an unreviewed package release.
BROWSER_MCP_PACKAGE = "@playwright/mcp@0.0.83"
# Keep in sync with settings.browser.agentBrowser in frontend/src/lib/settings.js.
BrowserTarget = Literal["raticode", "external", "external-headless"]
BROWSER_TARGETS: tuple[BrowserTarget, ...] = ("raticode", "external", "external-headless")


def with_browser(
    resources: AgentResources,
    target: BrowserTarget = "external-headless",
    *,
    raticode_server: McpReference | None = None,
) -> AgentResources:
    """Back the Browser switch with one browser MCP server for every provider.

    Raticode's own tabs need a server supplied by the desktop app. External
    browsers use Playwright, either in a visible window or headless.
    """
    if not resources.browser or any(
        server.name == BROWSER_MCP_NAME and server.enabled for server in resources.mcpServers
    ):
        return resources
    if target == "raticode":
        if raticode_server is None:
            raise ValueError(
                "Raticode's browser needs the Raticode desktop app. Open it, or choose an "
                "external browser in Settings > Browser."
            )
        server = raticode_server.model_copy(update={"name": BROWSER_MCP_NAME, "enabled": True})
    else:
        npx = shutil.which("npx")
        if npx is None:
            raise ValueError("Browser automation needs Node.js. Install it or turn Browser off.")
        server = McpReference(
            name=BROWSER_MCP_NAME,
            type="stdio",
            command=npx,
            args=[
                "-y",
                BROWSER_MCP_PACKAGE,
                *(["--headless"] if target == "external-headless" else []),
                "--isolated",
                *playwright_browser_args(),
            ],
        )
    servers = [item for item in resources.mcpServers if item.name != BROWSER_MCP_NAME]
    servers.append(server)
    return resources.model_copy(update={"mcpServers": servers})


def playwright_browser_args() -> list[str]:
    """Choose a Chromium browser that is installed.

    Playwright defaults to Google Chrome and fails when only another browser
    is present. Prefer Chrome, then Edge, then any other Chromium executable,
    including one Playwright downloaded earlier. Otherwise ask for Playwright's
    own Chromium so its error names the install command.
    """
    if _installed(_CHROME_COMMANDS, _platform_paths(_CHROME_PATHS)):
        return []
    if _installed(_EDGE_COMMANDS, _platform_paths(_EDGE_PATHS)):
        return ["--browser=msedge"]
    executable = _installed(_CHROMIUM_COMMANDS, _platform_paths(_CHROMIUM_PATHS))
    executable = executable or _cached_playwright_chromium()
    if executable:
        return ["--browser=chromium", f"--executable-path={executable}"]
    return ["--browser=chromium"]


# Playwright looks for its Chrome and Edge channels at these locations.
_CHROME_COMMANDS = ("google-chrome", "google-chrome-stable")
_CHROME_PATHS = {
    "linux": ["/opt/google/chrome/chrome"],
    "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "win32": [
        "{PROGRAMFILES}/Google/Chrome/Application/chrome.exe",
        "{PROGRAMFILES(X86)}/Google/Chrome/Application/chrome.exe",
        "{LOCALAPPDATA}/Google/Chrome/Application/chrome.exe",
    ],
}
_EDGE_COMMANDS = ("microsoft-edge", "microsoft-edge-stable")
_EDGE_PATHS = {
    "linux": ["/opt/microsoft/msedge/msedge"],
    "darwin": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
    "win32": [
        "{PROGRAMFILES(X86)}/Microsoft/Edge/Application/msedge.exe",
        "{PROGRAMFILES}/Microsoft/Edge/Application/msedge.exe",
    ],
}
_CHROMIUM_COMMANDS = ("chromium", "chromium-browser", "brave", "brave-browser")
_CHROMIUM_PATHS = {
    "darwin": [
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ],
}
_PLAYWRIGHT_CHROMIUM_EXECUTABLES = (
    "chrome-linux64/chrome",
    "chrome-linux/chrome",
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    "chrome-win64/chrome.exe",
    "chrome-win/chrome.exe",
)


def _platform_paths(paths: dict[str, list[str]]) -> list[Path]:
    key = "linux" if sys.platform.startswith("linux") else sys.platform
    resolved = []
    for template in paths.get(key, []):
        variable = template[1 : template.index("}")] if template.startswith("{") else ""
        if variable:
            base = os.environ.get(variable)
            if not base:
                continue
            template = base + template[len(variable) + 2 :]
        resolved.append(Path(template))
    return resolved


def _installed(commands: tuple[str, ...], paths: list[Path]) -> str:
    for command in commands:
        found = shutil.which(command)
        if found:
            return found
    return next((str(path) for path in paths if path.is_file()), "")


def _cached_playwright_chromium() -> str:
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if configured and configured != "0":
        root = Path(configured)
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Caches/ms-playwright"
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"
    builds = []
    for folder in root.glob("chromium-*"):
        revision = folder.name.removeprefix("chromium-")
        if revision.isdigit():
            builds.append((int(revision), folder))
    # The newest build is closest to what the pinned Playwright expects.
    for _revision, folder in sorted(builds, reverse=True):
        for relative in _PLAYWRIGHT_CHROMIUM_EXECUTABLES:
            if (folder / relative).is_file():
                return str(folder / relative)
    return ""


def resource_index(resources: AgentResources) -> str:
    """Index files, never inject their implementation or MCP connection details."""
    from gofer.core.rem_library import skill_header

    entries: list[dict[str, str]] = []
    for skill in resources.skills:
        if skill.enabled:
            path = Path(skill.path).expanduser()
            if path.name != "SKILL.md":
                path = path / "SKILL.md"
            # The description tells the model when the skill is worth reading.
            header = skill_header(path.parent)
            entry = {"kind": "skill", "name": header.get("name") or path.parent.name}
            if header.get("description"):
                entry["description"] = header["description"]
            entries.append({**entry, "path": str(path)})
    for server in resources.mcpServers:
        if server.enabled:
            entries.append({"kind": "mcp", "name": server.name})
    return json.dumps(entries, ensure_ascii=False)


def prompt_envelope(*, instructions: str, context: str, request: str) -> str:
    # JSON quoting keeps embedded delimiters and role labels inside their data fields.
    return (
        instructions.strip()
        + "\n\n"
        + json.dumps(
            {"context": context, "request": request},
            ensure_ascii=False,
            indent=2,
        )
    )


def resource_cli_args(
    provider: str,
    resources: AgentResources,
    working_dir: Path | None = None,
) -> list[str]:
    if provider == "codex":
        args = [
            "-c",
            f"features.shell_tool={str(resources.shell).lower()}",
            "-c",
            f"web_search={json.dumps('live' if resources.web else 'disabled')}",
            "-c",
            f"features.multi_agent={str(resources.agents).lower()}",
            # Pin Codex features that the menu does not offer, so its own
            # defaults cannot add tools the thread never selected. Browser
            # automation comes from the shared Playwright MCP server instead.
            "-c",
            "features.apps=false",
            "-c",
            "features.browser_use=false",
            "-c",
            "features.computer_use=false",
            "-c",
            "features.image_generation=false",
            # Viewing images is part of reading files.
            "-c",
            "tools.view_image=true",
        ]
        if resources.skills:
            skills = ", ".join(
                "{path="
                + json.dumps(str(Path(item.path).expanduser()))
                + ",enabled="
                + str(item.enabled).lower()
                + "}"
                for item in resources.skills
            )
            args += ["-c", f"skills.config=[{skills}]"]
        inherited = _codex_mcp_names(working_dir)
        # Codex splits CLI keys on dots without parsing TOML key quoting.
        # Put dotted names inside one TOML value. Multiple root overrides would
        # replace earlier CLI overrides and leave some inherited servers enabled.
        if any("." in name for name in inherited):
            disabled = ",".join(json.dumps(name) + "={enabled=false}" for name in inherited)
            args += ["-c", "mcp_servers={" + disabled + "}"]
        else:
            for name in inherited:
                args += ["-c", f"mcp_servers.{name}.enabled=false"]
        server_names = codex_mcp_server_names(resources, working_dir)
        for server in resources.mcpServers:
            # A fresh name prevents Codex from merging inherited transport or credentials.
            fields = [f"enabled={str(server.enabled).lower()}"]
            if server.type == "stdio":
                fields += [
                    f"command={json.dumps(server.command)}",
                    f"args={json.dumps(server.args)}",
                ]
                if server.name in {"reports", "second_brain"}:
                    fields += ['env_vars=["RATICODE_REPORT_PDF_URL","RATICODE_REPORT_PDF_TOKEN"]']
            else:
                fields += [f"url={json.dumps(server.url)}"]
            args += ["-c", f"mcp_servers.{server_names[server.name]}={{" + ",".join(fields) + "}"]
        return args
    if provider == "claude_code":
        tools = claude_native_tools(resources)
        servers: dict[str, Any] = {
            item.name: (
                {"type": "stdio", "command": item.command, "args": item.args}
                if item.type == "stdio"
                else {"type": "http", "url": item.url}
            )
            for item in resources.mcpServers
            if item.enabled
        }
        # Explicit MCP configuration prevents ambient servers leaking between threads.
        return [
            "--tools",
            ",".join(tools),
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps({"mcpServers": servers}),
        ]
    raise ValueError(f"Resource configuration is unsupported by provider '{provider}'")


def claude_native_tools(resources: AgentResources) -> list[str]:
    # Notebook edits and language-server queries ride along with file access.
    tools = ["Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep", "LSP"]
    if resources.shell:
        tools.append("Bash")
    if resources.web:
        tools += ["WebFetch", "WebSearch"]
    if resources.agents:
        tools.append("Agent")
    return tools


def codex_mcp_server_names(
    resources: AgentResources, working_dir: Path | None = None
) -> dict[str, str]:
    """Map selected servers to invocation names free of inherited configuration."""
    inherited = set(_codex_mcp_names(working_dir))
    occupied = inherited | {server.name for server in resources.mcpServers}
    names: dict[str, str] = {}
    for server in resources.mcpServers:
        if server.name in names:
            continue
        name = server.name
        if name in inherited:
            suffix = 1
            while True:
                name = f"raticode_{server.name[:40]}_{suffix}"
                if name not in occupied:
                    break
                suffix += 1
        occupied.add(name)
        names[server.name] = name
    return names


def _codex_mcp_names(working_dir: Path | None) -> list[str]:
    """Disable inherited MCP entries before applying the thread's explicit selection."""
    codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    configs = [codex_dir / "config.toml", Path("/etc/codex/config.toml")]
    if working_dir:
        configs += [
            parent / ".codex" / "config.toml" for parent in [working_dir, *working_dir.parents]
        ]
    names: set[str] = set()
    for config in configs:
        try:
            if config.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Codex configuration exceeds the supported size")
            document = tomllib.loads(config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        names.update(document.get("mcp_servers", {}).keys())
    return sorted(names)
