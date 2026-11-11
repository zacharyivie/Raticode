# Raticode website

The public Raticode site, built with [VitePress](https://vitepress.dev/) 1.6. This file is excluded from the published pages.

The website is the single source of truth for Raticode documentation, desktop and mobile. A new feature or a change users would notice updates `docs/` here before it is merged to `dev`; see the root `AGENTS.md`.

## Commands

Select Node through nvm before every npm command.

```bash
source "$HOME/.nvm/nvm.sh"
nvm use
cd website
npm ci
npm run dev        # local dev server
npm run typecheck  # vue-tsc
npm run check      # typecheck, build, then check links, #anchors, sitemap and robots.txt
npm run check:downloads  # download links match the newest published release (network)
npm run set-release -- X.Y.Z  # move the site to a new desktop release
npm run build      # static output in .vitepress/dist
npm run preview    # serve the build
```

The build targets project Pages at `https://zacharyivie.github.io/Raticode/`. For another
address set `WEBSITE_URL` (for a custom domain, `WEBSITE_URL=https://<domain>/`), and
optionally `WEBSITE_BASE` for the path; `data/site.ts` reads both, and `check:links` must
see the same values as the build. The sitemap, `robots.txt`, canonical links and the social
preview image are generated from that address.

## Publishing

`.github/workflows/website.yml` deploys the site to GitHub Pages from `main` after
`npm run check` and `npm run check:downloads` pass, and again when a release is published.
The same two variables are read from the repository's Actions variables. Setup, the
release step and custom domains are covered in `docs/releasing.md` under "The website".

Suggested text for the repository's **About** box (gear icon on the repository page):

- Description: `Local studio for agentic coding workflows: wire shell commands, scripts and coding agents into workflows you can see, schedule and run anywhere.`
- Website: `https://zacharyivie.github.io/Raticode/`
- Topics: `ai-agents`, `workflow-automation`, `developer-tools`, `electron`, `claude-code`, `codex`, `local-first`, `rattish`

## Layout

- `index.md` renders the landing page through `ProductLanding.vue`.
- `.vitepress/theme/components/landing/` holds the landing sections.
- `.vitepress/theme/styles/` holds brand tokens and the landing navbar overrides.
- `data/releases.ts` is the reviewed release manifest; `npm run set-release` updates it.
- `data/site.ts` resolves the site's address and base path.
- `public/brand/` holds the roundel and Rem sprites, derived from `frontend/src/assets/`.
- `docs/` holds the Markdown documentation wiki, including installation, feature guides, and the node reference.
- `.vitepress/sidebar.ts` defines the grouped docs navigation; `.vitepress/theme/styles/docs.css` styles the reading layout.

## Documentation

Each docs page has a title, description, and `pageClass: docs-layout` in its frontmatter.
Use relative Markdown links between guides and add new pages to the sidebar. VitePress
provides local search, an on-page outline, and previous/next guide navigation.

Run `npm run check` after editing. The build rejects dead page links, and `scripts/check-links.mjs` also checks links in the landing components and `#anchors`.
Validate complete Rattish examples with the installed CLI's format, check, and preflight
commands in a disposable folder with the documented resource fixtures. Do not execute
examples during documentation verification. Feature availability notes distinguish the
public desktop release from experimental and legacy integration paths.

The example workflow in `components/landing/workflow.ts` passes `Raticode rattish check` and
`Raticode rattish preflight`. Re-validate it if you edit the source.
