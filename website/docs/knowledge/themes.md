---
title: Report themes
description: Select, generate, import, and export reusable design guidance for Rem reports.
pageClass: docs-layout
---

# Report themes

Give new reports a consistent visual direction while allowing each document to fit its subject.

## Select a theme

Open **Settings > Memory > Report themes**. The gallery includes System, Light, Dark, Sepia, Vaporwave, Steam, Carbon, Botanical, Blueprint, Arcade, Sakura, Deep Sea, Solarpunk, Noir, Candy Lab, and Cosmic.

System asks Rem to coordinate light and dark palettes with device appearance. Other themes provide their own design instructions. Preview the document examples, then select the guidance you want future output to use.

Theme guidance applies to subsequent HTML reports, slides, and PDFs wherever they are saved. It does not enable Second Brain, change an explicit format request, or rewrite existing documents.

## Generate a custom theme

Choose **New theme**, describe the design, and optionally attach references. You can use dictation, paste, or drag and drop. The theme composer uses the chat attachment limits: up to five files, 20 MB each, 40 MB total.

Choose **Generate preview** to request a demonstration document and reusable instructions. Inspect readability and layout, rename the theme, and choose **Save theme** when you want to keep it. Unaccepted previews are not saved.

Generation normally uses the current Rem provider, model, and effort. Expand **Theme generation settings** to select dedicated choices. Image references work with Codex, Claude Code, Cursor, Copilot, OpenCode, and Grok. Text descriptions and document attachments work across supported Rem providers.

## Share a theme

Select a saved custom theme and choose **Export theme**. Send the resulting `.raticode-theme.json` file to a teammate. They can choose **Import theme** in the same section.

The file contains the name, design instructions, and HTML preview. It does not transfer provider choices or unrelated settings. An import creates a new custom theme even when its name matches an existing one. Invalid or unsupported files leave saved themes unchanged.

Up to 24 custom themes can be saved. Preview documents are isolated with scripts and network access disabled.

## Keep the document portable

Rem authors the report's embedded styling. The save tools preserve that authored content and the reader adds no replacement report CSS. Exporting a theme shares design guidance, not every report created with it.

See [Reports, slides and PDF](./reports.md) and [Attachments and dictation](../rem/attachments.md).
