---
title: Report themes
description: Select, generate, import, and export reusable design guidance for Rem reports.
pageClass: docs-layout
---

# Report themes

A report theme gives new reports a consistent look, while each one still fits its subject.

## Pick a theme

Open **Settings > Memory > Report themes**. The gallery has System, Light, Dark, Sepia, Vaporwave, Steam, Carbon, Botanical, Blueprint, Arcade, Sakura, Deep Sea, Solarpunk, Noir, Candy Lab and Cosmic.

- **System** asks Rem to match light and dark palettes to the device's appearance. The others bring their own design instructions.
- Preview the example documents, then select one.
- Clear **Use the selected report theme** to send no theme guidance at all.

A theme applies to later HTML reports, slides and PDFs, wherever they're saved. It doesn't turn on Second Brain, override a format you ask for, or rewrite existing documents.

## Make your own

1. Choose **New theme** and describe the design. You can attach references by dictation, paste or drag and drop: up to five files, 20 MB each, 40 MB total (the chat limits).
2. Choose **Generate preview**. Rem returns an example document and reusable instructions.
3. Check readability and layout, rename the theme, and choose **Save theme**. Previews you don't save are discarded.

Generation uses the current Rem provider, model and effort, unless you pick others under **Theme generation settings**.

- Image references work with Codex, Claude Code, Cursor, Copilot, OpenCode and Grok.
- Text descriptions and document attachments work with every supported Rem provider.

## Share a theme

Select a saved custom theme and choose **Export theme** to get a `.raticode-theme.json` file. The other person chooses **Import theme** in the same section.

- The file holds the name, design instructions and HTML preview. No provider choices or other settings.
- Theme files can be up to 2 MB.
- An import always creates a new theme, even if the name matches one you have.
- An invalid file changes nothing.
- You can save up to 24 custom themes.
- Previews run with scripts and network access off.

## Portable documents

Rem writes each report's styling into the report itself. The save tools keep it as written, and the reader adds no CSS of its own. Exporting a theme shares the design guidance, not the reports made with it.

See [Reports, slides and PDF](./reports.md) and [Attachments and dictation](../rem/attachments.md).
