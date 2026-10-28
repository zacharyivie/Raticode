---
title: Integrated browser
description: Browse documentation and local reports in Raticode without leaving your workspace.
pageClass: docs-layout
---

# Integrated browser

Open web references and local HTML documents beside the project you are working on.

## Open a browser tab

Use the browser command or `Ctrl+J` to open a browser tab. Enter a URL to navigate, or use the configured search URL for a query. The browser has its own navigation state; selecting a code file does not change the page you are reading.

In **Settings > Browser**, set the Home page and Search URL. Include `{query}` where the encoded search terms should appear. The defaults apply when opening new browser tabs.

## Read project reports

Local HTML files can open in Browse mode. Markdown files have a separate rendered preview. Use the mode control to return to source for edits.

Reports authored by Rem keep their own embedded styling. Reading a report should not rewrite its CSS or change its theme. [Reports, slides and PDF](../knowledge/reports.md) explains how those documents are produced.

## Browser access and agent access

An integrated browser tab is for your interactive use. It does not automatically give Rem access to the page, its cookies, or a signed-in session. Rem's web-search capability and selected MCP tools are configured separately in the thread resource menu.

Likewise, using the browser to sign into a website does not sign in a coding CLI. Follow [Provider setup](../setup.md) for that CLI's authentication flow.

## A page does not load

Check the URL and try it in the system browser to separate a website problem from an embedded-browser problem. For a local report, confirm the file still exists and belongs to a selected folder. Use **Settings > Developer** for application diagnostics when the page or preview fails inside Raticode.

