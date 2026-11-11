---
title: Reports, slides and PDF
description: Ask Rem for portable documents and save them in a project or Second Brain.
pageClass: docs-layout
---

# Reports, slides and PDF

Turn an investigation into a document you can keep, share, and read outside the chat.

## Choose an output

Open **Settings > Memory > Report output** to choose the default; it starts as Markdown. An explicit request in your message takes precedence.

| Format | Saved result | Use it for |
| --- | --- | --- |
| Markdown | A `.md` document | Notes, source-controlled guides, and text editing |
| HTML | A standalone `.html` document | Styled research, diagrams, and readable reports |
| Slides | A standalone HTML presentation | Presenting an argument in a sequence of slides |
| PDF | A `.pdf` plus its HTML source | A printable document with deliberate pagination |

Ask for the subject, audience, and destination together. For example: "Review the source-control implementation and save a standalone HTML report in `reports/engineering/`." Rem can inspect the project only with the relevant resources enabled.

## Save in the right place

The `reports.save_report` tool writes relative to the active project. It is available independently of Second Brain. The `second_brain.save_note` tool uses the same document writer inside the configured knowledge root.

Both preserve existing files instead of silently replacing a report. A successful save returns a local link. Open that link to review the actual artifact, rather than relying on a chat description of it.

If no project is selected, use Second Brain with a configured root, or select a project before asking for a project report. A failed save is not a saved document.

## Portable HTML

Rem authors a complete document with embedded CSS. The reader preserves that styling. Ask for semantic headings, accessible colors, readable print styles, and embedded essential assets when the report will be shared.

The selected [report theme](./themes.md) guides new documents. It does not restyle older files. An HTML report should be usable without remote fonts, scripts, or a running Raticode server.

## Presentations and PDF

Slides use `section.slide` elements and a 16:9 presentation layout, with navigation for reading and print styles that expose every slide.

For PDF, Rem supplies standalone HTML; `section.page` elements can define deliberate page breaks. Raticode renders it with bundled Chromium, using A4 portrait by default. External requests, local file access, and scripts are disabled during rendering, so essential images and fonts must be embedded.

PDF rendering requires a running desktop app. A failed render does not produce a file merely named `.pdf`. Successful output includes a `.pdf.html` companion so the content can be indexed and reviewed.

## Find it again

Project documents appear in the file editor, where HTML opens in [Browse mode](../studio/browser.md#read-project-reports). Documents saved in Second Brain become searchable as supported text is indexed. Keep related notes in topic folders and use titles that describe their contents.
