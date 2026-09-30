from __future__ import annotations

import json
import os
import sys
import tomllib
import zipfile
from io import StringIO
from pathlib import Path

import pytest
import tomli_w
from rich.console import Console

import gofer.core.bundles as bundles
from gofer.cli.commands import workflow as workflow_cmd
from gofer.core.bundles import (
    BundleError,
    export_workflow_bundle,
    import_workflow_bundle,
    preview_workflow_bundle,
)
from gofer.core.resources import ResourceLimits
from gofer.core.validation import validate_workflow_file
from gofer.core.workflow import AgenticWorkflow


@pytest.mark.parametrize(
    "manifest",
    [
        '{"formatVersion":1,"workflow":{"id":"review","name":"Review"},"extra":'
        + "[" * (sys.getrecursionlimit() + 100)
        + "0"
        + "]" * (sys.getrecursionlimit() + 100)
        + "}",
        "[]",
        '{"workflow":"invalid"}',
        '{"workflow":{},"includedPaths":null}',
        '{"workflow":{},"formatVersion":{}}',
        '{"workflow":{},"includedPaths":[{"path":"asset.txt"}]}',
        '{"workflow":{},"includedPaths":[{"archivePath":"asset.txt"}]}',
        '{"workflow":{},"includedPaths":[{"path":[],"archivePath":"asset.txt"}]}',
    ],
    ids=[
        "deep",
        "array",
        "workflow-type",
        "paths-type",
        "version-type",
        "missing-archive-path",
        "missing-path",
        "path-value-type",
    ],
)
def test_bundle_rejects_malformed_manifest_before_creating_files(
    tmp_path: Path, manifest: str
) -> None:
    bundle = tmp_path / "invalid.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr("workflow.toml", '[workflow]\nid="review"\nname="Review"\n')
    target = tmp_path / "target"
    with pytest.raises(BundleError, match="manifest"):
        preview_workflow_bundle(bundle, data_dir=target)
    with pytest.raises(BundleError, match="manifest"):
        import_workflow_bundle(bundle, data_dir=target)
    assert not target.exists()


def _write_bundle_source(base: Path) -> Path:
    (base / "prompts").mkdir()
    (base / "scripts").mkdir()
    (base / "prompts" / "hello.md").write_text("Say {{secret.API_TOKEN}}\n")
    (base / "scripts" / "hello.sh").write_text("echo hello\n")
    workflow_path = base / "hello.toml"
    workflow_path.write_text(
        """
[workflow]
id = "hello"
name = "Hello"

[agents.bot]
subscription = "codex"
working_dir = "."
prompt_path = "prompts/hello.md"

[[nodes]]
id = "script"
type = "shell_script"
script_path = "scripts/hello.sh"

[[nodes]]
id = "agent"
type = "agent"
agent_id = "bot"
working_dir = "."
prompt_path = "prompts/hello.md"

[[edges]]
from = "script"
to = "agent"
""".strip()
        + "\n"
    )
    return workflow_path


def test_export_import_bundle_round_trips_and_validates(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    workflow_path = _write_bundle_source(source_dir)
    bundle_path = tmp_path / "hello.gof.zip"

    manifest = export_workflow_bundle(workflow_path, bundle_path)

    assert manifest.workflow_id == "hello"
    assert {item["path"] for item in manifest.included_paths} == {
        "prompts/hello.md",
        "scripts/hello.sh",
    }
    assert manifest.required_secrets == [
        {
            "name": "API_TOKEN",
            "description": "Required by prompts/hello.md",
        }
    ]

    plan = import_workflow_bundle(bundle_path, data_dir=target_dir)

    assert plan.workflow_id == "hello"
    assert (target_dir / "hello.toml").exists()
    assert (target_dir / "prompts" / "hello.md").read_text() == "Say {{secret.API_TOKEN}}\n"
    assert validate_workflow_file(target_dir / "hello.toml", data_dir=target_dir).ok


@pytest.mark.parametrize("destination", ["prompts/hello.md", "hello.toml"])
def test_bundle_replacement_does_not_modify_a_hardlinked_file_outside_the_target(
    tmp_path: Path, destination: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    workflow_path = _write_bundle_source(source)
    bundle = tmp_path / "hello.zip"
    export_workflow_bundle(workflow_path, bundle)
    target = tmp_path / "target"
    entry = target / destination
    entry.parent.mkdir(parents=True)
    protected = tmp_path / "private.txt"
    protected.write_text("Private content outside the import folder")
    entry.hardlink_to(protected)

    import_workflow_bundle(bundle, data_dir=target, replace=True)

    assert protected.read_text() == "Private content outside the import folder"
    assert entry.read_bytes() != protected.read_bytes()
    assert validate_workflow_file(target / "hello.toml", data_dir=target).ok


@pytest.mark.parametrize("destination", ["prompts/hello.md", "hello.toml"])
@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks needs Windows developer mode")
def test_bundle_import_does_not_follow_a_destination_replaced_with_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    bundle = tmp_path / "hello.zip"
    export_workflow_bundle(_write_bundle_source(source), bundle)
    target = tmp_path / "target"
    entry = target / destination
    entry.parent.mkdir(parents=True)
    entry.write_text("Original destination")
    protected = tmp_path / "private.txt"
    protected.write_text("Private content outside the import folder")
    original_read = zipfile.ZipExtFile.read
    swapped = False

    def replace_after_read(source, *args, **kwargs):
        nonlocal swapped
        content = original_read(source, *args, **kwargs)
        if source.name == "assets/prompts/hello.md" and not swapped:
            swapped = True
            entry.unlink()
            entry.symlink_to(protected)
        return content

    monkeypatch.setattr(zipfile.ZipExtFile, "read", replace_after_read)
    import_workflow_bundle(bundle, data_dir=target, replace=True)

    assert swapped
    assert protected.read_text() == "Private content outside the import folder"
    assert not entry.is_symlink()
    assert validate_workflow_file(target / "hello.toml", data_dir=target).ok


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_bundle_replacement_keeps_existing_script_executable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    bundle = tmp_path / "hello.zip"
    export_workflow_bundle(_write_bundle_source(source), bundle)
    target = tmp_path / "target"
    script = target / "scripts/hello.sh"
    script.parent.mkdir(parents=True)
    script.write_text("echo old script\n")
    script.chmod(0o751)

    import_workflow_bundle(bundle, data_dir=target, replace=True)

    assert script.read_text() == "echo hello\n"
    assert script.stat().st_mode & 0o7777 == 0o751


def test_bundle_round_trips_structured_output_schemas_and_predicates(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    workflow_path = source_dir / "typed.toml"
    workflow_path.write_text(
        """
[workflow]
id = "typed"
name = "Typed"

[workflow.output_schemas.result]
"$ref" = "#/$defs/result"

[workflow.output_schemas.result."$defs".result]
type = "object"
required = ["verdict"]

[workflow.output_schemas.result."$defs".result.properties.verdict]
type = "string"

[agents.reviewer]
subscription = "codex"
working_dir = "."

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "."
output_schema = "result"

[[nodes]]
id = "done"
type = "bash_command"
command = "true"

[[edges]]
from = "review"
to = "done"
condition = "output_field"
field = "verdict"
operator = "equals"
value = "approved"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    bundle_path = tmp_path / "typed.gof.zip"

    export_workflow_bundle(workflow_path, bundle_path)
    with zipfile.ZipFile(bundle_path) as archive:
        bundled = archive.read("workflow.toml").decode("utf-8")
    assert "output_schemas" in bundled
    assert 'condition = "output_field"' in bundled

    import_workflow_bundle(bundle_path, data_dir=target_dir)
    imported = AgenticWorkflow.from_file(target_dir / "typed.toml")
    edge = imported.graph.get_edge_config("review", "done")
    assert imported.config.output_schemas["result"]["$ref"] == "#/$defs/result"
    assert edge.field == "verdict"
    assert edge.operator is not None and edge.operator.value == "equals"


def test_bundle_round_trips_workflow_scopes_and_invocation_bindings(tmp_path: Path) -> None:
    source_dir = tmp_path / "scope-source"
    target_dir = tmp_path / "scope-target"
    source_dir.mkdir()
    workflow_path = source_dir / "scoped.toml"
    workflow_path.write_text(
        """
[workflow]
id = "scoped"
name = "Scoped"

[workflow.inputs.project_dir]
type = "path"
required = true

[workflow.inputs.child_id]
type = "string"
default = "child"

[workflow.variables.attempt]
type = "number"
initial = 1

[workflow.schedule]
cron_expression = "0 9 * * *"
inputs = { project_dir = "/scheduled" }

[workflow.watch]
path = "incoming"
inputs = { project_dir = "/watched" }

[workflow.webhooks.default]
enabled = true
allow_unauthenticated = true

[workflow.webhooks.default.input_bindings]
project_dir = "{{trigger.payload.project_dir}}"

[[nodes]]
id = "workflow-child"
type = "workflow"
workflow_id = "{{inputs.child_id}}"
input_bindings = { project_dir = "{{inputs.project_dir}}" }

[[nodes]]
id = "subflow-child"
type = "subflow"
component_id = "{{inputs.child_id}}"
input_bindings = { project_dir = "{{inputs.project_dir}}" }
output_contract = { attempt = "{{vars.attempt}}" }
""".strip()
        + "\n",
        encoding="utf-8",
    )
    bundle_path = tmp_path / "scoped.gof.zip"

    export_workflow_bundle(workflow_path, bundle_path)
    import_workflow_bundle(bundle_path, data_dir=target_dir)

    imported = AgenticWorkflow.from_file(target_dir / "scoped.toml")
    assert imported.config.inputs["project_dir"].required
    assert imported.config.variables["attempt"].initial == 1
    assert imported.config.schedule is not None
    assert imported.config.schedule.inputs == {"project_dir": "/scheduled"}
    assert imported.config.watch is not None
    assert imported.config.watch.inputs == {"project_dir": "/watched"}
    assert imported.config.webhooks["default"].input_bindings == {
        "project_dir": "{{trigger.payload.project_dir}}"
    }
    workflow_call = imported.graph._nodes["workflow-child"].operation
    subflow_call = imported.graph._nodes["subflow-child"].operation
    assert workflow_call.input_bindings == {"project_dir": "{{inputs.project_dir}}"}
    assert subflow_call.input_bindings == {"project_dir": "{{inputs.project_dir}}"}


def test_import_bundle_conflicts_rename_workflow_and_asset_paths(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    workflow_path = _write_bundle_source(source_dir)
    bundle_path = tmp_path / "hello.gof.zip"
    export_workflow_bundle(workflow_path, bundle_path)
    (target_dir / "hello.toml").write_text(
        '[workflow]\nid = "hello"\nname = "Existing"\n',
        encoding="utf-8",
    )
    (target_dir / "prompts").mkdir()
    (target_dir / "prompts" / "hello.md").write_text("existing\n")

    plan = import_workflow_bundle(bundle_path, data_dir=target_dir)

    assert plan.workflow_id == "hello-2"
    assert plan.path_rewrites["prompts/hello.md"] == "bundle-assets/hello-2/prompts/hello.md"
    assert (target_dir / "hello-2.toml").exists()
    assert (target_dir / "bundle-assets" / "hello-2" / "prompts" / "hello.md").exists()
    imported_toml = (target_dir / "hello-2.toml").read_text()
    assert 'id = "hello-2"' in imported_toml
    assert 'prompt_path = "bundle-assets/hello-2/prompts/hello.md"' in imported_toml


def test_export_bundle_marks_missing_and_absolute_paths_external(tmp_path: Path) -> None:
    workflow_path = tmp_path / "external.toml"
    workflow_path.write_text(
        f"""
[workflow]
id = "external"
name = "External"

[[nodes]]
id = "missing"
type = "shell_script"
script_path = "missing.sh"

[[nodes]]
id = "absolute"
type = "python_script"
script_path = "{tmp_path / "outside.py"}"
""".strip()
        + "\n"
    )

    manifest = export_workflow_bundle(workflow_path, tmp_path / "external.zip")

    requirements = {item["path"]: item["reason"] for item in manifest.external_requirements}
    assert requirements["missing.sh"] == "referenced path was missing during export"
    assert requirements[str(tmp_path / "outside.py")] == (
        "absolute or user-relative path is machine-specific"
    )


def test_import_bundle_dry_run_reports_without_writing(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    workflow_path = _write_bundle_source(source_dir)
    bundle_path = tmp_path / "hello.gof.zip"
    export_workflow_bundle(workflow_path, bundle_path)

    plan = import_workflow_bundle(bundle_path, data_dir=target_dir, dry_run=True)

    assert "hello.toml" in plan.files_to_create
    assert not target_dir.exists()


def test_import_bundle_preview_reports_directory_files(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    (source_dir / "samples" / "nested").mkdir(parents=True)
    (source_dir / "samples" / "one.txt").write_text("one\n")
    (source_dir / "samples" / "nested" / "two.txt").write_text("two\n")
    workflow_path = source_dir / "copy.toml"
    workflow_path.write_text(
        """
[workflow]
id = "copy-samples"
name = "Copy Samples"

[[nodes]]
id = "copy"
type = "copy_file"
source_path = "samples"
destination_path = "out/samples"
""".strip()
        + "\n"
    )
    bundle_path = tmp_path / "copy-samples.zip"
    export_workflow_bundle(workflow_path, bundle_path)
    (target_dir / "samples").mkdir(parents=True)
    (target_dir / "samples" / "one.txt").write_text("existing\n")

    plan = preview_workflow_bundle(bundle_path, data_dir=target_dir)

    assert "copy-samples.toml" in plan.files_to_create
    assert "bundle-assets/copy-samples/samples/nested/two.txt" in plan.files_to_create
    assert "bundle-assets/copy-samples/samples/one.txt" in plan.files_to_create
    assert "samples" not in plan.files_to_create
    assert "samples" not in plan.files_to_overwrite


def test_export_bundle_includes_local_vector_index_sidecar(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    (source_dir / "indexes").mkdir()
    index_path = source_dir / "indexes" / "docs.json"
    entries_path = source_dir / "indexes" / "docs.json.entries.jsonl"
    index_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries_file": entries_path.name,
                "entries": [],
            }
        )
        + "\n"
    )
    entries_path.write_text('{"path":"docs/a.md","text":"hello"}\n')
    workflow_path = source_dir / "search.toml"
    workflow_path.write_text(
        """
[workflow]
id = "search"
name = "Search"

[[nodes]]
id = "search"
type = "local_search"
index_path = "indexes/docs.json"
query = "hello"
""".strip()
        + "\n"
    )
    bundle_path = tmp_path / "search.zip"

    manifest = export_workflow_bundle(workflow_path, bundle_path)

    assert {item["path"] for item in manifest.included_paths} == {
        "indexes/docs.json",
        "indexes/docs.json.entries.jsonl",
    }
    with zipfile.ZipFile(bundle_path) as archive:
        assert "assets/indexes/docs.json.entries.jsonl" in archive.namelist()

    import_workflow_bundle(bundle_path, data_dir=target_dir)

    assert (target_dir / "indexes" / "docs.json").exists()
    assert (target_dir / "indexes" / "docs.json.entries.jsonl").read_text() == (
        '{"path":"docs/a.md","text":"hello"}\n'
    )


def test_export_bundle_manifest_includes_trigger_preview_data(tmp_path: Path) -> None:
    workflow_path = tmp_path / "triggered.toml"
    workflow_path.write_text(
        """
[workflow]
id = "triggered"
name = "Triggered"

[workflow.schedule]
cron_expression = "0 9 * * *"
timezone = "America/New_York"

[workflow.watch]
path = "incoming"
glob = "*.md"
mode = "fanout"

[workflow.webhooks.github]
enabled = true
source = "github"
token_env = "GITHUB_WEBHOOK_TOKEN"
fanout_path = "payload.items"
""".strip()
        + "\n"
    )

    manifest = export_workflow_bundle(workflow_path, tmp_path / "triggered.zip")

    assert manifest.triggers == [
        {"type": "schedule", "cron": "0 9 * * *", "timezone": "America/New_York"},
        {"type": "watch", "path": "incoming", "glob": "*.md", "mode": "fanout"},
        {
            "type": "webhook",
            "id": "github",
            "source": "github",
            "enabled": "true",
            "concurrencyPolicy": "allow",
            "tokenConfigured": "true",
            "allowUnauthenticated": "false",
            "risk": "normal",
            "tokenEnv": "GITHUB_WEBHOOK_TOKEN",
            "fanoutPath": "payload.items",
        },
    ]
    assert manifest.required_secrets == [
        {
            "name": "GITHUB_WEBHOOK_TOKEN",
            "description": "Required by trigger:github.token_env",
        }
    ]


def test_import_bundle_preview_warns_for_unauthenticated_webhook_opt_in(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "local.toml"
    workflow_path.write_text(
        """
[workflow]
id = "local"
name = "Local"

[workflow.webhooks.default]
enabled = true
allow_unauthenticated = true

[[nodes]]
id = "start"
type = "pass"
message = "ok"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    bundle_path = tmp_path / "local.zip"

    manifest = export_workflow_bundle(workflow_path, bundle_path)
    plan = preview_workflow_bundle(bundle_path, data_dir=tmp_path / "imported")

    assert manifest.triggers[0]["risk"] == "high"
    assert manifest.triggers[0]["riskReasons"] == "unauthenticated_allowed"
    assert any("allows unauthenticated requests" in warning for warning in plan.risk_warnings)
    assert any(
        "allows unauthenticated requests" in warning for warning in plan.to_dict()["riskWarnings"]
    )


def test_cli_bundle_import_plan_prints_risk_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    output = StringIO()
    monkeypatch.setattr(
        workflow_cmd,
        "console",
        Console(file=output, force_terminal=False, width=160),
    )

    workflow_cmd._print_bundle_import_plan(
        {
            "workflowId": "local-hook",
            "workflowPath": "/tmp/local-hook.toml",
            "riskWarnings": [
                "Webhook trigger 'github' explicitly allows unauthenticated requests; "
                "only use this for local testing.",
            ],
        },
        dry_run=True,
    )

    text = output.getvalue()
    assert "High-risk configuration:" in text
    assert "explicitly allows unauthenticated requests" in text


def test_export_bundle_sanitizes_http_secret_fields(tmp_path: Path) -> None:
    workflow_path = tmp_path / "http.toml"
    workflow_path.write_text(
        (
            """
[workflow]
id = "http"
name = "HTTP"

[[nodes]]
id = "call"
type = "http_request"
method = "POST"
url = "https://api.example.test/items?token=real-token"
headers = { Authorization = "Bearer real-token", Accept = "application/json" }
params = { api_key = "real-api-key", query = "public" }
body = "password=body-secret visible=ok"
secret_fields = ["headers.Authorization", "json.password", "url", "api_key", "password"]
"""
            + 'json = { password = "cleartext-secret", nested = { token = "nested-token" }, '
            + 'safe = "{{secret.API_TOKEN}}" }\n'
        ).strip()
        + "\n"
    )
    bundle_path = tmp_path / "http.zip"

    manifest = export_workflow_bundle(workflow_path, bundle_path)

    assert manifest.required_secrets == [
        {"name": "API_TOKEN", "description": "Required by workflow.toml"}
    ]
    with zipfile.ZipFile(bundle_path) as archive:
        bundled_workflow = archive.read("workflow.toml").decode("utf-8")

    assert "Bearer real-token" not in bundled_workflow
    assert "token=real-token" not in bundled_workflow
    assert "cleartext-secret" not in bundled_workflow
    assert "nested-token" not in bundled_workflow
    assert "real-api-key" not in bundled_workflow
    assert "body-secret" not in bundled_workflow
    assert "{{secret.API_TOKEN}}" in bundled_workflow


def test_export_bundle_sanitizes_default_http_secret_field_names(tmp_path: Path) -> None:
    workflow_path = tmp_path / "http-defaults.toml"
    workflow_path.write_text(
        """
[workflow]
id = "http-defaults"
name = "HTTP Defaults"

[[nodes]]
id = "call"
type = "http_request"
url = "https://api.example.test/items?token=query-secret&visible=ok"
headers = { Authorization = "Bearer header-secret", Accept = "application/json" }
params = { password = "param-secret", query = "public" }
json = { secret = "json-secret", safe = "public" }
""".strip()
        + "\n"
    )
    bundle_path = tmp_path / "http-defaults.zip"

    export_workflow_bundle(workflow_path, bundle_path)

    with zipfile.ZipFile(bundle_path) as archive:
        bundled_workflow = archive.read("workflow.toml").decode("utf-8")

    assert "query-secret" not in bundled_workflow
    assert "Bearer header-secret" not in bundled_workflow
    assert "param-secret" not in bundled_workflow
    assert "json-secret" not in bundled_workflow
    assert "visible=ok" in bundled_workflow
    assert 'query = "public"' in bundled_workflow
    assert 'safe = "public"' in bundled_workflow


@pytest.mark.parametrize(
    "field",
    [
        "api_key",
        "apikey",
        "access-key",
        "access_key",
        "auth",
        "credential",
        "credentials",
        "passwd",
        "private_key",
        "x_api_key",
        "Proxy-Authorization",
        "Set-Cookie",
    ],
)
def test_export_bundle_masks_common_credential_names_and_copies(tmp_path: Path, field: str) -> None:
    source = tmp_path / "credentials.toml"
    source.write_text(
        tomli_w.dumps(
            {
                "workflow": {"id": "credentials", "name": "Credentials"},
                "nodes": [
                    {
                        "id": "call",
                        "type": "http_request",
                        "url": f"https://example.test/?{field}=private-value",
                        "headers": {field: "private-value"},
                        "params": {field: "private-value"},
                        "json": {"nested": {field: "private-value"}, "copy": "private-value"},
                        "body": f"{field}=private-value visible=ok",
                    }
                ],
            }
        )
    )
    output = tmp_path / "credentials.zip"
    export_workflow_bundle(source, output)
    with zipfile.ZipFile(output) as archive:
        exported = archive.read("workflow.toml").decode()
    assert "private-value" not in exported
    assert "visible=ok" in exported


def test_export_bundle_masks_env_literals_containing_secret_references(tmp_path: Path) -> None:
    source = tmp_path / "env.toml"
    source.write_text(
        tomli_w.dumps(
            {
                "workflow": {"id": "env", "name": "Env"},
                "agents": {
                    "bot": {
                        "subscription": "codex",
                        "working_dir": ".",
                        "env": {"API_KEY": "private-value {{secret.OTHER}}"},
                    }
                },
                "nodes": [
                    {
                        "id": "run",
                        "type": "bash_command",
                        "command": "echo ok",
                        "env": {
                            "PASSWORD": "{{secret.OTHER}} private-password",
                            "SAFE_TOKEN": "{{secret.SAFE}}",
                            "DEBUG": "true",
                        },
                    }
                ],
            }
        )
    )
    output = tmp_path / "env.zip"
    manifest = export_workflow_bundle(source, output)
    with zipfile.ZipFile(output) as archive:
        exported = archive.read("workflow.toml").decode()
    assert "private-value" not in exported
    assert "private-password" not in exported
    assert "{{secret.SAFE}}" in exported
    assert 'DEBUG = "true"' in exported
    assert {item["name"] for item in manifest.required_secrets} == {
        "API_KEY",
        "PASSWORD",
        "OTHER",
        "SAFE",
    }


@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks needs Windows developer mode")
@pytest.mark.parametrize("kind", ["file", "directory", "dangling", "fifo"])
def test_export_bundle_rejects_linked_and_special_directory_assets(
    tmp_path: Path, kind: str
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    samples = source_dir / "samples"
    samples.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "private.txt"
    protected.write_text("{{secret.PRIVATE_OUTSIDE}}")
    asset = samples / "asset"
    if kind == "fifo":
        os.mkfifo(asset)
    else:
        target = {"file": protected, "directory": outside, "dangling": outside / "missing"}[kind]
        asset.symlink_to(target, target_is_directory=kind == "directory")
    source = source_dir / "copy.toml"
    source.write_text(
        '[workflow]\nid="copy"\nname="Copy"\n'
        '[[nodes]]\nid="copy"\ntype="copy_file"\n'
        'source_path="samples"\ndestination_path="out"\n'
    )
    output = tmp_path / "copy.zip"
    with pytest.raises(BundleError, match="ordinary file or directory"):
        export_workflow_bundle(source, output)
    assert not output.exists()


@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks needs Windows developer mode")
def test_export_bundle_rejects_asset_replaced_after_secret_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_bundle_source(tmp_path)
    asset = tmp_path / "scripts/hello.sh"
    protected = tmp_path / "private.txt"
    protected.write_text("private content")
    output = tmp_path / "existing.zip"
    output.write_bytes(b"previous export")
    write = bundles._write_bundle_asset

    def swap(archive: zipfile.ZipFile, path: Path, name: str) -> None:
        if path == asset:
            asset.unlink()
            asset.symlink_to(protected)
        write(archive, path, name)

    monkeypatch.setattr(bundles, "_write_bundle_asset", swap)
    with pytest.raises(BundleError, match="Could not read bundle asset"):
        export_workflow_bundle(source, output)
    assert output.read_bytes() == b"previous export"


def test_export_bundle_streams_assets_with_pre_zip_timestamps(tmp_path: Path) -> None:
    source = _write_bundle_source(tmp_path)
    asset = tmp_path / "scripts/hello.sh"
    content = b"echo ok\n" * 200000
    asset.write_bytes(content)
    os.utime(asset, (0, 0))
    output = tmp_path / "old-assets.zip"
    export_workflow_bundle(source, output)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("assets/scripts/hello.sh") == content
        assert archive.getinfo("assets/scripts/hello.sh").date_time == (1980, 1, 1, 0, 0, 0)


def test_export_bundle_removes_url_credentials_and_their_copies(tmp_path: Path) -> None:
    workflow_path = tmp_path / "url-credentials.toml"
    workflow_path.write_text(
        """
[workflow]
id = "url-credentials"
name = "URL credentials"

[[nodes]]
id = "call"
type = "http_request"
url = "https://private-user:private%2Dpassword@[2606:4700:4700::1111]:8443/items?visible=ok"
headers = { "X-Copy" = "private-password" }
json = { copy = "private-user", visible = "public" }
""".strip()
        + "\n"
    )
    bundle = tmp_path / "credentials.zip"
    export_workflow_bundle(workflow_path, bundle)
    with zipfile.ZipFile(bundle) as archive:
        source = archive.read("workflow.toml").decode()
    assert "private-user" not in source
    assert "private-password" not in source
    assert "private%2Dpassword" not in source
    operation = tomllib.loads(source)["nodes"][0]
    assert operation["url"] == "https://[2606:4700:4700::1111]:8443/items?visible=ok"
    assert operation["headers"]["X-Copy"] == "***"
    assert operation["json"] == {"copy": "***", "visible": "public"}


def test_export_bundle_omits_secret_scope_literals_and_preserves_bindings(tmp_path: Path) -> None:
    workflow_path = tmp_path / "scope-secrets.toml"
    workflow_path.write_text(
        """
[workflow]
id = "scope-secrets"
name = "Scope secrets"

[workflow.inputs.credential]
type = "secret"
default = "private-default"
required = true

[workflow.parameters.pin]
type = "number"
secret = true
default = 987654321

[workflow.inputs.visible]
default = "public-default"

[workflow.variables.credential]
secret = true
initial = "private-variable"

[workflow.variables.reference]
secret = true
initial = "{{inputs.credential}}"

[workflow.schedule]
cron_expression = "0 9 * * *"
inputs = { credential = "private-schedule", visible = "public-schedule" }
params = { pin = 876543210 }

[workflow.watch]
path = "."
params = { credential = "private-watch" }

[workflow.webhooks.default]
input_bindings = { credential = "{{trigger.payload.credential}}", pin = 765432109 }

[component]
id = "scope-secrets"

[component.inputs.credential]
secret = true
default = "private-component"

[[nodes]]
id = "run"
type = "bash_command"
command = "echo ok"
""".strip()
        + "\n"
    )
    bundle = tmp_path / "scope-secrets.zip"
    export_workflow_bundle(workflow_path, bundle)
    with zipfile.ZipFile(bundle) as archive:
        source = archive.read("workflow.toml").decode()
    assert "private-" not in source
    for pin in (987654321, 876543210, 765432109):
        assert str(pin) not in source
    exported = tomllib.loads(source)
    assert exported["workflow"]["inputs"]["credential"]["required"] is True
    assert "default" not in exported["workflow"]["inputs"]["credential"]
    assert "default" not in exported["workflow"]["parameters"]["pin"]
    assert "default" not in exported["component"]["inputs"]["credential"]
    assert exported["workflow"]["schedule"]["inputs"] == {"visible": "public-schedule"}
    assert exported["workflow"]["watch"]["params"] == {}
    assert exported["workflow"]["webhooks"]["default"]["input_bindings"] == {
        "credential": "{{trigger.payload.credential}}"
    }
    target = tmp_path / "imported"
    import_workflow_bundle(bundle, data_dir=target)
    workflow = AgenticWorkflow.from_file(target / "scope-secrets.toml")
    assert workflow.config.inputs["credential"].default is None
    assert workflow.config.variables["reference"].initial == "{{inputs.credential}}"
    assert validate_workflow_file(target / "scope-secrets.toml", data_dir=target).ok
    # Export must leave the source workflow and its credentials intact.
    assert AgenticWorkflow.from_file(workflow_path).config.inputs["credential"].default == (
        "private-default"
    )


def test_export_bundle_sanitizes_nested_subflow_sources(tmp_path: Path) -> None:
    parent = tmp_path / "parent.toml"
    parent.write_text(
        '[workflow]\nid = "parent"\nname = "Parent"\n'
        '[[nodes]]\nid = "child"\ntype = "subflow"\ncomponent_id = "child"\n'
        '[[nodes]]\nid = "script"\ntype = "shell_script"\nscript_path = "child.toml"\n'
    )
    child = tmp_path / "child.toml"
    child.write_text(
        '[workflow]\nid = "child"\nname = "Child"\n'
        '[workflow.inputs.credential]\ntype = "secret"\ndefault = "private-child"\n'
        '[[nodes]]\nid = "grandchild"\ntype = "subflow"\ncomponent_id = "grandchild"\n'
    )
    grandchild = tmp_path / "grandchild.toml"
    grandchild.write_text(
        '[workflow]\nid = "grandchild"\nname = "Grandchild"\n'
        '[[nodes]]\nid = "run"\ntype = "bash_command"\ncommand = "echo ok"\n'
        'env = { API_TOKEN = "private-grandchild", VISIBLE = "public" }\n'
    )
    bundle = tmp_path / "parent.zip"
    manifest = export_workflow_bundle(parent, bundle)
    with zipfile.ZipFile(bundle) as archive:
        for name in ("workflow.toml", "assets/child.toml", "assets/grandchild.toml"):
            assert "private-" not in archive.read(name).decode()
        exported_child = tomllib.loads(archive.read("assets/child.toml").decode())
        assert "default" not in exported_child["workflow"]["inputs"]["credential"]
        exported_grandchild = tomllib.loads(archive.read("assets/grandchild.toml").decode())
        assert exported_grandchild["nodes"][0]["env"] == {"API_TOKEN": "***", "VISIBLE": "public"}
    assert manifest.required_secrets == [
        {"name": "API_TOKEN", "description": "Required by grandchild.toml"}
    ]
    target = tmp_path / "imported"
    import_workflow_bundle(bundle, data_dir=target)
    assert (
        AgenticWorkflow.from_file(target / "child.toml").config.inputs["credential"].default is None
    )
    assert "private-child" in child.read_text()
    assert "private-grandchild" in grandchild.read_text()


def test_export_bundle_sanitizes_notification_secret_fields(tmp_path: Path) -> None:
    workflow_path = tmp_path / "notify.toml"
    workflow_path.write_text(
        """
[workflow]
id = "notify"
name = "Notify"

[[nodes]]
id = "slack"
type = "notification"
channel = "slack"
webhook_url = "https://hooks.example.test/services/T000/B000/secret-token?visible=ok"
headers = { Authorization = "Bearer header-secret" }
payload = { password = "payload-secret", safe = "{{secret.SAFE_VALUE}}" }

[[nodes]]
id = "email"
type = "notification"
channel = "email"
email_from = "gofer@example.test"
email_to = ["ops@example.test"]
smtp_host = "smtp.example.test"
smtp_username = "smtp-user"
smtp_password = "smtp-secret"
""".strip()
        + "\n"
    )
    bundle_path = tmp_path / "notify.zip"

    manifest = export_workflow_bundle(workflow_path, bundle_path)

    assert manifest.required_secrets == [
        {"name": "SAFE_VALUE", "description": "Required by workflow.toml"}
    ]
    with zipfile.ZipFile(bundle_path) as archive:
        bundled_workflow = archive.read("workflow.toml").decode("utf-8")

    assert "secret-token" not in bundled_workflow
    assert "header-secret" not in bundled_workflow
    assert "payload-secret" not in bundled_workflow
    assert "smtp-user" not in bundled_workflow
    assert "smtp-secret" not in bundled_workflow
    assert "{{secret.SAFE_VALUE}}" in bundled_workflow


def test_export_bundle_sanitizes_agent_and_node_env_secrets(tmp_path: Path) -> None:
    workflow_path = tmp_path / "env-secrets.toml"
    workflow_path.write_text(
        """
[workflow]
id = "env-secrets"
name = "Env Secrets"

[agents.bot]
subscription = "codex"
working_dir = "."
env = { OPENAI_API_KEY = "sk-real-secret", DEBUG = "true" }

[[nodes]]
id = "run"
type = "shell_script"
script_path = "run.sh"
env = { PASSWORD = "cleartext-password", LOG_LEVEL = "info" }
""".strip()
        + "\n"
    )
    (tmp_path / "run.sh").write_text("echo ok\n")
    bundle_path = tmp_path / "env-secrets.zip"

    manifest = export_workflow_bundle(workflow_path, bundle_path)

    assert manifest.required_secrets == [
        {"name": "OPENAI_API_KEY", "description": "Required by workflow.toml"},
        {"name": "PASSWORD", "description": "Required by workflow.toml"},
    ]
    with zipfile.ZipFile(bundle_path) as archive:
        bundled_workflow = archive.read("workflow.toml").decode("utf-8")

    assert "sk-real-secret" not in bundled_workflow
    assert "cleartext-password" not in bundled_workflow
    assert 'OPENAI_API_KEY = "***"' in bundled_workflow
    assert 'PASSWORD = "***"' in bundled_workflow
    assert 'DEBUG = "true"' in bundled_workflow
    assert 'LOG_LEVEL = "info"' in bundled_workflow


def test_import_bundle_rejects_unsafe_archive_entries(tmp_path: Path) -> None:
    bundle_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(bundle_path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "formatVersion": 1,
                    "workflow": {"id": "unsafe", "name": "Unsafe"},
                    "includedPaths": [],
                    "requiredSecrets": [],
                    "providerAssumptions": [],
                    "externalRequirements": [],
                }
            ),
        )
        archive.writestr("workflow.toml", '[workflow]\nid = "unsafe"\nname = "Unsafe"\n')
        archive.writestr("../outside.txt", "nope")

    with pytest.raises(BundleError, match="Unsafe bundle path"):
        preview_workflow_bundle(bundle_path, data_dir=tmp_path / "target")

    assert not (tmp_path / "outside.txt").exists()


def _write_minimal_bundle(
    bundle_path: Path,
    *,
    workflow_id: str = "limited",
    workflow_body: str | None = None,
    included_paths: list[dict[str, str]] | None = None,
    files: dict[str, str | bytes] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> None:
    manifest = {
        "formatVersion": 1,
        "workflow": {"id": workflow_id, "name": "Limited"},
        "includedPaths": included_paths or [],
        "requiredSecrets": [],
        "providerAssumptions": [],
        "externalRequirements": [],
    }
    workflow_body = workflow_body or (f'[workflow]\nid = "{workflow_id}"\nname = "Limited"\n')
    with zipfile.ZipFile(bundle_path, "w", compression=compression) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("workflow.toml", workflow_body)
        for name, data in (files or {}).items():
            archive.writestr(name, data)


def test_import_bundle_rejects_excessive_entry_count(tmp_path: Path) -> None:
    bundle_path = tmp_path / "entries.zip"
    _write_minimal_bundle(bundle_path, files={"extra.txt": "extra\n"})

    limits = ResourceLimits(max_bundle_entries=2)

    with pytest.raises(BundleError, match="entry count exceeded limit 2 entries"):
        preview_workflow_bundle(bundle_path, data_dir=tmp_path / "target", limits=limits)

    with pytest.raises(BundleError, match="entry count exceeded limit 2 entries"):
        import_workflow_bundle(
            bundle_path,
            data_dir=tmp_path / "target",
            dry_run=True,
            limits=limits,
        )


def test_import_bundle_rejects_zip_bomb_compression_ratio(tmp_path: Path) -> None:
    bundle_path = tmp_path / "ratio.zip"
    _write_minimal_bundle(bundle_path, files={"payload.txt": "A" * 4000})

    with pytest.raises(BundleError, match="compression ratio exceeded limit 2:1"):
        preview_workflow_bundle(
            bundle_path,
            data_dir=tmp_path / "target",
            limits=ResourceLimits(max_bundle_compression_ratio=2),
        )


def test_import_bundle_rejects_data_bearing_directory_entries(tmp_path: Path) -> None:
    bundle_path = tmp_path / "data-dir.zip"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "formatVersion": 1,
                    "workflow": {"id": "limited", "name": "Limited"},
                    "includedPaths": [],
                    "requiredSecrets": [],
                    "providerAssumptions": [],
                    "externalRequirements": [],
                }
            ),
        )
        archive.writestr("workflow.toml", '[workflow]\nid = "limited"\nname = "Limited"\n')
        archive.writestr("payload/", "A" * 4000)

    with pytest.raises(BundleError, match="directory entry contains file data"):
        preview_workflow_bundle(bundle_path, data_dir=tmp_path / "target")


def test_import_bundle_rejects_excessive_total_uncompressed_size(tmp_path: Path) -> None:
    bundle_path = tmp_path / "total.zip"
    _write_minimal_bundle(
        bundle_path,
        files={
            "one.txt": "1" * 100,
            "two.txt": "2" * 100,
        },
        compression=zipfile.ZIP_STORED,
    )

    with pytest.raises(BundleError, match="total uncompressed size exceeded limit 300 bytes"):
        preview_workflow_bundle(
            bundle_path,
            data_dir=tmp_path / "target",
            limits=ResourceLimits(max_bundle_total_uncompressed_bytes=300),
        )


def test_import_bundle_rejects_oversized_metadata_entries(tmp_path: Path) -> None:
    manifest_bundle = tmp_path / "manifest-too-large.zip"
    _write_minimal_bundle(manifest_bundle)
    workflow_bundle = tmp_path / "workflow-too-large.zip"
    _write_minimal_bundle(
        workflow_bundle,
        workflow_body='[workflow]\nid = "limited"\nname = "Limited"\n' + "# pad\n" * 40,
    )

    with pytest.raises(BundleError, match="manifest\\.json size exceeded metadata limit 60 bytes"):
        preview_workflow_bundle(
            manifest_bundle,
            data_dir=tmp_path / "target",
            limits=ResourceLimits(max_bundle_metadata_bytes=60),
        )

    with pytest.raises(BundleError, match="workflow\\.toml size exceeded metadata limit 200 bytes"):
        preview_workflow_bundle(
            workflow_bundle,
            data_dir=tmp_path / "target",
            limits=ResourceLimits(max_bundle_metadata_bytes=200),
        )


def test_import_bundle_uses_bundle_limit_environment_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_path = tmp_path / "env-limits.zip"
    _write_minimal_bundle(bundle_path, files={"extra.txt": "extra\n"})
    monkeypatch.setenv("GOFER_BUNDLE_MAX_ENTRIES", "2")

    with pytest.raises(BundleError, match="entry count exceeded limit 2 entries"):
        preview_workflow_bundle(bundle_path, data_dir=tmp_path / "target")


def test_import_bundle_accepts_valid_bundle_under_resource_limits(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    workflow_path = _write_bundle_source(source_dir)
    bundle_path = tmp_path / "hello.gof.zip"
    export_workflow_bundle(workflow_path, bundle_path)

    plan = import_workflow_bundle(
        bundle_path,
        data_dir=target_dir,
        limits=ResourceLimits(
            max_bundle_entries=4,
            max_bundle_entry_bytes=4096,
            max_bundle_total_uncompressed_bytes=4096,
            max_bundle_compressed_bytes=4096,
            max_bundle_metadata_bytes=2048,
            max_bundle_compression_ratio=100,
        ),
    )

    assert plan.workflow_id == "hello"
    assert (target_dir / "hello.toml").exists()
    assert (target_dir / "prompts" / "hello.md").exists()


def test_import_bundle_rejects_before_partial_asset_writes(tmp_path: Path) -> None:
    bundle_path = tmp_path / "oversized-asset.zip"
    target_dir = tmp_path / "target"
    _write_minimal_bundle(
        bundle_path,
        workflow_id="partial",
        workflow_body=(
            '[workflow]\nid = "partial"\nname = "Partial"\n\n'
            '[[nodes]]\nid = "script"\ntype = "shell_script"\nscript_path = "scripts/run.sh"\n'
        ),
        included_paths=[
            {
                "path": "scripts/run.sh",
                "archivePath": "assets/scripts/run.sh",
                "kind": "script",
            }
        ],
        files={"assets/scripts/run.sh": "echo should-not-write\n"},
        compression=zipfile.ZIP_STORED,
    )

    with pytest.raises(BundleError, match="entry size exceeded limit 10 bytes"):
        import_workflow_bundle(
            bundle_path,
            data_dir=target_dir,
            limits=ResourceLimits(max_bundle_entry_bytes=10),
        )

    assert not target_dir.exists()
