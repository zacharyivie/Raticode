---
title: http-request
description: Call an HTTP or HTTPS endpoint and expose a structured response.
pageClass: docs-layout
---

# http-request

Call an HTTP or HTTPS endpoint and expose a structured response.

## When to use it

Use it for a reviewed call to a service. To open a URL in a browser instead of reading the response, use [open-resource](./open-resource.md).

## Example

```rattish
Rattish: 1

Workflow:
  name: Make an HTTP request

Node step:
  type: http-request
  url: https://api.github.com/repos/zacharyivie/Raticode/releases/latest
  method: GET
  headers:
    Accept: application/vnd.github+json
  response-mode: json
  expected-statuses:
    - 200
  request-timeout: 30s
  finish: pass
```

- **Defaults.** GET, a 30-second timeout, one attempt, expected status 200. List other statuses yourself for endpoints that return 201 or 204.
- **Retries.** Set attempts, backoff and retry-on-statuses on purpose, especially for requests that write data.
- **Payload.** Use `json` or `body`, not both. A bound payload port overrides the matching field you wrote.
- **Response modes** are `auto`, `json`, `text` and `none`. Output includes the raw body, parsed JSON when there is some, selected values and request details.
- `output-mapping` picks values out of the response by path. `network-allowlist` limits where requests may go.
- Bind secrets for protected values, and check the redaction fields. An allowed host can still send back content you shouldn't trust.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `method` | string | `"GET"` | HTTP method. |
| `url` | string | Required | HTTP or HTTPS endpoint. |
| `headers` | object | `{}` | String HTTP headers. |
| `params` | object | `{}` | String query parameters. |
| `json` | any JSON value | Not set | JSON request body; excludes authored body. |
| `body` | string or null | Not set | Text request body. |
| `request-timeout` | string | `"30s"` | Per-request timeout. |
| `retry` | object | `{"attempts": 1, "backoff": "0s", "retry-on-statuses": []}` | Attempt count, backoff, and retry-on-statuses map. |
| `expected-statuses` | list of integer | `[200]` | Accepted HTTP status codes. |
| `response-mode` | `auto`, `json`, `text`, `none` | `"auto"` | Response interpretation. |
| `output-mapping` | object | `{}` | Named response selectors. |
| `secret-fields` | list of string | `[]` | Request field redaction declarations. |
| `network-allowlist` | list of string | `[]` | Configured network destination constraints. |

## Inputs and output

Input ports, bound through `with`:

| Port | Value type |
| --- | --- |
| `url` | string |
| `headers` | object |
| `params` | object |
| `json` | any JSON value |
| `body` | string |

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `status` | integer |
| `headers` | object |
| `body` | string |
| `json` | any JSON value |
| `value` | any JSON value |
| `selected` | object |
| `url` | string |
| `method` | string |
| `attempts` | integer (min 1) |

Downstream, select a field as `node.step.output.status`.

## Preflight and limits

Preflight checks the URL and network policy without sending anything. Whether the service is up, rate limits, sign-in, the response format and status all show up only at run time.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
