# Rattish editor document protocol

This protocol lets Studio analyze and save a registered `workflow.rattish` while the source
is incomplete. It does not weaken the compiler boundary: only a strict, diagnostic-free
compile produces executable IR, and only an IR that passes deployment preflight may run.

## Document analysis

Opening or analyzing a document returns:

- the source buffer, its SHA-256 revision, the saved source revision, and `dirty` state;
- the recovering AST, recoverable invalid regions, and source-linked diagnostics;
- a partial graph projection containing valid declarations and recoverable invalid nodes;
- the current strict-compilation state and fingerprint, plus the most recent valid
  fingerprint observed by this service instance;
- deployment-preflight results when strict compilation succeeded; and
- the validated `workflow.metadata.json` document and its revision.

The recovering AST and partial graph are editor models. Neither is executable IR. When
any lexer, parser, semantic, contract, or lowering error exists, `compilation.state` is
`invalid`, the current fingerprint is absent, and projected nodes do not expose partially
lowered execution configuration.

Graph nodes use canonical Rattish node names as their stable source identities. A
`projectionId` may add a `#N` suffix only to distinguish duplicate or broken declarations
in an invalid buffer. Route edges whose target is not a recovered valid declaration are
marked `unresolved` rather than discarded.

## Revisions and saves

Source and metadata saves use optimistic concurrency. The client sends the revision it
last read. The service compares it with the current persisted document while holding its
save lock and atomically replaces the file only when they match.

A conflict returns HTTP 409 with code `RATTISH_EDITOR_REVISION_CONFLICT`, the resource
(`source` or `metadata`), and both expected and current revisions. The client must reload
or explicitly merge; it must not silently retry against the new revision.

Invalid Rattish source may be saved. This preserves ordinary editor behavior and allows a
user to leave work in progress. Invalid source cannot publish executable IR or run. A
valid save may update the workflow's registry display name, but the installed workflow ID
never changes.

## Metadata

`workflow.metadata.json` is machine-owned, non-execution state validated by
[`workflow-metadata.schema.json`](../schemas/workflow-metadata.schema.json). Version 1
stores canvas positions, viewport state, and folded declarations. Node positions and
folded declarations are keyed by canonical node name. Execution meaning remains entirely
in `workflow.rattish`.

## Studio HTTP surface

- `GET /api/workflows/{id}/document` opens the persisted document.
- `POST /api/workflows/{id}/document/analyze` analyzes `{ "source": string }` without a
  write.
- `POST /api/workflows/{id}/document/save` saves
  `{ "source": string, "expectedRevision": string }`.
- `POST /api/workflows/{id}/metadata` saves
  `{ "metadata": object, "expectedRevision": string }`.

The editor source limit is 4 MiB per request. Metadata has a closed versioned schema.
