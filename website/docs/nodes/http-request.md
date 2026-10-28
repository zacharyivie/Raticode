---
title: http-request
description: Call an HTTP or HTTPS endpoint and expose a structured response.
pageClass: docs-layout
---

# http-request

Call an HTTP or HTTPS endpoint and expose a structured response.

## When to use it

Use this node for a reviewed service request. Use open-resource when the goal is to open a URL in a browser rather than consume its response.

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

GET, a 30-second request timeout, one attempt, and expected status 200 are the defaults. Choose explicit expected statuses for endpoints that return 201 or 204. Set retry attempts, backoff, and retry-on-statuses deliberately, especially for requests that write data.

Use `json` or `body` for a request payload, not both. Bound payload ports override the corresponding authored payload. Response modes are `auto`, `json`, `text`, and `none`. Output includes raw body, parsed JSON where available, selected values, and request metadata.

`output-mapping` selects response values by supported paths; `network-allowlist` constrains configured destinations. Use compatible secret bindings for protected values and review redaction fields. An allowed target is not a guarantee that arbitrary response content is trustworthy.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

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

Bind these declared ports through `with`:

| Port | Value type |
| --- | --- |
| `url` | string |
| `headers` | object |
| `params` | object |
| `json` | any JSON value |
| `body` | string |

Successful output fields use these exact selector names:

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

For example, refer to `node.step.output.status` from a compatible downstream binding.

## Preflight and limits

Preflight validates the URL and network policy without sending the request. Availability, rate limits, authentication, response format, and status are runtime concerns.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
