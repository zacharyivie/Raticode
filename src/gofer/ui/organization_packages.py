"""Agent Companies v1 draft packages.

Reference: Paperclip efce9356b553a08f77a5877bb0ceac68d2cc4ad8.
"""

from __future__ import annotations

import base64
import io
import lzma
import posixpath
import stat
import zipfile
import zlib
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from gofer.core.http import read_response_bytes
from gofer.ui.organization_store import OrganizationConfig, slugify
from gofer.utils.atomic_output import open_binary_input

MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_YAML_DEPTH = 64
KINDS = {"COMPANY.md", "TEAM.md", "AGENTS.md", "PROJECT.md", "TASK.md", "SKILL.md"}
_WINDOWS_DEVICES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    *(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"),
}


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 4096
        or len(path.parts) > 64
        or not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or any(character in value for character in '\\:<>"|?*')
        or any(ord(character) < 32 for character in value)
        or any(
            part.endswith((".", " "))
            or part.partition(".")[0].rstrip(" ").upper() in _WINDOWS_DEVICES
            for part in path.parts
        )
        or str(path) != value
    ):
        raise ValueError(f"Unsafe package path: {value}")
    return value


def _validate_file_paths(names: Iterable[str]) -> None:
    """Reject aliases and file/directory collisions before materializing assets."""
    spellings: dict[str, str] = {}
    files: set[str] = set()
    directories: set[str] = set()
    for name in names:
        parts = PurePosixPath(safe_path(name)).parts
        for length in range(1, len(parts) + 1):
            spelling = "/".join(parts[:length])
            key = spelling.casefold()
            previous = spellings.setdefault(key, spelling)
            is_file = length == len(parts)
            if previous != spelling or key in files or (is_file and key in directories):
                raise ValueError(f"Colliding package paths: {previous}, {name}")
            (files if is_file else directories).add(key)


def read_directory(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise ValueError("Choose a package folder")
    files: dict[str, str] = {}
    total = 0
    for path in root.rglob("*"):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            raise ValueError("Package symlinks are not supported")
        if not path.is_file():
            continue
        if path.stat().st_size + total > MAX_PACKAGE_BYTES or len(files) >= 1000:
            raise ValueError("Package exceeds 16 MiB or 1000 files")
        try:
            with open_binary_input(path) as source:
                content = source.read(MAX_PACKAGE_BYTES - total + 1)
        except OSError as exc:
            raise ValueError("Package files changed or cannot be read safely") from exc
        total += len(content)
        if total > MAX_PACKAGE_BYTES:
            raise ValueError("Package exceeds 16 MiB or 1000 files")
        files[safe_path(path.relative_to(root).as_posix())] = base64.b64encode(content).decode()
    _validate_file_paths(files)
    return files


def read_zip(encoded: str) -> dict[str, str]:
    if len(encoded) > MAX_PACKAGE_BYTES * 2:
        raise ValueError("Package exceeds size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded, validate=True))) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) > 1000:
                raise ValueError("Package exceeds 1000 files")
            _validate_file_paths(item.filename for item in members)
            files: dict[str, str] = {}
            total = 0
            for item in members:
                path = safe_path(item.filename)
                if item.orig_filename != path:
                    raise ValueError("Unsafe package ZIP path")
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError("Package symlinks are not supported")
                if item.flag_bits & (0x01 | 0x20 | 0x40) or item.compress_type not in {
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                }:
                    raise ValueError("Unsupported organization ZIP member")
                total += item.file_size
                if total > MAX_PACKAGE_BYTES or len(files) >= 1000 or path in files:
                    raise ValueError("Package exceeds limits or has duplicate paths")
                # An unbounded ZipFile.read can expand dishonest size headers
                # before ZipExtFile truncates its output to the declared size.
                # BZIP2/LZMA also ignore read limits inside their decompressors.
                with archive.open(item) as source:
                    files[path] = base64.b64encode(source.read(item.file_size + 1)).decode()
            return files
    except (zipfile.BadZipFile, OSError, EOFError, zlib.error, lzma.LZMAError) as exc:
        raise ValueError("Invalid organization ZIP") from exc


def markdown(content: bytes) -> tuple[dict[str, Any], str]:
    text = content.decode("utf-8-sig").replace("\r\n", "\n")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("Package documents need YAML frontmatter between --- lines")
    header, body = text[4:].split("\n---\n", 1)
    # Alias expansion permits tiny YAML files to construct enormous graphs.
    try:
        depth = 0
        for token in yaml.scan(header):
            if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)):
                raise ValueError("YAML anchors and aliases are not supported in packages")
            if isinstance(
                token,
                (
                    yaml.tokens.BlockMappingStartToken,
                    yaml.tokens.BlockSequenceStartToken,
                    yaml.tokens.FlowMappingStartToken,
                    yaml.tokens.FlowSequenceStartToken,
                ),
            ):
                depth += 1
                if depth > MAX_YAML_DEPTH:
                    raise ValueError("Package YAML nesting is too deep")
            elif isinstance(
                token,
                (
                    yaml.tokens.BlockEndToken,
                    yaml.tokens.FlowMappingEndToken,
                    yaml.tokens.FlowSequenceEndToken,
                ),
            ):
                depth -= 1
        data = yaml.safe_load(header)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Frontmatter must be an object")
    return data, body.strip()


def parse_package(files: dict[str, str]) -> dict[str, Any]:
    if not isinstance(files, dict) or not all(
        isinstance(path, str) and isinstance(content, str) for path, content in files.items()
    ):
        raise ValueError("Package files must map relative paths to base64 strings")
    if len(files) > 1000:
        raise ValueError("Package exceeds 1000 files")
    _validate_file_paths(files)
    decoded = {
        safe_path(path): base64.b64decode(content, validate=True) for path, content in files.items()
    }
    if sum(map(len, decoded.values())) > MAX_PACKAGE_BYTES:
        raise ValueError("Package exceeds 16 MiB")
    standalone = "COMPANY.md" not in decoded
    if standalone:
        root = next((p for p in ("TEAM.md", "AGENTS.md") if p in decoded), None)
        if root is None:
            raise ValueError("Select a package with COMPANY.md, TEAM.md or AGENTS.md at its root")
        meta, _ = markdown(decoded[root])
        name = str(meta.get("name") or meta.get("slug") or "Imported team")
        company_text = (
            "---\n"
            + yaml.safe_dump(
                {
                    "name": name,
                    "slug": slugify(name),
                    "description": str(meta.get("description", "")),
                    "schema": "agentcompanies/v1",
                }
            )
            + "---\n"
        )
        decoded["COMPANY.md"] = company_text.encode()
        files = {**files, "COMPANY.md": base64.b64encode(decoded["COMPANY.md"]).decode()}
    docs = {
        path: markdown(content)
        for path, content in decoded.items()
        if PurePosixPath(path).name in KINDS
    }
    company, body = docs["COMPANY.md"]
    if company.get("schema", "agentcompanies/v1") not in (
        "agentcompanies/v1",
        "agentcompanies/v1-draft",
    ):
        raise ValueError("Unsupported Agent Companies schema")
    for key in ("name", "description", "slug"):
        if not isinstance(company.get(key), str):
            raise ValueError(f"COMPANY.md needs {key}")
    warnings = (
        [
            "Standalone package wrapped in a paused organization; "
            "bind local workspaces before running."
        ]
        if standalone
        else []
    )
    if ".paperclip.yaml" in files:
        warnings.append(
            "Known Paperclip local adapters and models are imported. "
            "Other Paperclip settings, including cron triggers, need configuration in Raticode."
        )
    extension: dict[str, Any] = {}
    if ".raticode.yaml" in decoded:
        extension, _ = markdown(b"---\n" + decoded[".raticode.yaml"] + b"\n---\n")
        if extension.get("schema") != "raticode/organizations/v1":
            raise ValueError("Unsupported Raticode organization extension")
        warnings.append(
            "Portable provider settings are imported; local resources must be selected again."
        )

    def resolve(source: str, reference: str) -> str:
        if not isinstance(reference, str):
            raise ValueError("Package references must be strings")
        if "://" in reference:
            raise ValueError(
                "Vendor external includes locally before import; remote includes "
                "are not fetched automatically"
            )
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source), reference))
        safe_path(target)
        if target not in decoded and not any(p.startswith(target + "/") for p in decoded):
            raise ValueError(f"Missing package reference {reference} in {source}")
        return target

    for path, (meta, _) in docs.items():
        for key in ("includes", "sources", "skills", "docs"):
            if key in meta and not isinstance(meta[key], list):
                if key != "docs" or not isinstance(meta[key], dict):
                    raise ValueError(f"Package {key} in {path} must be a list")
        for reference in meta.get("includes", []):
            if not isinstance(reference, (str, dict)):
                raise ValueError(f"Package includes in {path} must contain paths")
            resolve(path, reference if isinstance(reference, str) else reference.get("path", ""))
        for source in meta.get("sources", []):
            if isinstance(source, dict) and not isinstance(source.get("kind", ""), str):
                raise ValueError(f"Package sources in {path} must have string kinds")
            if isinstance(source, dict) and source.get("kind", "").startswith("github-"):
                commit = str(source.get("commit", ""))
                if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit.lower()):
                    raise ValueError(
                        "GitHub source references require immutable 40-character commits"
                    )
    agents = {
        path: str(meta.get("slug") or PurePosixPath(path).parent.name)
        for path, (meta, _) in docs.items()
        if path == "AGENTS.md" or path.endswith("/AGENTS.md")
    }
    if len(set(agents.values())) != len(agents):
        raise ValueError("Duplicate employee slugs")

    def employee_ref(source: str, value: Any) -> str | None:
        if value is None:
            return None
        if value in agents.values():
            return str(value)
        target = resolve(source, value)
        if target not in agents:
            raise ValueError("Reporting references must identify AGENTS.md")
        return agents[target]

    employees = []
    for path, eid in agents.items():
        meta, instructions = docs[path]
        skill_names = meta.get("skills", [])
        for skill in skill_names:
            if f"skills/{skill}/SKILL.md" not in docs:
                raise ValueError(f"Unresolved skill: {skill}")
        extra_docs = meta.get("docs", [])
        if isinstance(extra_docs, dict):
            extra_docs = list(extra_docs.values())
        for doc in extra_docs:
            target = resolve(path, doc)
            if target not in decoded:
                raise ValueError(f"Package document reference {doc} in {path} must identify a file")
            instructions += f"\n\n## {doc}\n\n" + decoded[target].decode("utf-8")
        employees.append(
            {
                "id": eid,
                "name": meta.get("name", eid),
                "title": meta.get("title", ""),
                "role": meta.get("title", ""),
                "reportsTo": employee_ref(path, meta.get("reportsTo")),
                "instructions": instructions,
                "skills": skill_names,
                "metadata": meta,
            }
        )
    runtime_keys = {
        "provider",
        "model",
        "effort",
        "permissionMode",
        "role",
        "paused",
        "heartbeatSeconds",
        "monthlyBudgetUsd",
        "monthlyTurnLimit",
    }
    adapter_types = {
        "codex_local": "codex",
        "claude_local": "claude_code",
        "cursor": "cursor",
        "opencode_local": "opencode",
    }
    paperclip: dict[str, Any] = {}
    if ".paperclip.yaml" in decoded:
        paperclip, _ = markdown(b"---\n" + decoded[".paperclip.yaml"] + b"\n---\n")
    for settings_file, settings_document, keys in (
        (".raticode.yaml", extension, ("company", "agents", "routines")),
        (".paperclip.yaml", paperclip, ("agents",)),
    ):
        for key in keys:
            values = settings_document.get(key, {})
            if not isinstance(values, dict):
                raise ValueError(f"Package {settings_file} {key} must be an object")
            if key in {"agents", "routines"} and not all(
                isinstance(value, dict) for value in values.values()
            ):
                raise ValueError(f"Package {settings_file} {key} entries must be objects")
    for employee in employees:
        settings = extension.get("agents", {}).get(employee["id"], {})
        employee.update({key: value for key, value in settings.items() if key in runtime_keys})
        adapter = paperclip.get("agents", {}).get(employee["id"], {}).get("adapter", {})
        if not isinstance(adapter, dict) or not isinstance(adapter.get("config", {}), dict):
            raise ValueError("Package Paperclip adapter and config must be objects")
        adapter_type = adapter.get("type", "")
        if not isinstance(adapter_type, str):
            raise ValueError("Package Paperclip adapter type must be a string")
        provider = adapter_types.get(adapter_type)
        if provider and not settings:
            employee["provider"] = provider
            employee["permissionMode"] = "workspace-write" if provider == "codex" else "default"
            if adapter.get("config", {}).get("model"):
                employee["model"] = adapter["config"]["model"]
    projects, teams, tasks = [], [], []
    for path, (meta, text) in docs.items():
        slug = str(meta.get("slug") or PurePosixPath(path).parent.name)
        if path.endswith("/PROJECT.md"):
            projects.append(
                {
                    **meta,
                    "id": slug,
                    "name": meta.get("name", slug),
                    "owner": employee_ref(path, meta.get("owner")),
                    "body": text,
                }
            )
        if path == "TEAM.md" or path.endswith("/TEAM.md"):
            teams.append(
                {
                    **meta,
                    "id": slug,
                    "name": meta.get("name", slug),
                    "manager": employee_ref(path, meta.get("manager")),
                    "body": text,
                }
            )
        if path.endswith("/TASK.md"):
            parent_project = PurePosixPath(path).parts
            project = meta.get("project")
            if not project and parent_project[0] == "projects" and len(parent_project) >= 5:
                project = parent_project[1]
            tasks.append(
                {
                    "id": slug,
                    "title": meta.get("name", slug),
                    "description": text,
                    "assignee": employee_ref(path, meta.get("assignee")),
                    "project": project,
                    "recurring": bool(meta.get("recurring", False)),
                }
            )
    if len({task["id"] for task in tasks}) != len(tasks):
        raise ValueError("Task slugs must be unique within the company package")
    for project in projects:
        project.pop("workspacePath", None)
    for task in tasks:
        routine = extension.get("routines", {}).get(task.get("id"), {})
        if "intervalSeconds" in routine:
            task["intervalSeconds"] = routine["intervalSeconds"]
    company_settings = {
        key: value
        for key, value in extension.get("company", {}).items()
        if key
        in {
            "maxTaskTurns",
            "turnTimeoutSeconds",
            "maxConcurrency",
            "monthlyBudgetUsd",
            "monthlyTurnLimit",
        }
    }
    config = OrganizationConfig.model_validate(
        {
            "name": company["name"],
            "description": company["description"],
            "slug": company["slug"],
            "instructions": body,
            "goals": company.get("goals", []),
            "employees": employees,
            "projects": projects,
            "teams": teams,
            "metadata": company,
            "packageFiles": files,
            **company_settings,
        }
    ).model_dump()
    return {
        "config": config,
        "tasks": tasks,
        "warnings": warnings,
        "specification": "agentcompanies/v1-draft",
    }


def export_package(config: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, str]:
    files = dict(config.get("packageFiles", {}))
    # Portable exports contain no local provider resources, paths, env bindings or secrets.
    files.pop(".raticode.yaml", None)
    files.pop(".paperclip.yaml", None)
    for path in list(files):
        if PurePosixPath(path).name in KINDS - {"SKILL.md"}:
            del files[path]

    def write(path: str, meta: dict[str, Any], body: str) -> None:
        content = (
            "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + body
        )
        files[safe_path(path)] = base64.b64encode(content.encode()).decode()

    meta = {
        **config.get("metadata", {}),
        "schema": "agentcompanies/v1",
        "kind": "company",
        "name": config["name"],
        "description": config["description"],
        "slug": config["slug"],
        "goals": config["goals"],
    }
    meta.pop("includes", None)  # All referenced content is vendored in this export.
    write("COMPANY.md", meta, config["instructions"])
    for e in config["employees"]:
        meta = {
            **e["metadata"],
            "name": e["name"],
            "slug": e["id"],
            "title": e["title"],
            "reportsTo": e["reportsTo"],
            "skills": e["skills"],
        }
        meta.pop("docs", None)  # Sibling instructions were folded into canonical body at import.
        meta.pop("includes", None)
        write(f"agents/{e['id']}/AGENTS.md", meta, e["instructions"])
    for kind, entries, filename in (
        ("projects", config["projects"], "PROJECT.md"),
        ("teams", config["teams"], "TEAM.md"),
    ):
        for entry in entries:
            meta = {
                k: v
                for k, v in entry.items()
                if k not in {"body", "id", "includes", "workspacePath"}
            }
            meta["slug"] = entry["id"]
            write(f"{kind}/{entry['id']}/{filename}", meta, entry.get("body", ""))
    task_slugs: dict[str, str] = {}
    used_slugs: set[str] = set()
    for task in tasks:
        base_slug = slugify(task["id"])
        slug = base_slug
        suffix = 2
        while slug in used_slugs:
            ending = f"-{suffix}"
            slug = base_slug[: 100 - len(ending)] + ending
            suffix += 1
        used_slugs.add(slug)
        task_slugs[task["id"]] = slug
        write(
            f"tasks/{slug}/TASK.md",
            {
                "name": task["title"],
                "assignee": task["assignee"],
                "project": task["project"],
                "recurring": task["recurring"],
            },
            task["description"],
        )
    extension = {
        "schema": "raticode/organizations/v1",
        "company": {
            key: config[key]
            for key in (
                "maxTaskTurns",
                "turnTimeoutSeconds",
                "maxConcurrency",
                "monthlyBudgetUsd",
                "monthlyTurnLimit",
            )
        },
        "agents": {
            e["id"]: {
                key: e[key]
                for key in (
                    "provider",
                    "model",
                    "effort",
                    "permissionMode",
                    "role",
                    "paused",
                    "heartbeatSeconds",
                    "monthlyBudgetUsd",
                    "monthlyTurnLimit",
                )
            }
            for e in config["employees"]
        },
        "routines": {
            task_slugs[t["id"]]: {"intervalSeconds": t["intervalSeconds"]}
            for t in tasks
            if t["recurring"]
        },
    }
    files[".raticode.yaml"] = base64.b64encode(yaml.safe_dump(extension).encode()).decode()
    return files


def zip_package(files: dict[str, str]) -> str:
    _validate_file_paths(files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(safe_path(path), base64.b64decode(content, validate=True))
    return base64.b64encode(buffer.getvalue()).decode()


PAPERCLIP_REFERENCE = "01d9a121859a3d8298dce91452f75516e837e819"


def github_package(repository: str, commit: str, directory: str = "") -> dict[str, str]:
    """Fetch a bounded archive at an immutable revision; never execute package code."""
    import re
    from urllib.request import HTTPRedirectHandler, build_opener

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("GitHub repository must be owner/name")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("GitHub imports require an immutable 40-character commit")
    if directory:
        safe_path(directory)

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(
            self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
        ) -> None:
            raise ValueError("Package archive redirects are not allowed")

    url = f"https://codeload.github.com/{repository}/zip/{commit}"
    with build_opener(NoRedirect()).open(url, timeout=20) as response:
        archive = read_response_bytes(response, MAX_PACKAGE_BYTES)
    if len(archive) > MAX_PACKAGE_BYTES:
        raise ValueError("GitHub archive exceeds 16 MiB")
    files = read_zip(base64.b64encode(archive).decode())
    prefixes = {path.split("/", 1)[0] for path in files}
    if len(prefixes) != 1:
        raise ValueError("GitHub archive must have one root")
    prefix = next(iter(prefixes)) + "/" + (directory + "/" if directory else "")
    selected = {
        path.removeprefix(prefix): value for path, value in files.items() if path.startswith(prefix)
    }
    if not selected:
        raise ValueError("Package directory not found in the pinned archive")
    return selected
