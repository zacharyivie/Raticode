---
title: local-vectorize
description: Chunk supported local text and maintain an on-disk retrieval index.
pageClass: docs-layout
---

# local-vectorize

Chunk supported local text and maintain an on-disk retrieval index.

## When to use it

Use this node to prepare workflow-specific retrieval. It is independent of Second Brain's folder index and does not enable Second Brain automatically.

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

Create the source folder and text files before preflight. Incremental mode is the default; `full` rebuilds and `validate` checks index currency. The default chunk size is 1,200 with 120 overlap. Keep overlap below the selected size.

The built-in strategy pair is `hash_token_v1` and `cosine_v1`. This is local retrieval with deterministic token hashing, not a claim that a remote embedding model has read the folder. Match the same pair in local-search.

Output distinguishes a current, stale, or updated index and includes file/chunk counts. A query needs an existing readable index, so build the index deliberately before running a separate search workflow.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

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

This node has no declared input ports. Configure its authored fields directly.

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `source_path` | string |
| `index_path` | string |
| `mode` | string |
| `status` | `current`, `stale`, `updated` |
| `current` | boolean |
| `file_count` | integer |
| `chunk_count` | integer |

For example, refer to `node.step.output.source_path` from a compatible downstream binding.

## Preflight and limits

The source must be readable, the index destination writable, the encoding supported, and the strategy pair available. Runtime scanning and reads have resource limits.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
