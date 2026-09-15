# Agent Instructions

This file provides guidance for AI coding agents, including Codex, Claude Code, and other agent providers, when working in this repository.

## Project Summary

`gofer-flow` is a Python CLI and desktop workflow studio. Workflows use the Rattish language in `workflow.rattish`; the compiler validates them and emits versioned JSON IR. Execution supports explicit routes, joins, branches, and cycles.

## Commands

```bash
# Install for development
pip install -e ".[dev]"

# Run all tests
python -m pytest

# Run specific test categories
python -m pytest tests/unit/
python -m pytest tests/integration/
python -m pytest tests/regression/

# Run a single test
python -m pytest tests/unit/test_executor.py::test_name -v

# Type checking
mypy src tests

# Linting and formatting
ruff check src tests --fix
ruff format src tests
```

## Architecture

Layer structure:

- `src/gofer/cli/` - Typer CLI; routes commands to workflow, agent, schedule, and builder command modules.
- `src/gofer/core/` - Domain logic: operations, graph, workflow, executor, and scheduler.
- `src/gofer/subscriptions/` - ABC for LLM CLI backends such as Claude Code and Codex. Subclasses primarily override `_build_command()`.
- `src/gofer/prompts/` - Markdown prompt templates with `{{var}}` interpolation.
- `src/gofer/utils/` - Subprocess runner, XDG paths, name-based registry, and logging.

Execution flow:

1. The Rattish lexer and parser build a source-faithful AST.
2. Semantic analysis applies machine-readable node and provider contracts.
3. The compiler emits schema-valid, versioned JSON IR with explicit defaults.
4. Preflight checks whether the compiled workflow has the resources needed to run.
5. The activation runtime executes routes, joins, branches, and cycles while preserving activation lineage.

Key patterns:

- Built-in node behavior must agree with the Rattish machine contract, compiler, preflight, runtime handler, and conformance fixtures.
- Loop nodes provide runtime fan-out with explicit concurrency and failure behavior.
- `WorkflowScheduler` wraps APScheduler with a SQLite job store persisted at `~/.local/share/gofer/schedules.db`.
- Tests use `FakeSubscription` from `tests/conftest.py` to avoid requiring real `claude` or `codex` CLIs.

## Rattish workflow format

```yaml
Rattish: 1

Workflow:
  name: My Workflow

Node step-1:
  type: bash-command
  command: echo hello
  to: step-2

Node step-2:
  type: agent
  provider: codex
  prompt: Analyze the command output.
  needs: step-1
```

## Development Rules

- Prefer the existing module boundaries and patterns before adding new abstractions.
- Keep CLI behavior covered by tests when changing command behavior.
- Keep workflow parsing and execution behavior covered by unit or regression tests when changing operation, graph, executor, or scheduler logic.
- Do not require real LLM provider CLIs in tests; use `FakeSubscription` or another test double.
- Frontend controlled inputs: do not transform or normalize a user-editable value
  on every keystroke when the displayed value is derived from the stored value,
  such as percentages, parsed numbers, paths, JSON, units, or enum-like labels.
  Keep a local draft string while the field is focused and commit on blur or
  Enter, otherwise clearing/backspacing can snap the field back to the previous
  normalized value. Add regression coverage for focus -> clear -> type -> blur
  flows.
- After code changes, run `ruff check src tests --fix`.
- After code changes, run `mypy src tests`.
- Run targeted pytest tests for the changed area; run the full suite when touching shared workflow execution, scheduling, or CLI behavior.
- To run any npm commands make sure you first use nvm to select the correct version of npm for the project.
