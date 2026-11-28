"""Converting old TOML workflows to Rattish (``Raticode rattish convert``)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import anyio
import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.run_service import run_rattish_file
from gofer.rattish.toml_convert import (
    TomlConversionError,
    convert_toml_into_project,
    convert_toml_workflow,
    legacy_toml_workflow_files,
)

runner = CliRunner()

# Shaped like the six TOML workflows people made before Rattish: commands, scripts,
# files, local search, agents with prompt files, and all three edge kinds.
SUPPORT_TRIAGE = """
[workflow]
id = "support-triage"
name = "Support Triage Digest"
max_total_node_runs = 20

[agents.digest-writer]
subscription = "codex"
working_dir = "."
prompt_path = "prompts/digest.md"
tools = []
mcp_servers = []

[[nodes]]
type = "bash_command"
command = "mkdir -p out\\nprintf '[{\\"id\\":\\"T-1\\"}]' > out/tickets.json"
working_dir = "."
id = "seed-tickets"
pipe_output = true
timeout_seconds = 5.0

[nodes.env]

[[nodes]]
type = "read_file"
path = "out/tickets.json"
id = "read-ticket-json"
pipe_output = true

[[nodes]]
type = "python_script"
script_path = "triage.py"
args = []
id = "score-and-format-digest"
pipe_output = true
timeout_seconds = 5.0

[[nodes]]
type = "write_file"
path = "out/digest.md"
content = ""
create_dirs = true
id = "write-digest"
pipe_output = true

[[nodes]]
type = "bash_command"
command = "echo escalate"
id = "flag-escalation"

[[nodes]]
type = "agent"
agent_id = "digest-writer"
prompt_path = "prompts/digest.md"
working_dir = "."
id = "summarize-for-manager"
timeout_seconds = 120.0

[nodes.input_mapping]
triage_digest = "score-and-format-digest.output"
report_path = "write-digest.output"

[[nodes]]
type = "open_resource"
target = "out"
resource_type = "folder"
id = "open-digest-folder"

[[edges]]
from = "seed-tickets"
to = "read-ticket-json"
condition = "on_success"

[[edges]]
from = "read-ticket-json"
to = "score-and-format-digest"
condition = "on_success"

[[edges]]
from = "score-and-format-digest"
to = "write-digest"
condition = "on_success"

[[edges]]
from = "score-and-format-digest"
to = "flag-escalation"
condition = "output_matches"
output_pattern = "ESCALATE_NOW"

[[edges]]
from = "write-digest"
to = "summarize-for-manager"
condition = "on_success"

[[edges]]
from = "write-digest"
to = "open-digest-folder"
condition = "on_success"
"""

FAIL_NODE_OK = """
[workflow]
id = "fail-node-ok"
name = "Fail Node Ok"

[agents.agent-1]
subscription = "claude_code"
working_dir = "."
prompt_path = "prompts/agent-1.md"

[[nodes]]
type = "agent"
agent_id = "agent-1"
prompt_path = "prompts/agent-1.md"
working_dir = "."
id = "node-1"
allow_failure = true

[nodes.input_mapping]
"""

FILE_IO = """
edges = [
    { from = "reset", to = "read-source", condition = "on_success" },
    { from = "read-source", to = "copy-source", condition = "on_success" },
    { from = "copy-source", to = "move-copy", condition = "on_success" },
    { from = "move-copy", to = "write-report", condition = "on_success" },
    { from = "write-report", to = "append-report", condition = "on_success" },
    { from = "append-report", to = "delete-scratch", condition = "on_success" },
]

[workflow]
id = "file-io"
name = "File I/O Demo"

[[nodes]]
type = "bash_command"
command = "mkdir -p work"
id = "reset"
pipe_output = true

[[nodes]]
type = "read_file"
path = "inbox/source.txt"
encoding = "utf-8"
errors = "strict"
id = "read-source"
pipe_output = true

[[nodes]]
type = "copy_file"
source_path = "inbox/source.txt"
destination_path = "work/copy.txt"
create_dirs = true
overwrite = false
id = "copy-source"

[[nodes]]
type = "move_file"
source_path = "work/copy.txt"
destination_path = "archive/copy.txt"
id = "move-copy"

[[nodes]]
type = "write_file"
path = "outbox/report.md"
content = "# Report\\n\\nWritten by write_file.\\n"
overwrite = true
id = "write-report"

[[nodes]]
type = "write_file"
path = "outbox/report.md"
content = "\\nAppended.\\n"
append = true
id = "append-report"

[[nodes]]
type = "delete_file"
path = "work/delete-me.tmp"
use_trash = true
missing_ok = true
id = "delete-scratch"
"""

GOFER_DEMO = """
[workflow]
id = "gofer-demo"
name = "Gofer Demo"

[agents.narrator]
subscription = "codex"
working_dir = "."
prompt_path = "prompts/narrator.md"

[[nodes]]
type = "bash_command"
command = "printf 'repo=%s\\\\n' \\"$(basename \\"$PWD\\")\\""
id = "collect-context"
pipe_output = true
retry_count = 1
timeout_seconds = 5.0

[nodes.env]
DEMO_MODE = "lightweight"

[[nodes]]
type = "python_script"
script_path = "metrics.py"
id = "generate-metrics"
pipe_output = true

[[nodes]]
type = "shell_script"
script_path = "format.sh"
id = "format-brief"
pipe_output = true

[[nodes]]
type = "agent"
agent_id = "narrator"
working_dir = "."
id = "summarize-demo"

[nodes.input_mapping]
context = "collect-context.output"
metrics = "generate-metrics.output"
brief = "format-brief.output"

[[nodes]]
type = "bash_command"
command = "echo marker"
id = "marker-note"

[[nodes]]
type = "bash_command"
command = "echo fallback"
id = "fallback-note"

[[edges]]
from = "collect-context"
to = "generate-metrics"
condition = "on_success"

[[edges]]
from = "generate-metrics"
to = "format-brief"
condition = "on_success"

[[edges]]
from = "format-brief"
to = "summarize-demo"
condition = "on_success"

[[edges]]
from = "format-brief"
to = "marker-note"
condition = "output_matches"
output_pattern = "DEMO_READY"

[[edges]]
from = "format-brief"
to = "fallback-note"
condition = "on_failure"
"""

LOCAL_SEARCH = """
edges = [
    { from = "seed-corpus", to = "index-corpus", condition = "on_success" },
    { from = "index-corpus", to = "search-incidents", condition = "on_success" },
    { from = "index-corpus", to = "search-controls", condition = "on_success" },
    { from = "search-incidents", to = "write-brief", condition = "on_success" },
    { from = "search-controls", to = "write-brief", condition = "on_success" },
    { from = "write-brief", to = "analyze-results", condition = "on_success" },
]

[workflow]
id = "local-search"
name = "Local Vector and Search"

[agents.analyst]
subscription = "codex"
working_dir = "."
prompt_path = "prompts/analyst.md"

[[nodes]]
type = "bash_command"
command = "mkdir -p corpus && echo '# Playbook' > corpus/a.md"
id = "seed-corpus"

[[nodes]]
type = "local_vectorize"
source_path = "corpus"
index_path = "indexes/corpus.json"
glob = "**/*.md"
recursive = true
chunk_size = 700
chunk_overlap = 80
id = "index-corpus"
pipe_output = true

[[nodes]]
type = "local_search"
index_path = "indexes/corpus.json"
query = "checkout incident"
top_k = 3
id = "search-incidents"
pipe_output = true

[[nodes]]
type = "local_search"
index_path = "indexes/corpus.json"
query = "certificate rotation"
top_k = 3
id = "search-controls"
pipe_output = true

[[nodes]]
type = "prompt_file"
output_path = "outbox/brief.md"
template = "# Brief\\n\\n```json\\n{{_piped_input}}\\n```\\n"
id = "write-brief"
pipe_output = true

[nodes.variables]

[[nodes]]
type = "agent"
agent_id = "analyst"
working_dir = "."
id = "analyze-results"

[nodes.input_mapping]
incident_results = "search-incidents.output"
brief_path = "write-brief.output"
"""

SKILLS_DEMO = """
[workflow]
id = "skills-demo"
name = "Skills Demo"
max_total_node_runs = 10

[agents.skill-runner]
subscription = "codex"
working_dir = "."

[agents.skill-runner.env]
SKILL_DEMO = "true"

[[nodes]]
type = "bash_command"
command = "echo context"
id = "prepare"
pipe_output = true

[[nodes]]
type = "agent"
agent_id = "skill-runner"
working_dir = "."
skill_name = "raticode-workflow-builder"
id = "invoke-skill"

[nodes.input_mapping]
demo_context = "prepare.output"

[[edges]]
from = "prepare"
to = "invoke-skill"
condition = "on_success"
"""

PROMPTS = {
    "digest.md": "Triage digest:\n\n{{triage_digest}}\n\nReport: {{report_path}}\n",
    "agent-1.md": "File: {{file_path}}\n\n{{file_content}}\n",
    "narrator.md": "Context:\n{{context}}\n\nMetrics:\n{{metrics}}\n\nBrief:\n{{brief}}\n",
    "analyst.md": "Summarize the incident results and the brief.\n",
}

SHAPES = {
    "support-triage": SUPPORT_TRIAGE,
    "fail-node-ok": FAIL_NODE_OK,
    "file-io": FILE_IO,
    "gofer-demo": GOFER_DEMO,
    "local-search": LOCAL_SEARCH,
    "skills-demo": SKILLS_DEMO,
}


def _toml(folder: Path, name: str, text: str) -> Path:
    prompts = folder / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    for prompt_name, prompt in PROMPTS.items():
        (prompts / prompt_name).write_text(prompt, encoding="utf-8")
    path = folder / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _check(tmp_path: Path, source: str) -> list[str]:
    """Compile the way ``Raticode rattish check`` does; return error codes."""
    folder = tmp_path / "checked"
    folder.mkdir(exist_ok=True)
    entrypoint = folder / "workflow.rattish"
    entrypoint.write_text(source, encoding="utf-8")
    try:
        artifact = compile_rattish_file(entrypoint, data_dir=tmp_path / "data")
    except RattishError as exc:
        return [item.code for item in exc.diagnostics if item.severity == "error"]
    return [item["code"] for item in artifact.diagnostics if item["severity"] == "error"]


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_old_workflow_shape_converts_and_passes_check(tmp_path: Path, shape: str) -> None:
    path = _toml(tmp_path / "old", shape, SHAPES[shape])

    conversion = convert_toml_workflow(path)

    assert conversion.unsupported == ()
    assert conversion.source.startswith("rattish: 1\n")
    assert _check(tmp_path, conversion.source) == []


def test_edges_bindings_and_prompts_keep_their_meaning(tmp_path: Path) -> None:
    source = convert_toml_workflow(_toml(tmp_path, "triage", SUPPORT_TRIAGE)).source

    # A hyphen-separated "and" would be read as a predicate, so the ID changes.
    assert "node score-plus-format-digest:" in source
    assert "score-and-format" not in source
    assert (
        'flag-escalation when node.score-plus-format-digest.output.stdout matches "ESCALATE_NOW"'
        in (source)
    )
    # Piped output becomes an explicit binding on the main output field.
    assert "stdin: node.read-ticket-json.output.content" in source
    assert "stdin: node.score-plus-format-digest.output.stdout" in source
    # The prompt file is inlined with Rattish placeholder names.
    assert "{{triage-digest}}" in source and "{{report-path}}" in source
    assert "triage-digest: node.score-plus-format-digest.output.stdout" in source
    # Relative paths resolved from the TOML file's folder still point there.
    tickets = str((tmp_path / "out" / "tickets.json").resolve())
    assert json.dumps(tickets, ensure_ascii=False) in source
    assert "max-runs: 20" in source

    demo = convert_toml_workflow(_toml(tmp_path, "demo", GOFER_DEMO)).source
    assert "allow-fail: true" in demo
    assert "summarize-demo when succeeded" in demo
    assert "fallback-note when failed" in demo
    assert "retry-count: 1" in demo
    assert '"DEMO_MODE": "lightweight"' in demo


def test_a_step_with_several_incoming_edges_waits_for_all_and_joins_piped_text(
    tmp_path: Path,
) -> None:
    source = convert_toml_workflow(_toml(tmp_path, "search", LOCAL_SEARCH)).source

    assert "needs:\n    - search-incidents\n    - search-controls" in source
    assert "{{search-incidents}}\n    {{search-controls}}" in source


def test_unbound_placeholders_become_inputs(tmp_path: Path) -> None:
    conversion = convert_toml_workflow(_toml(tmp_path, "fail", FAIL_NODE_OK))

    assert "file-path:" in conversion.source and "file-content:" in conversion.source
    assert any("workflow input file-path" in note for note in conversion.notes)
    assert "provider: claude-code" in conversion.source


def test_a_converted_workflow_runs_its_branches_and_joins_once(tmp_path: Path) -> None:
    toml = """
[workflow]
id = "join"
name = "Join"

[[nodes]]
id = "start-here"
type = "bash_command"
command = "echo hello"
pipe_output = true

[[nodes]]
id = "shout"
type = "bash_command"
command = "tr a-z A-Z"

[[nodes]]
id = "breaks"
type = "bash_command"
command = "exit 3"

[[nodes]]
id = "recover"
type = "bash_command"
command = "echo recovered"

[[nodes]]
id = "join"
type = "bash_command"
command = "echo joined"

[[edges]]
from = "start-here"
to = "shout"
condition = "on_success"

[[edges]]
from = "start-here"
to = "breaks"

[[edges]]
from = "breaks"
to = "recover"
condition = "on_failure"

[[edges]]
from = "shout"
to = "join"

[[edges]]
from = "recover"
to = "join"
"""
    conversion = convert_toml_workflow(_toml(tmp_path, "join", toml))
    folder = tmp_path / "wf"
    folder.mkdir()
    (folder / "workflow.rattish").write_text(conversion.source, encoding="utf-8")

    result = anyio.run(
        lambda: run_rattish_file(folder / "workflow.rattish", data_dir=tmp_path / "data")
    )

    assert result.status == "passed", result.document.get("error")
    ran = [item["node_id"] for item in result.document["runs"]]
    assert sorted(ran) == ["breaks", "join", "recover", "shout", "start-here"]
    outputs = result.document["latest_node_outputs"]
    assert outputs["shout"]["stdout"].strip() == "HELLO"


def test_parts_without_a_rattish_form_are_listed_not_dropped(tmp_path: Path) -> None:
    toml = """
[workflow]
id = "rich"
name = "Rich"
run_continuously = true

[[nodes]]
id = "fan"
type = "loop"
source = { type = "count", count = 2 }

[[nodes]]
id = "child"
type = "subflow"
component_id = "summarize"

[[nodes]]
id = "uses-trigger"
type = "bash_command"
command = "echo {{trigger.path}}"
"""
    conversion = convert_toml_workflow(_toml(tmp_path, "rich", toml))

    joined = "\n".join(conversion.unsupported)
    assert "node fan: loops aren't converted" in joined
    assert "node child: subflows aren't converted" in joined
    assert "placeholder {{trigger.path}}" in joined
    assert "run_continuously" in joined
    # The result still compiles, so a partial conversion can be finished by hand.
    assert _check(tmp_path, conversion.source) == []


def test_plaintext_secrets_never_reach_the_rattish_source(tmp_path: Path) -> None:
    toml = """
[workflow]
id = "secrets"
name = "Secrets"

[[nodes]]
id = "call"
type = "http_request"
url = "https://example.com/api"
headers = { Authorization = "Bearer sk-live-very-secret" }

[[nodes]]
id = "deploy"
type = "bash_command"
command = "deploy --token $API_TOKEN {{secret.OTHER_KEY}}"

[nodes.env]
API_TOKEN = "tok-plain-12345"
"""
    conversion = convert_toml_workflow(_toml(tmp_path, "secrets", toml))

    assert "sk-live-very-secret" not in conversion.source
    assert "tok-plain-12345" not in conversion.source
    assert 'secret["API_TOKEN"]' in conversion.source
    assert 'secret["AUTHORIZATION"]' in conversion.source
    assert 'secret["OTHER_KEY"]' in conversion.source
    notes = "\n".join(conversion.notes)
    assert "Raticode rattish secrets set API_TOKEN" in notes
    assert "Raticode rattish secrets set AUTHORIZATION" in notes
    assert _check(tmp_path, conversion.source) == []


def test_schedules_watches_and_webhooks_become_trigger_commands(tmp_path: Path) -> None:
    toml = """
[workflow]
id = "triggered"
name = "Triggered"

[workflow.schedule]
cron_expression = "0 9 * * 1-5"
timezone = "Europe/Berlin"

[workflow.watch]
path = "inbox"
glob = "*.md"
recursive = true

[workflow.webhooks.github]
id = "github"
enabled = true
token_env = "HOOK_TOKEN"

[[nodes]]
id = "work"
type = "bash_command"
command = "echo hi"
"""
    path = _toml(tmp_path, "triggered", toml)
    conversion = convert_toml_workflow(path)
    notes = "\n".join(conversion.notes)

    assert (
        'Raticode rattish triggers add cron workflow.rattish --cron "0 9 * * 1-5" '
        "--timezone Europe/Berlin"
    ) in notes
    assert 'triggers add file workflow.rattish --path inbox --glob "*.md" --recursive' in notes
    assert "triggers add webhook workflow.rattish --label github" in notes
    assert conversion.unsupported == ()

    project = tmp_path / "project"
    project.mkdir()
    registered, placed = convert_toml_into_project(path, project, registry_dir=tmp_path / "reg")
    # Quoted for the shell, with the folder's letters as they are.
    entrypoint = json.dumps(str(registered.entrypoint), ensure_ascii=False)
    assert f"triggers add cron {entrypoint} " in "\n".join(placed.notes)


def test_converting_into_a_project_registers_it_and_leaves_the_toml_alone(
    tmp_path: Path,
) -> None:
    path = _toml(tmp_path / "old", "demo", GOFER_DEMO)
    before = path.read_bytes()
    os.utime(path, (1_000_000, 1_000_000))
    project = tmp_path / "project"
    project.mkdir()

    registered, conversion = convert_toml_into_project(path, project, registry_dir=tmp_path / "reg")

    assert registered.entrypoint.read_text(encoding="utf-8") == conversion.source
    assert registered.entrypoint.is_relative_to(project / ".raticode")
    assert registered.name == "Gofer Demo"
    assert path.read_bytes() == before
    assert path.stat().st_mtime == 1_000_000


def test_converting_into_a_project_refuses_a_partial_result_unless_allowed(
    tmp_path: Path,
) -> None:
    toml = '[workflow]\nid = "x"\nname = "X"\n\n[[nodes]]\nid = "fan"\ntype = "loop"\n'
    path = _toml(tmp_path, "partial", toml)
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(TomlConversionError) as raised:
        convert_toml_into_project(path, project, registry_dir=tmp_path / "reg")
    assert any("loops aren't converted" in item for item in raised.value.problems)
    assert not (project / ".raticode").exists()

    registered, _ = convert_toml_into_project(
        path, project, registry_dir=tmp_path / "reg", allow_partial=True
    )
    assert registered.entrypoint.is_file()


def test_a_file_that_is_not_a_toml_workflow_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.toml"
    broken.write_text("[workflow\n", encoding="utf-8")
    other = tmp_path / "pyproject.toml"
    other.write_text('[project]\nname = "x"\n', encoding="utf-8")

    with pytest.raises(TomlConversionError, match="isn't valid TOML"):
        convert_toml_workflow(broken)
    with pytest.raises(TomlConversionError, match="isn't a Raticode workflow"):
        convert_toml_workflow(other)
    with pytest.raises(TomlConversionError, match="Can't read"):
        convert_toml_workflow(tmp_path / "missing.toml")


def test_leftover_toml_workflows_are_found_at_the_top_of_the_data_folder(
    tmp_path: Path,
) -> None:
    _toml(tmp_path, "b-demo", GOFER_DEMO)
    _toml(tmp_path, "a-skills", SKILLS_DEMO)
    (tmp_path / "settings.toml").write_text('[ui]\ntheme = "dark"\n', encoding="utf-8")
    (tmp_path / "broken.toml").write_text("nodes = [", encoding="utf-8")
    nested = tmp_path / "projects"
    nested.mkdir()
    _toml(nested, "nested", GOFER_DEMO)

    assert legacy_toml_workflow_files(tmp_path) == [
        tmp_path / "a-skills.toml",
        tmp_path / "b-demo.toml",
    ]
    assert legacy_toml_workflow_files(tmp_path / "missing") == []


def test_cli_convert_prints_writes_and_refuses_partial_results(tmp_path: Path) -> None:
    path = _toml(tmp_path, "demo", GOFER_DEMO)

    printed = runner.invoke(app, ["rattish", "convert", str(path)])
    assert printed.exit_code == 0, printed.output
    assert printed.stdout.startswith("rattish: 1\n")

    output = tmp_path / "converted" / "workflow.rattish"
    written = runner.invoke(
        app, ["rattish", "convert", str(path), "--output", str(output), "--format", "json"]
    )
    assert written.exit_code == 0, written.output
    payload = json.loads(written.stdout)
    assert payload["ok"] is True and payload["output"] == str(output)
    assert output.read_text(encoding="utf-8").startswith("rattish: 1\n")

    again = runner.invoke(app, ["rattish", "convert", str(path), "--output", str(output)])
    assert again.exit_code == 2
    assert "already exists" in again.output

    partial = _toml(
        tmp_path,
        "partial",
        '[workflow]\nid = "p"\nname = "P"\n\n[[nodes]]\nid = "l"\ntype = "loop"\n',
    )
    target = tmp_path / "partial.rattish"
    refused = runner.invoke(app, ["rattish", "convert", str(partial), "--output", str(target)])
    assert refused.exit_code == 1
    assert "Not converted: node l: loops aren't converted" in refused.output
    assert not target.exists()

    project = tmp_path / "project"
    project.mkdir()
    registered = runner.invoke(
        app,
        [
            "rattish",
            "convert",
            str(path),
            "--project",
            str(project),
            "--registry-dir",
            str(tmp_path / "reg"),
            "--format",
            "json",
        ],
    )
    assert registered.exit_code == 0, registered.output
    workflow = json.loads(registered.stdout)["workflow"]
    assert Path(workflow["sourcePath"]).is_file()
