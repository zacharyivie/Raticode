---
name: Raticode
description: A compact local workflow studio with a shared document editor.
colors:
  primary: "#4f46e5"
  canvas: "#e4dfd5"
  sidebar: "#30364f"
  assistant: "#dce5df"
  editor: "#eee9df"
  surface: "#f4f0e7"
  surface-muted: "#d9d4ca"
  ink: "#30343b"
  muted: "#62635f"
  line: "#b6b3aa"
  success: "#059669"
  warning: "#d97706"
  error: "#dc2626"
typography:
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.25
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.25
rounded:
  sm: "6px"
  md: "10px"
  lg: "14px"
  full: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    height: "34px"
    padding: "0 12px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 12px"
---

# Design System: Raticode

## Overview

**Creative North Star: "The Local Workflow Cockpit"**

Workflow graphs, source files, and browser pages share persistent editor tabs. An activity rail chooses what to browse; the project selector changes the browsing folder without retargeting open documents. Navigation and assistance frame the editor with compact, predictable tools. Light mode uses a deep indigo navigation rail, a warm stone workspace and editor, and a muted sage assistant pane. Indigo marks selection, focus, and the primary action. Dark mode uses a zinc palette (#18181a surfaces, #1e1e21 raised panels) with the same semantic hues.

The interface is dense enough for builders but uses plain labels and familiar controls so workflow authors do not need to know the TOML model first.

**Key Characteristics:**

- Three-pane desktop composition with a dominant center canvas
- Contextual floating UI for maps, menus, and configuration
- One accent color plus semantic run-state colors
- Light and dark palettes with matching hierarchy

## Colors

Indigo is the action accent. Large regions have distinct roles: navigation uses #30364f with pale text, the workspace uses #e4dfd5, the editor uses #eee9df, and chat uses #dce5df with green-black text. Inputs and floating content use lighter fills within their region. Sage is a panel background, never a success indicator. Green, amber, red, and blue retain their semantic state meanings.

**The One Accent Rule.** Use indigo for selection, focus, and primary actions. Do not introduce another brand accent.

## Typography

The studio uses the operating system's UI sans stack. Monospace is reserved for code, paths, identifiers, logs, and measurements.

The hierarchy stays compact: 14px titles, 13px body copy, 11px supporting labels, and 10px metadata where space is tight.

## Layout

The desktop layout uses a 41px activity rail within a resizable 272px project pane, a flexible tabbed editor, and a 380px assistant pane. Rem, Workflows, Files, Search, Source control, Swarms, Organizations, and Second Brain share the rail. Rem leads it with a rat-face icon. The recent-project selector sits below the sidebar identity header and stays in place across activities. New Workflow belongs inside the Workflows panel, to the right of the activity rail. When the sidebar is collapsed, File > Open Recent retains project access. Each graph view owns its toolbar, camera, selection, and inspector. Graph headers wrap controls when space is limited. Below 1000 CSS pixels, both side panes collapse; users can open one at a time, and desktop preferences return when the window widens.

Floating canvas controls must keep 16px from the viewport edge and move clear of the inspector. The application targets desktop windows and keeps keyboard access for every graph action.

## Elevation & Depth

Base panes are flat and separated by one-pixel lines. Shadows belong to floating surfaces such as popovers, nodes, dialogs, and the inspector.

**The Flat Frame Rule.** Fixed navigation and headers use tonal separation or a border. They do not cast shadows.

## Shapes

Standard controls use 6px corners. Nodes and compact surfaces use 10px corners. Popovers and the composer use 14px corners. Pills are limited to badges, status chips, and the combined model trigger.

## Components

### Primitives and tokens

Shared controls live in `frontend/src/styles/primitives.css` and read semantic tokens
from `index.css` (`--color-surface`, `-surface-muted`, `-raised`, `-field`, `-brand-solid`,
`-success`, `-warning`, `-danger`, `-info`, each status with a `-soft` tint, plus
`--color-scrim`). Every token is defined for light, dark, and the light-mode indigo
sidebar region, and the same names are Tailwind colors (`bg-surface`, `text-danger`,
`bg-warning-soft`). Use the primitives before writing new control styles:
`ui-button` (`--primary`, `--ghost`, `--danger`, `--danger-ghost`, `--sm`, `--lg`),
`ui-icon-button`, `ui-input`, `ui-badge` and `ui-notice` with `data-tone`, `ui-popover`,
`ui-menu-item`, `ui-dialog`, `ui-scrim`, `ui-panel-header`, and `ui-section-title`.
Every single-line select shares one chevron.

Dark mode follows light-mode utilities automatically: the 50 and 100 tints of each hue
become dark tints, 200–300 borders and 600–950 text shades lighten, and neutral slate
utilities map to the zinc surfaces. These fallbacks yield to an authored `dark:` variant
on the same element. New pale tint text must not use the 50 or 100 shades.

### Buttons

Primary buttons use indigo with white text. Quiet icon buttons start borderless and gain a muted background on hover. Focus uses a visible two-pixel indigo outline.

### Chips

Chips are small pills with a tinted surface. Their text and border share the same semantic hue.

### Cards / Containers

Persistent panes are not cards. Popovers use a 14px corner, a one-pixel neutral border, and a soft offset shadow.

### Inputs / Fields

Inputs use warm paper in the workspace, pale sage in chat, and a raised indigo fill in navigation, with contrasting borders. Focus shifts the border to indigo and may add a restrained translucent ring.

### Navigation

Workflow groups use collapsible folder rows. Selecting a workflow opens or focuses its persistent graph tab. Graph and source views share one document buffer; split graph views retain independent cameras and selections. Foreign-project tabs show their owning path. The selected workflow receives an indigo tint without an extra border. Run status and actions are separate from tab selection and closing. The global Runs list retains background results and opens exact run snapshots. Thread history lives in a popover opened from the assistant header.

### Graph map

Outline and minimap share one bottom-right Map popover. The popover defaults closed and uses tabs so both views never compete for canvas space.

## Do's and Don'ts

### Do:

- **Do** keep the graph readable before adding secondary controls.
- **Do** expose status with text or an accessible label, not color alone.
- **Do** keep the assistant composer available even when no thread is open.

### Don't:

- **Don't** stack multiple permanent panels over the canvas.
- **Don't** use native selects for provider and model discovery when grouped availability matters.
- **Don't** add explanatory wireframe notes to the product UI.

### Swarm dashboard

Swarms use one Operate-mode mission dashboard. The current task and run controls lead;
message board, accepted milestone progress, and agent roster share the page. At widths
above 740px within the workspace, the board takes the wider left column and progress
sits above the roster on the right. Narrow panes stack these sections in reading order.

Agent identity groups a 48×56 portrait slot, name, responsibility, and role. Initials
occupy the portrait until character sprites are available. Inset activity updates and
thin accepted-milestone meters borrow from a game party stats screen. Progress uses
real weighted accepted work; agents without assigned milestones have no invented meter.
Three-dot menus open individual agent settings. Activity and conversation expand inline.
Team setup uses the quiet header settings action. Previous runs are collapsed below the
live dashboard and open the same composition with read-only controls and an explicit
return to the current run. The sidebar lists teams without duplicating their rosters.

Swarm surfaces retain the studio's indigo actions and semantic state colors. Dark mode
uses #18181a for the workspace, #1e1e21 for the board, and #151517 for inset status areas.
Light mode uses the existing warm workspace, paper, and stone colors. Task titles can
reach 32px; roster names remain 14px. Progress fill transitions honor reduced motion.

### Second Brain workspace

Second Brain follows Organizations in the activity rail. Its sidebar leads with a brain
switcher (opened brains, folder, availability, open/create/import) and then lists saved
searches, collections, reports, and recent documents for the selected brain. The workspace
keeps the existing Rem pane instead of adding a second assistant; Ask Rem actions attach
the selected evidence to a Rem thread for review before sending.

Search is the default section: one query bar with a text syntax, visible filter buttons
(Scope, Type, Tags, Date, Search in, More filters), removable chips, a plain-language
explanation of the query, and the equivalent text form. Results sit in a 300–400px column
beside a tabbed reader; single click previews, Enter or double-click keeps a tab. Below
860px of workspace width, results and reader become tabs. Invalid query clauses are shown
and never silently dropped.

Citations are numbered indigo chips. Their state is always written out: Passage found,
Source changed, Location needs review, Source unavailable, Support not reviewed. Amber marks
changed sources and affected claims; red marks unsupported claims. Coverage counts evidence
links and is labeled as not measuring truth. Light mode uses the warm editor paper; dark mode
uses #18181a, #1e1e21, and #232327 surfaces.

The workspace uses the persistent Second Brain API. Import/export, extraction, citations,
reports, comparisons and research jobs show real task receipts and errors. Explicit fixtures
are limited to tests. See [Second Brain workspace](docs/second-brain-workspace.md) for
storage, provider privacy, runtime requirements and verification commands.

### Rem home

Rem's home is a lazy river: one continuous conversation in a 680px column on warm editor
paper, with Rem's task list in the sidebar. Rem's identity is the rat face on a sage disc, and the animated
Rem sprite greets on the welcome screen (it no longer appears above the thread list);
user messages are soft indigo bubbles; Rem's replies are plain text without a bubble.
Work Rem starts appears as a live card under its message, always with an Open thread link.
The sidebar groups tasks by what they need (Working on, Needs you, Coming up, Paused, Done);
only running phases spin, and every state is written out.

The task sheet keeps visible controls to the four task sections plus Done when. Run controls
sit beside the run's threads in the status card, and Delete is a quiet icon that confirms inline.
Do not add provider, budget, or permission knobs to this surface; they belong to the backend
task recipe and its details. See [Rem home](docs/rem-home.md).
