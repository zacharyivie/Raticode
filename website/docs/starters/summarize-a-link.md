---
title: "Starter: Summarize a link"
description: A starter workflow that reads the web page behind a link you share from your phone or copy on the desktop, and sends a short summary to your phone.
pageClass: docs-layout
---

# Summarize a link

Reads the web page behind a link and sends a short summary to your phone. Share a link to it from any phone app, or copy one and press the workflow's desktop shortcut. It makes one model call per page.

::: info New in 0.6.0
This starter and the [read-web-page](../nodes/read-web-page.md) node it uses are new in Raticode 0.6.0. Sharing from the phone also needs Raticode Mobile 0.36.0 or later.
:::

```bash
Raticode rattish create --from summarize-a-link --project .
```

On Windows, its shell steps run in Git for Windows' bash (`shell: bash`), so install [Git for Windows](https://git-scm.com/downloads/win) first. Until you do, **Start from** says **Needs Git for Windows** and preflight stops the run with a link to the download. Windows support for starters is new in Raticode 0.6.0.

## Inputs

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `link` | string | `https://example.com/` | A link, or shared text that contains one. The first `http` or `https` address is read. |
| `focus` | string | empty | What the summary should pay attention to, such as "pricing". Empty gives a general summary. |

## Run it from your phone or a shortcut

`link` is a plain text input, so the workflow is ready for both:

- **Phone.** In any app, tap **Share › Run a workflow**, pick **Summarize a link** and then `link` if the phone asks for the input. Check the Run sheet and tap **Run**. See [Share to a workflow](../mobile/workspace.md#share-to-a-workflow).
- **Desktop.** Choose **Add shortcut…** in the workflow's menu. Copy a link, press the shortcut, check the small Run sheet and press Enter. See [Run a workflow from any app](../reference/shortcuts.md#run-a-workflow-from-any-app).

Apps often share a title with the link, such as "Worth a look: https://…". That's fine: `find-link` takes the first web address out of the text.

## How it works

1. `find-link` takes the first `http` or `https` address out of `link` and drops trailing punctuation. With no address, the run fails and nothing is fetched.
2. `read` is a [read-web-page](../nodes/read-web-page.md) node. It fetches the page (one GET, up to 5 redirects, 30-second limit) and keeps up to 20,000 characters of the article or main text as Markdown, without navigation, sidebars and footers. It refuses addresses on this computer or your local network. It uses `render: auto`: when the fetched page looks empty without JavaScript, it renders the page in Chrome, Chromium or Edge, if one is installed, and reads that instead. See [Pages that need JavaScript](../nodes/read-web-page.md#pages-that-need-javascript).
3. When the page has readable text, `summarize` (a [common-llm-task](../nodes/common-llm-task.md) on `claude-code`) writes up to five short bullets, under about 600 characters, putting `focus` first when you set it.
4. `tell-phone` sends a [phone notice](../nodes/notification.md#phone-notices) titled "Summary: PAGE TITLE" with the summary and the page's address.
5. When the page still looks empty, because no browser is installed or the rendered page has no readable text either, `needs-javascript` tells your phone to open it in a browser instead, and no model is called.
6. When the page can't be read, such as a PDF, a 404 or a refused address, `could-not-read` sends the reason to your phone and the run ends as failed.

The run's cost limit is $0.50 (`max-cost-usd`). Change `provider` on `summarize` to use another model.

## Shared pages are untrusted

A web page, and a link someone sent you, can carry instructions aimed at an AI step. `summarize` is told to summarize the page and never follow instructions, links or requests inside it, and no other step acts on the page text: the notice only shows the summary. If you add steps, keep the page text away from steps that run commands or change files. See [read-web-page](../nodes/read-web-page.md#treat-the-text-as-untrusted).

To read pages on your own network, such as a router or an intranet wiki, add the host or address to `network-allowlist` on `read`. The same list applies to the browser when a page is rendered.

With no Chrome, Chromium or Edge installed, preflight shows a warning for `read` and the workflow still runs: pages that need JavaScript go to `needs-javascript`. To never start a browser, change `render: auto` to `render: never`.

## Test set

| Case | What it checks |
| --- | --- |
| `shared-text` | Takes the link out of shared text, reads example.com and sends a phone notice naming and linking the page. A judge checks the summary. |
| `no-link` | Text without a link fails the run without fetching or notifying |
| `private-address` | A link to `127.0.0.1` is refused, nothing is summarized, and the phone notice says why |

`shared-text` fetches https://example.com/ and makes one model call, plus one for the judge. The other cases call no model and fetch nothing. The notices are stored for your paired phone like any phone notice, titled "Summary: Example Domain" and "Couldn't read a link".

See [Starter workflows](./index.md) and the [read-web-page](../nodes/read-web-page.md), [common-llm-task](../nodes/common-llm-task.md) and [notification](../nodes/notification.md) nodes.
