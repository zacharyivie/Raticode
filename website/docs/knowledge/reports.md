---
title: Reports, slides and PDF
description: Ask Rem for portable documents and save them in a project or Second Brain.
pageClass: docs-layout
---

# Reports, slides and PDF

Turn an investigation into a document you can keep, share and read outside the chat.

## Choose a format

**Settings > Memory > Report output** sets the default, Markdown at first. A format you ask for in your message wins.

| Format | Saves | Good for |
| --- | --- | --- |
| Markdown | A `.md` file | Notes, guides kept in Git, plain text |
| HTML | A standalone `.html` file | Styled research, diagrams, readable reports |
| Slides | A standalone HTML presentation | Making an argument slide by slide |
| PDF | A `.pdf` plus its HTML source | Printing, with page breaks you control |

Name the subject, audience and destination together, for example: "Review the source-control implementation and save a standalone HTML report in `reports/engineering/`." Rem can read the project only with the right tools turned on.

## Where it's saved

| Tool | Saves into |
| --- | --- |
| `reports.save_report` | The active project. Works without Second Brain. |
| `second_brain.save_note` | The configured knowledge folder, with the same document writer |

- Neither tool replaces an existing file.
- Both take an optional `summary`: two or three plain sentences, at most 600 characters, on what the document finds. It's written into the file, as a `summary:` front-matter field in Markdown or a `<meta name="description">` in HTML, Slides and a PDF's HTML source, so it goes wherever the file goes. In a Second Brain, Rem reads it first in search; see [Document summaries](./second-brain.md#document-summaries). A longer summary is refused and nothing is saved. New in 0.6.0.
- A successful save returns a local link. Open it to check the real file rather than trusting the chat's description.
- With no project selected, use Second Brain with a configured folder, or select a project first.
- A failed save means no document was saved.

### Links in saved reports

Both tools check the file links in a report as it's saved, so they keep working when the folder moves or syncs to another computer.

- A link that uses a full path, or a `file://` address, to a file inside the folder is saved as a path relative to the report. For example, `/home/you/notes/Assets/chart.png` in `Reports/Topic/` becomes `../../Assets/chart.png`. Symbolic links and `..` are resolved first.
- A link to `/tmp` or another temporary folder, or to a file outside the folder, is saved as written. The save result lists a warning for it, because the file can disappear or won't exist on another computer. Copy the file into the folder and link to the copy.
- Web addresses, `#anchors` and links inside Markdown code are left alone. A warning never blocks the save.

## Portable HTML

Rem writes a complete document with its CSS embedded, and the reader keeps that styling.

- A good HTML report works without remote fonts, scripts or a running Raticode.
- For reports you'll share, ask for proper headings, accessible colors, print styles and embedded images.
- The selected [report theme](./themes.md) guides new documents. It doesn't restyle old ones.

## Slides and PDF

**Slides** use `section.slide` elements in a 16:9 layout, with navigation for reading and print styles that show every slide.

**PDF:** Rem writes standalone HTML, where `section.page` elements can set page breaks. Raticode renders it with its bundled Chromium, A4 portrait by default.

- Rendering blocks outside requests, local file access and scripts, so images and fonts must be embedded.
- It needs the desktop app running.
- A failed render never leaves a file that's only named `.pdf`.
- Each PDF comes with a `.pdf.html` copy so its content can be indexed and reviewed.

## Find it again

- Project documents show in the file editor. HTML opens in [Browse mode](../studio/browser.md#read-project-reports).
- Documents saved in Second Brain become searchable once their text is indexed.
- Keep related notes in topic folders, with titles that say what's inside.
