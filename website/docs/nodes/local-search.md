---
title: local-search
description: Find the best-matching text chunks in a local search index.
pageClass: docs-layout
---

# local-search

Find the best-matching text chunks in a local search index.

## When to use it

Use it to fetch relevant snippets for a later prompt. To search a knowledge folder from a chat, use Second Brain's tools instead.

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

- Build the index with [local-vectorize](./local-vectorize.md) first, before the run or in an upstream step listed in `needs`.
- Use the same strategy pair as the index. The default is `hash_token_v1` and `cosine_v1`.
- By default it returns up to five results, with snippets and file metadata.
- `score-threshold` runs from -1 to 1. A high threshold can remove every result. Zero results is not a failure.
- Bind `with.query` to replace the query you wrote with a public input or upstream value.
- Read the source text before you treat a match as evidence. The score measures similarity, not whether a statement is true or current.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

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

Input ports, bound through `with`:

| Port | Value type |
| --- | --- |
| `query` | string |

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `query` | string |
| `index_path` | string |
| `count` | integer |
| `results` | list of object |

Each result has `score`, `path`, `chunk` and `text`, plus `snippet` when snippets are enabled. With file metadata enabled, `metadata` holds the file's `size` in bytes and its `modified_at` time as an ISO 8601 UTC string.

Downstream, select a field as `node.step.output.query`.

## Preflight and limits

The index must exist and be readable, and its strategy pair must be available. With `needs`, preflight skips the existence check, because an earlier local-vectorize step may create the index. A missing index then fails when the node runs.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
