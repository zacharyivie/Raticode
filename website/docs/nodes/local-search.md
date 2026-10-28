---
title: local-search
description: Find ranked text chunks in a previously created local retrieval index.
pageClass: docs-layout
---

# local-search

Find ranked text chunks in a previously created local retrieval index.

## When to use it

Use this node to obtain relevant snippets for a later prompt. Choose Second Brain tools for conversational knowledge-folder search instead.

## Example

```rattish
Rattish: 1

Workflow:
  name: Search a local index

Node step:
  type: local-search
  index-path: indexes/notes.json
  query: How is the project installed?
  top-k: 5
  score-threshold: 0.0
  finish: pass
```

Build the index first using local-vectorize, either before the run or in an upstream step listed in `needs`, and keep the embedding/search strategies compatible. The default pair is `hash_token_v1` and `cosine_v1`. The default returns up to five results, with snippets and file metadata enabled.

`score-threshold` ranges from -1 to 1. Raising it can remove all results; a count of zero is not a provider failure. Bind `with.query` to replace the authored query from a public input or upstream value.

Read the returned source context before using a match as evidence. A ranking score measures retrieval similarity, not the truth or freshness of a statement.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `index-path` | string | Required | On-disk local retrieval index path. |
| `query` | string | Required | Retrieval query text. |
| `top-k` | integer (min 1) | `5` | Maximum returned results. |
| `score-threshold` | number (min -1, max 1) | `0.0` | Minimum accepted similarity score. |
| `include-snippets` | boolean | `true` | Include matched text snippets. |
| `include-file-metadata` | boolean | `true` | Include file metadata in results. |
| `embedding-strategy` | string | `"hash_token_v1"` | Installed text-to-vector strategy ID. |
| `search-strategy` | string | `"cosine_v1"` | Installed ranking strategy ID. |

## Inputs and output

Bind these declared ports through `with`:

| Port | Value type |
| --- | --- |
| `query` | string |

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `query` | string |
| `index_path` | string |
| `count` | integer |
| `results` | list of object |

Each result has `score`, `path`, `chunk` and `text`, plus `snippet` when snippets are enabled. With file metadata enabled, `metadata` holds the file's `size` in bytes and its `modified_at` time as an ISO 8601 UTC string.

For example, refer to `node.step.output.query` from a compatible downstream binding.

## Preflight and limits

The index must exist and be readable, and its strategy pair must be available. When the node has `needs`, preflight skips the existence check because an upstream step may create it with local-vectorize earlier in the run; the node then reports a missing index when it runs.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
