---
title: local-vectorize
description: Split local text files into chunks and keep a search index on disk.
pageClass: docs-layout
---

# local-vectorize

Split local text files into chunks and keep a search index on disk.

## When to use it

Use it to build a search index for a workflow. It is separate from Second Brain's folder index and doesn't turn Second Brain on.

## Example

```rattish
Rattish: 1

Workflow:
  name: Build a local index

Node step:
  type: local-vectorize
  source-path: notes
  index-path: indexes/notes.json
  glob: "**/*.md"
  mode: incremental
  finish: pass
```

- Create the source folder and its text files before preflight.
- `mode` is `incremental` by default. `full` rebuilds the index, and `validate` checks whether it is up to date.
- Chunks default to 1,200 with 120 overlap. Keep the overlap smaller than the chunk size.
- The built-in strategy pair is `hash_token_v1` and `cosine_v1`. It hashes tokens locally. No remote embedding model reads the folder. Use the same pair in [local-search](./local-search.md).
- Output says whether the index is current, stale or updated, with file and chunk counts.
- Searching needs an existing, readable index, so build it before you run a separate search workflow.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `source-path` | string | Required | Source resource path. |
| `index-path` | string | Required | On-disk local retrieval index path. |
| `glob` | string | `"**/*"` | Source-file pattern. |
| `recursive` | boolean | `true` | Permit recursive operation. |
| `chunk-size` | integer (min 1) | `1200` | Chunk size used by the indexer. |
| `chunk-overlap` | integer (min 0) | `120` | Overlap between text chunks. |
| `encoding` | string | `"utf-8"` | Text encoding. |
| `mode` | `incremental`, `full`, `validate` | `"incremental"` | Index update or validation mode. |
| `embedding-strategy` | string | `"hash_token_v1"` | Installed text-to-vector strategy ID. |
| `search-strategy` | string | `"cosine_v1"` | Installed ranking strategy ID. |

## Inputs and output

No input ports. Set its fields directly.

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `source_path` | string |
| `index_path` | string |
| `mode` | string |
| `status` | `current`, `stale`, `updated` |
| `current` | boolean |
| `file_count` | integer |
| `chunk_count` | integer |

Downstream, select a field as `node.step.output.source_path`.

## Preflight and limits

The source must be readable, the index path writable, the encoding supported and the strategy pair available. The runtime limits how much it scans and reads.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
