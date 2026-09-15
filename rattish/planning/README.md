# Rattish planning

This directory contains the working design for Rattish, the future authoring language for
Raticode workflows.

Rattish source uses the `.rattish` extension. A Rattish compiler will parse and analyze that
source, expand authoring defaults, and emit versioned JSON IR. The executor will accept
the IR and will not interpret Rattish source. Users will normally work only with `.rattish`
files and the graph and text views in Gofer Studio.

## Status

These documents are a review draft. They distinguish among three kinds of statements:

- **Accepted** records a decision made during design discussion.
- **Proposed** records a recommendation that still needs approval.
- **Open** identifies behavior that must be decided before the affected implementation
  begins.

No document in this directory is a final language specification yet.

Normative Rattish 1 drafts now live in [`../spec/`](../spec/). Planning records the design
work that feeds those drafts.

## Documents

- [01-language-design.md](01-language-design.md) defines the source-language goals,
  identifiers, draft syntax, values, paths, prompts, and schemas.
- [02-execution-semantics.md](02-execution-semantics.md) defines routing, readiness,
  branching, joins, cycles, outcomes, limits, and timeouts.
- [03-compiler-and-ir.md](03-compiler-and-ir.md) defines the compiler stages, validation
  boundary, JSON IR requirements, caching, versioning, and diagnostics.
- [04-workspace-bundles-and-editor.md](04-workspace-bundles-and-editor.md) defines workflow
  folders, metadata, `.raticode` bundles, ignore rules, and editor behavior.
- [05-migration-and-compatibility.md](05-migration-and-compatibility.md) defines the TOML
  transition and the proposed source and IR migration policy.
- [06-implementation-plan.md](06-implementation-plan.md) breaks implementation into reviewable
  phases.
- [07-open-questions.md](07-open-questions.md) lists decisions that remain unresolved.

## Naming

The following names are accepted:

| Item | Name |
|---|---|
| Language | Rattish |
| Source extension | `.rattish` |
| Portable bundle extension | `.raticode` |
| Bundle exclusion file | `.raticodeignore` |
| Suggested diagnostic prefix | `RATTISH_` |

## Design objectives

Rattish should be easy for a human or an agent to read and write without memorizing the
runtime object model. The compiler, not the author, owns normalization, generated IDs,
default expansion, static validation, and IR construction.

The design aims to provide these guarantees:

1. Every emitted IR document satisfies the versioned IR schema.
2. Every reference and dependency in emitted IR has passed semantic analysis.
3. The executor never repairs or guesses author intent.
4. An invalid source revision never replaces the last valid IR.
5. The compiler emits no requested output artifact when compilation has errors.
6. The same language version keeps the same defaults and semantics.

Rattish cannot guarantee that external commands, providers, networks, credentials, or
agent responses will succeed at runtime.

## Workflow states

The current proposal distinguishes four states:

| State | Meaning |
|---|---|
| Parsed | The source satisfies lexical and grammar rules. |
| Compiled | Static semantics pass and valid IR exists. |
| Ready | Deployment preflight finds the resources required on the current machine. |
| Running | The executor has started an immutable compiled IR revision. |

The workflow assistant should leave a created or modified workflow compiled and ready
on the user's current machine. A workflow may still compile on a machine that lacks its
provider, model, credentials, prompt files, or runtime executables.
