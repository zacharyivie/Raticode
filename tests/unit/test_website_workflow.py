"""The website workflow builds and checks the site, then deploys it to GitHub Pages only from
main and only after the download check passes."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf8"))
    assert isinstance(data, dict)
    # PyYAML reads the bare `on:` key as the boolean True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _steps(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in job["steps"]}


@pytest.fixture(scope="module")
def website() -> dict[str, Any]:
    return _workflow("website.yml")


def test_only_the_deploy_job_can_write_pages(
    website: dict[str, Any],
) -> None:
    assert website["permissions"] == {"contents": "read"}
    for name, job in website["jobs"].items():
        if name != "deploy":
            assert "permissions" not in job, name
    deploy = website["jobs"]["deploy"]
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    assert deploy["environment"]["name"] == "github-pages"
    assert deploy["environment"]["url"] == "${{ steps.deployment.outputs.page_url }}"
    assert deploy["concurrency"] == {"group": "pages", "cancel-in-progress": False}


def test_deploy_runs_only_after_the_build_and_download_checks(website: dict[str, Any]) -> None:
    jobs = website["jobs"]
    deploy = jobs["deploy"]
    assert deploy["needs"] == "build"
    assert deploy["if"] == "needs.build.outputs.deploy == 'true'"
    assert re.fullmatch(r"actions/deploy-pages@[0-9a-f]{40}", deploy["steps"][0]["uses"])

    build = jobs["build"]
    assert build["outputs"]["deploy"] == "${{ steps.release.outputs.deploy }}"
    names = [step["name"] for step in build["steps"]]
    check = names.index("Type-check, build and check internal links")
    downloads = names.index("Check download links match the newest release")
    upload = names.index("Upload the site for GitHub Pages")
    assert check < downloads < upload
    steps = _steps(build)
    assert steps["Type-check, build and check internal links"]["run"] == "npm run check"
    assert "check:downloads" in steps["Check download links match the newest release"]["run"]
    upload_step = steps["Upload the site for GitHub Pages"]
    assert upload_step["if"] == "steps.release.outputs.deploy == 'true'"
    assert re.fullmatch(r"actions/upload-pages-artifact@[0-9a-f]{40}", upload_step["uses"])
    assert upload_step["with"]["path"] == "website/.vitepress/dist"


def test_site_address_comes_from_repository_variables(website: dict[str, Any]) -> None:
    env = website["jobs"]["build"]["env"]
    assert env == {
        "WEBSITE_URL": "${{ vars.WEBSITE_URL }}",
        "WEBSITE_BASE": "${{ vars.WEBSITE_BASE }}",
    }
    setup = _steps(website["jobs"]["build"])["Set up Node"]
    assert setup["with"]["node-version-file"] == ".nvmrc"


def test_deploy_follows_a_published_release(website: dict[str, Any]) -> None:
    trigger = website["on"]["workflow_run"]
    assert trigger == {"workflows": ["Publish release"], "types": ["completed"]}
    assert _workflow("release.yml")["name"] == "Publish release"
    changes = website["jobs"]["changes"]
    assert "github.event.workflow_run.conclusion == 'success'" in changes["if"]
    assert website["on"]["push"]["branches"] == ["main", "dev"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize(
    ("check_exit", "event", "ref", "step_exit", "deploy"),
    [
        (0, "push", "refs/heads/main", 0, "true"),
        (0, "workflow_run", "refs/heads/main", 0, "true"),
        (0, "workflow_dispatch", "refs/heads/main", 0, "true"),
        (0, "push", "refs/heads/dev", 0, "false"),
        (0, "workflow_dispatch", "refs/heads/dev", 0, "false"),
        (0, "schedule", "refs/heads/main", 0, "false"),
        (3, "push", "refs/heads/main", 0, "false"),
        (1, "push", "refs/heads/main", 1, None),
        (1, "workflow_run", "refs/heads/main", 1, None),
    ],
)
def test_download_check_decides_whether_to_deploy(
    website: dict[str, Any],
    tmp_path: Path,
    check_exit: int,
    event: str,
    ref: str,
    step_exit: int,
    deploy: str | None,
) -> None:
    script = _steps(website["jobs"]["build"])["Check download links match the newest release"][
        "run"
    ]
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    npm = stub_dir / "npm"
    npm.write_text(f"#!/bin/sh\nexit {check_exit}\n", encoding="utf8")
    npm.chmod(0o755)
    output = tmp_path / "output"
    output.write_text("", encoding="utf8")
    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "EVENT": event,
        "REF": ref,
    }
    result = subprocess.run(
        ["bash", "-e", "-c", script], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == step_exit, result.stderr
    lines = output.read_text(encoding="utf8").splitlines()
    if deploy is None:
        assert lines == []
    else:
        assert lines == [f"deploy={deploy}"]


def _node_with_types() -> str | None:
    node = shutil.which("node")
    if node is None:
        return None
    # Node strips TypeScript types from imported .ts files from 22.18 on.
    module = (REPO_ROOT / "website" / "data" / "site.ts").as_uri()
    probe = subprocess.run(
        [node, "--input-type=module", "-e", f"await import({json.dumps(module)})"],
        capture_output=True,
        text=True,
        check=False,
    )
    return node if probe.returncode == 0 else None


NODE = _node_with_types()


def _site_location(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    module = (REPO_ROOT / "website" / "data" / "site.ts").as_uri()
    code = (
        f"import {{ siteLocation }} from {json.dumps(module)};"
        "console.log(JSON.stringify(siteLocation(JSON.parse(process.argv[1]))))"
    )
    return subprocess.run(
        [NODE, "--input-type=module", "-e", code, json.dumps(env)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(NODE is None, reason="needs Node 22.18 or newer, which runs TypeScript")
@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, {"url": "https://zacharyivie.github.io/Raticode/", "base": "/Raticode/"}),
        (
            {"WEBSITE_URL": "", "WEBSITE_BASE": ""},
            {"url": "https://zacharyivie.github.io/Raticode/", "base": "/Raticode/"},
        ),
        ({"WEBSITE_URL": "https://raticode.dev"}, {"url": "https://raticode.dev/", "base": "/"}),
        (
            {"WEBSITE_URL": "https://raticode.dev/", "WEBSITE_BASE": "/"},
            {"url": "https://raticode.dev/", "base": "/"},
        ),
        ({"WEBSITE_BASE": "/"}, {"url": "https://zacharyivie.github.io/", "base": "/"}),
        (
            {"WEBSITE_URL": "https://example.org/docs/site/"},
            {"url": "https://example.org/docs/site/", "base": "/docs/site/"},
        ),
    ],
)
def test_site_location_reads_the_variables(env: dict[str, str], expected: dict[str, str]) -> None:
    result = _site_location(env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


@pytest.mark.skipif(NODE is None, reason="needs Node 22.18 or newer, which runs TypeScript")
@pytest.mark.parametrize(
    "env",
    [
        {"WEBSITE_URL": "http://raticode.dev/"},
        {"WEBSITE_URL": "raticode.dev"},
        {"WEBSITE_URL": "https://raticode.dev/?x=1"},
        {"WEBSITE_BASE": "Raticode"},
        {"WEBSITE_BASE": "/Raticode"},
        {"WEBSITE_URL": "https://raticode.dev/", "WEBSITE_BASE": "/Raticode/"},
    ],
)
def test_site_location_rejects_bad_or_conflicting_values(env: dict[str, str]) -> None:
    result = _site_location(env)
    assert result.returncode != 0
    assert "WEBSITE_" in result.stderr
