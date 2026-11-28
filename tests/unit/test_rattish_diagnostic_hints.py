"""Fix hints a new author sees for common Rattish mistakes, in the CLI, the studio and the phone."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from gofer.cli.commands.rattish import _grouped_diagnostics
from gofer.cli.main import app
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import (
    RattishDiagnostic,
    RattishError,
    close_names,
    did_you_mean,
)
from gofer.rattish.provider_contracts import load_provider_contracts
from gofer.ui.api import _rattish_validation_summary

RATTISH_ROOT = Path(__file__).parents[2] / "rattish"
runner = CliRunner()


def diagnostics_for(source: str, project_root: Path) -> list[RattishDiagnostic]:
    compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=sorted((RATTISH_ROOT / "contracts").glob("*.json")),
    )
    providers = load_provider_contracts(
        RATTISH_ROOT / "schemas" / "provider-contract.schema.json",
        sorted((RATTISH_ROOT / "providers").glob("*.json")),
    )
    with pytest.raises(RattishError) as caught:
        compiler.compile(
            source, CompileContext("hints", project_root, provider_contracts=providers)
        )
    return list(caught.value.diagnostics)


def only(diagnostics: list[RattishDiagnostic], code: str) -> RattishDiagnostic:
    matches = [item for item in diagnostics if item.code == code]
    assert len(matches) == 1, [item.to_json() for item in diagnostics]
    return matches[0]


def test_close_names_prefers_one_clear_match_and_treats_underscores_as_hyphens() -> None:
    assert close_names("write_file", ["write-file", "read-file"]) == ["write-file"]
    assert close_names("write-fil", ["write-file", "read-file", "prompt-file"]) == ["write-file"]
    assert close_names("claude", ["claude-code", "codex"]) == ["claude-code"]
    assert close_names("zzz", ["write-file"]) == []
    assert did_you_mean("promt", ["prompt", "prompt-path"]) == ("Did you mean 'prompt'?",)
    assert did_you_mean("model-a", ["model-b", "model-c"]) == (
        "Did you mean 'model-b' or 'model-c'?",
    )
    assert did_you_mean("zzz", ["write-file"]) == ()


def test_unknown_field_points_at_the_field_and_names_the_real_one(tmp_path: Path) -> None:
    source = (
        "Rattish: 1\nWorkflow:\n  name: Hints\nNode greet:\n  type: write-file\n"
        "  path: out.txt\n  contents: hi\n"
    )
    diagnostic = only(diagnostics_for(source, tmp_path), "RATTISH_UNKNOWN_FIELD")

    assert diagnostic.message == "Node 'greet' has no field 'contents'."
    assert diagnostic.span.start.line == 7
    assert diagnostic.suggestions[0] == "Did you mean 'content'?"
    assert diagnostic.suggestions[1].startswith("write-file fields: append, content,")
    assert diagnostic.details["field"] == "contents"


def test_unknown_provider_and_node_type_suggest_the_closest_names(tmp_path: Path) -> None:
    source = (
        "Rattish: 1\nWorkflow:\n  name: Hints\nNode ask:\n  type: agent\n"
        "  provider: claude\n  prompt: Hi\nNode save:\n  type: write-fil\n"
    )
    diagnostics = diagnostics_for(source, tmp_path)

    provider = only(diagnostics, "RATTISH_PROVIDER_CONTRACT_UNRESOLVED")
    assert provider.suggestions[0] == "Did you mean 'claude-code'?"
    assert provider.suggestions[1].startswith("Providers: ")
    assert "codex" in provider.suggestions[1]
    node_type = only(diagnostics, "RATTISH_UNKNOWN_NODE_TYPE")
    assert node_type.suggestions == ("Did you mean 'write-file'?",)


def test_misspelled_route_target_and_the_node_it_strands(tmp_path: Path) -> None:
    source = (
        "Rattish: 1\nWorkflow:\n  name: Hints\nNode greet:\n  type: bash-command\n"
        "  command: echo hi\n  to: sumary\nNode summary:\n  type: bash-command\n"
        "  command: echo done\n  needs: greet\n"
    )
    diagnostics = diagnostics_for(source, tmp_path)

    route = only(diagnostics, "RATTISH_UNRESOLVED_NODE")
    assert route.suggestions == ("Did you mean 'summary'?",)
    stranded = only(diagnostics, "RATTISH_INCOMPATIBLE_ACTIVATION_LINEAGE")
    assert stranded.message == (
        "Node 'summary' never runs: it waits for 'greet', but no route reaches it."
    )
    assert stranded.suggestions == (
        "Add 'to: summary' to the node that should start it, usually 'greet'.",
    )


@pytest.mark.parametrize(
    ("source", "hint"),
    [
        (
            "Rattish: 1\nWorkflow:\n  name: Hints\nNode a:\n  type: write_file\n",
            "Did you mean 'write-file'? Rattish names use hyphens, not underscores.",
        ),
        (
            "Rattish: 1\nWorkflow:\n  name: Hints\nNode my_step:\n  type: bash-command\n",
            "Did you mean 'my-step'? Rattish names use hyphens, not underscores.",
        ),
        (
            "Rattish: 1\nWorkflow:\n  name: Hints\nNode a:\n  type: bash-command\n"
            "  command: echo\n  working_dir: .\n",
            "Did you mean 'working-dir'? Rattish names use hyphens, not underscores.",
        ),
        (
            "Workflow:\n  name: Hints\n",
            "Start the file with the line 'Rattish: 1'.",
        ),
    ],
    ids=["node-type", "node-name", "field-name", "version-line"],
)
def test_parser_errors_say_how_to_fix_the_line(source: str, hint: str, tmp_path: Path) -> None:
    (diagnostic,) = diagnostics_for(source, tmp_path)

    assert diagnostic.suggestions == (hint,)


def test_workflow_list_summary_includes_the_did_you_mean_hint(tmp_path: Path) -> None:
    source = "Rattish: 1\nWorkflow:\n  name: Hints\nNode save:\n  type: write-fil\n"
    compiler_error = RattishError(diagnostics_for(source, tmp_path))

    assert _rattish_validation_summary(compiler_error) == (
        "Line 5: No node contract is available for 'write-fil'. Did you mean 'write-file'?"
    )


def test_text_output_prints_a_problem_repeated_on_several_nodes_once() -> None:
    def item(line: int, message: str) -> dict[str, object]:
        return {
            "file": "workflow.rattish",
            "severity": "error",
            "code": "RATTISH_PREFLIGHT_JEV_NOT_CONFIGURED",
            "message": message,
            "span": {"start": {"line": line, "column": 1}},
            "suggestions": [],
        }

    grouped = _grouped_diagnostics(
        [item(36, "No Jev key."), item(20, "No Jev key."), item(9, "Other.")]
    )

    assert [(first["span"]["start"]["line"], also) for first, also in grouped] == [  # type: ignore[index]
        (36, [20]),
        (9, []),
    ]


def test_preflight_and_starters_speak_plainly(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        "Rattish: 1\nWorkflow:\n  name: Plain\nNode a:\n  type: write-file\n"
        "  path: out.txt\n  content: hi\n",
        encoding="utf-8",
    )

    ready = runner.invoke(app, ["rattish", "preflight", str(source)])
    starters = runner.invoke(app, ["rattish", "starters"])

    assert ready.exit_code == 0, ready.output
    assert "ready to run on this computer" in ready.output
    assert "deployment" not in ready.output
    assert starters.exit_code == 0, starters.output
    assert "Needs: the claude-code provider." in starters.output
    assert "Needs: the secret ALERT_WEBHOOK_URL when used." in starters.output
    assert "Create one with: Raticode rattish create --from STARTER_ID" in starters.output


def test_did_you_mean_hints_carry_one_click_replacements(tmp_path: Path) -> None:
    def replacements(source: str, code: str) -> object:
        return only(diagnostics_for(source, tmp_path), code).details.get("replacements")

    head = "Rattish: 1\nWorkflow:\n  name: Hints\n"
    assert replacements(head + "Node a:\n  type: write_file\n", "RATTISH_INVALID_TOKEN") == [
        "write-file"
    ]
    assert replacements(
        head + "Node greet:\n  type: write-file\n  path: out.txt\n  contents: hi\n",
        "RATTISH_UNKNOWN_FIELD",
    ) == ["content"]
    assert replacements(
        head + "Node ask:\n  type: agent\n  provider: claude\n  prompt: Hi\n",
        "RATTISH_PROVIDER_CONTRACT_UNRESOLVED",
    ) == ["claude-code"]
    route = (
        head + "Node greet:\n  type: bash-command\n  command: echo hi\n  to: sumary\n"
        "Node summary:\n  type: bash-command\n  command: echo done\n  needs: greet\n"
    )
    assert replacements(route, "RATTISH_UNRESOLVED_NODE") == ["summary"]
    needs = (
        head + "Node greet:\n  type: bash-command\n  command: echo hi\n"
        "Node summary:\n  type: bash-command\n  command: echo done\n  needs:\n    - gret\n"
    )
    assert replacements(needs, "RATTISH_UNRESOLVED_NODE") == ["greet"]
    assert replacements(head + "  workspace: worktee\n", "RATTISH_INVALID_FIELD_VALUE") == [
        "worktree"
    ]
    assert replacements(head + "Node a:\n  type: write-fil\n", "RATTISH_UNKNOWN_NODE_TYPE") == [
        "write-file"
    ]


def test_replacements_point_at_the_name_and_skip_text_that_is_not_just_the_name(
    tmp_path: Path,
) -> None:
    head = "Rattish: 1\nWorkflow:\n  name: Résumé hints\n"
    source = head + "Node ask:\n  type: agent\n  provider: claude\n  prompt: Hi\n"
    provider = only(diagnostics_for(source, tmp_path), "RATTISH_PROVIDER_CONTRACT_UNRESOLVED")
    # Offsets count UTF-8 bytes, so the accented name above shifts them past the characters.
    start, end = provider.span.start.offset, provider.span.end.offset
    assert source.encode("utf-8")[start:end] == b"claude"
    assert (provider.span.start.line, provider.span.start.column) == (6, 13)

    quoted = only(
        diagnostics_for(
            head + 'Node ask:\n  type: agent\n  provider: "claude"\n  prompt: Hi\n', tmp_path
        ),
        "RATTISH_PROVIDER_CONTRACT_UNRESOLVED",
    )
    assert quoted.suggestions[0] == "Did you mean 'claude-code'?"
    assert "replacements" not in quoted.details
    capitalized = only(
        diagnostics_for(
            head + "Node a:\n  type: bash-command\n  command: echo\n  to: Sumary\n"
            "Node summary:\n  type: bash-command\n  command: echo\n",
            tmp_path,
        ),
        "RATTISH_UNRESOLVED_NODE",
    )
    # The name is compared lowercased, so the span's text isn't the name to swap out.
    assert capitalized.suggestions == ("Did you mean 'summary'?",)
    assert "replacements" not in capitalized.details
    no_match = only(
        diagnostics_for(head + "Node a:\n  type: zzzzzz\n", tmp_path), "RATTISH_UNKNOWN_NODE_TYPE"
    )
    assert "replacements" not in no_match.details
