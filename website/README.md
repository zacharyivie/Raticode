# Raticode website

The public Raticode site, built with [VitePress](https://vitepress.dev/) 1.6. This file is excluded from the published pages.

## Commands

Select Node through nvm before every npm command.

```bash
source "$HOME/.nvm/nvm.sh"
nvm use
cd website
npm ci
npm run dev        # local dev server
npm run typecheck  # vue-tsc
npm run build      # static output in .vitepress/dist
npm run preview    # serve the build
```

The build targets project Pages at `/Raticode/`. Set `WEBSITE_BASE=/` when building for a custom domain.

## Layout

- `index.md` renders the landing page through `ProductLanding.vue`.
- `.vitepress/theme/components/landing/` holds the landing sections.
- `.vitepress/theme/styles/` holds brand tokens and the landing navbar overrides.
- `data/releases.ts` is the reviewed release manifest. Update it when a new release is approved.
- `public/brand/` holds the roundel and Rem sprites, derived from `frontend/src/assets/`.
- `docs/` holds the Markdown documentation wiki, including installation, feature guides, and the node reference.
- `.vitepress/sidebar.ts` defines the grouped docs navigation; `.vitepress/theme/styles/docs.css` styles the reading layout.

## Documentation

Each docs page has a title, description, and `pageClass: docs-layout` in its frontmatter.
Use relative Markdown links between guides and add new pages to the sidebar. VitePress
provides local search, an on-page outline, and previous/next guide navigation.

Run the type check and production build after editing; the build rejects dead page links.
Validate complete Rattish examples with the installed CLI's format, check, and preflight
commands in a disposable folder with the documented resource fixtures. Do not execute
examples during documentation verification. Feature availability notes distinguish the
public desktop release from experimental and legacy integration paths.

The example workflow in `components/landing/workflow.ts` passes `gof rattish check` and
`gof rattish preflight`. Re-validate it if you edit the source.
