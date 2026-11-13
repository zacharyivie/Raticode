# Raticode website

The public Raticode site, built with [VitePress](https://vitepress.dev/) 1.6. This file is excluded from the published pages.

`package.json` overrides VitePress's Vite 5 with Vite 6.4.6 or later, because no Vite 5 release fixes the dev-server advisories (path traversal, `fs.deny` bypass, cross-origin script execution) that `npm audit` reports. Drop the override when VitePress itself depends on a patched Vite.

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
npm run check      # versions match the release sources, typecheck, build, then check links, #anchors, sitemap and robots.txt
npm run check:downloads  # every desktop download and the signed phone APK exist on their published releases (network; exit 3 = not published yet)
npm run set-release -- X.Y.Z  # move the site to a new desktop release, and the phone APK to mobile/android/version.properties
npm run set-release -- --mobile  # move only the phone APK to mobile/android/version.properties
npm run build      # static output in .vitepress/dist
npm run preview    # serve the build
```

The site lives at `https://raticode.dev/`, and the build targets that address by default.
For another address set `WEBSITE_URL` (for another domain, `WEBSITE_URL=https://<domain>/`),
and optionally `WEBSITE_BASE` for the path; `data/site.ts` reads both, and `check:links` must
see the same values as the build. The sitemap, `robots.txt`, canonical links and the social
preview image are generated from that address.

## Search engines

`.vitepress/config.ts` and `.vitepress/seo.ts` give every page its own title, description,
canonical link, Open Graph and Twitter card tags, with `public/brand/social-card.jpg`
(1200 x 630) as the preview image. The home page carries schema.org `WebSite`,
`SoftwareApplication` and `MobileApplication` data built from `data/releases.ts`. The
sitemap dates each page by its last Git commit (the deploy checks out full history for
this); `robots.txt` allows every crawler and names the sitemap. Unknown addresses get the
theme's `NotFoundPage.vue`, marked `noindex`, which also forwards old `/Raticode/...`
addresses to the same page. `theme/oldAddress.ts` decides where, and only ever to a path on
this site (tested in `tests/unit/test_website_workflow.py`). `npm run check:links` fails if any of this goes missing.
Steps only the site owner can take, such as Search Console, are kept in the maintainer's notes, outside the repository.

## Publishing

`.github/workflows/website.yml` deploys the site to GitHub Pages from `main` after
`npm run check` and `npm run check:downloads` pass, and again when a desktop or phone release
is published. While either release the site links isn't public yet, the deploy waits and the
live site stays as it was, so it never links a missing file.
The same two variables are read from the repository's Actions variables. Setup, the
release step and custom domains are covered in `docs/releasing.md` under "The website".

Suggested text for the repository's **About** box (gear icon on the repository page):

- Description: `Local studio for agentic coding workflows: wire shell commands, scripts and coding agents into workflows you can see, schedule and run anywhere.`
- Website: `https://raticode.dev`
- Topics: `ai-agents`, `workflow-automation`, `developer-tools`, `electron`, `claude-code`, `codex`, `local-first`, `rattish`

## Layout

- `index.md` renders the landing page through `ProductLanding.vue`.
- `.vitepress/theme/components/landing/` holds the landing sections.
- `.vitepress/theme/styles/` holds brand tokens and the landing navbar overrides.
- `data/releases.ts` is the reviewed release manifest; `npm run set-release` updates it.
- `data/site.ts` resolves the site's address and base path.
- `public/brand/` holds the roundel and Rem sprites, derived from `frontend/src/assets/`.
- `public/images/screenshots/` holds app screenshots; `data/screenshots.ts` lists them with their size and alt text.
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

## Screenshots

The landing page's tour (`components/landing/AppTour.vue`) and the docs show real screenshots of
the desktop app. In Markdown, use `<AppScreenshot name="rem-home" caption="…" />`; add `eager`
only for an image near the top of a page. The component sets the width, height, `srcset` and
lazy loading from `data/screenshots.ts`, and links to the full-size file.

To retake them:

- Run the real app headlessly (Electron on a hidden Xvfb display through
  `frontend/electron/tests/background-display.cjs`) with its own profile, data folder and `HOME`,
  so no real projects, threads, accounts or tokens can appear.
- Use demo content only: a fictional project with fictional authors, and stand-in provider CLIs
  on `PATH` that return a model list and scripted replies. Nothing contacts a provider.
- Capture 1440 x 900 windows in the dark theme. Save WebP at 960 and 1440 px wide
  (`magick in.png -strip -define webp:method=6 -quality 82 out.webp`), or one size for a crop,
  and update `data/screenshots.ts`.
- The workflow in the screenshots is the landing example in `components/landing/workflow.ts`.
  Keep the two in step.
- Browser tabs: serve fictional pages from a localhost server, and open tabs per engine with
  `webContents.send("gofer:browser-open-tab", { url, engine })`. Headless Firefox and Playwright's
  Chrome for Testing (`RATICODE_CHROME_PATH`) can fill Firefox and Chrome tabs. A Raticode (gecko)
  page is Firefox's own X window and doesn't appear in `capturePage`, so keep a chromium tab active.
- Phone screenshots (`phone-*`, `<AppScreenshot … phone />`): run a debug build of
  `mobile/android` on a private read-only emulator (`-no-window`) paired with
  `mobile/android/scripts/live-device-fixture.py`, the disposable desktop peer that keeps its keys
  in memory and never touches the computer's keyring. Capture with `adb exec-out screencap -p` and
  save WebP at 360 and 720 px wide. Delete the build output afterwards (about 1.4 GB).
