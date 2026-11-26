"""The website asks for support in one place, buymeacoffee.com/Raticode: a banner at the
bottom of the home page, under "On this page" on docs pages and on the 404 page, the top
menu and the home page footer.

`npm run check:links` in website/ checks the built pages; this checks the sources, so a
change that drops the banner fails the Python suite too.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEBSITE = REPO_ROOT / "website"
SUPPORT_URL = "https://buymeacoffee.com/Raticode"


def read(relative: str) -> str:
    return (WEBSITE / relative).read_text(encoding="utf8")


def test_the_support_address_lives_in_site_data() -> None:
    assert f"export const supportProjectUrl = '{SUPPORT_URL}'" in read("data/site.ts")
    banner = read(".vitepress/theme/components/SupportBanner.vue")
    assert "import { supportProjectUrl } from '../../../data/site'" in banner
    assert ':href="supportProjectUrl"' in banner
    assert "data-support-banner" in banner
    assert ':aria-labelledby="titleId"' in banner
    assert "'support-banner-title'" in banner


def test_the_home_page_shows_the_banner_at_the_bottom() -> None:
    landing = read(".vitepress/theme/components/landing/ProductLanding.vue")
    template = landing[landing.index("<template>") :]
    assert template.count("<SupportBanner />") == 1
    # After the Download section, with only the closing call to action below it.
    assert template.index("<DownloadSection />") < template.index("<SupportBanner />")
    assert template.index("<SupportBanner />") < template.index("<FinalCta />")
    assert template.index("<FinalCta />") < template.index("</main>")


def test_docs_pages_show_the_banner_under_the_outline_not_in_the_article() -> None:
    theme = read(".vitepress/theme/index.ts")
    assert "'aside-outline-after': () => h(SupportBanner, { variant: 'aside' })" in theme
    # Narrow screens hide the aside, so the page also ends with a compact banner that
    # docs.css hides wherever the aside shows.
    assert (
        "'doc-after': () => h('div', { class: 'support-banner-end' }, "
        "h(SupportBanner, { variant: 'compact' }))"
    ) in theme
    css = read(".vitepress/theme/styles/docs.css")
    assert re.search(
        r"@media \(min-width: 1280px\) \{\s*"
        r"\.VPDoc\.has-aside \.support-banner-end \{ display: none; \}",
        css,
    )
    # Nothing puts it inside the article any more.
    assert not (WEBSITE / ".vitepress" / "supportBanner.ts").exists()
    assert "supportBanner" not in read(".vitepress/config.ts")
    assert "app.component('SupportBanner'" not in theme
    for page in (WEBSITE / "docs").rglob("*.md"):
        text = page.read_text(encoding="utf8")
        assert "<SupportBanner" not in text, f"{page} adds the banner by hand"


def test_the_404_page_keeps_its_banner_below_its_links() -> None:
    not_found = read(".vitepress/theme/components/NotFoundPage.vue")
    template = not_found[not_found.index("<template>") :]
    assert template.count("<SupportBanner") == 1
    banner_at = template.index('<SupportBanner variant="compact" />')
    assert template.index("</h1>") < template.index("</ul>") < banner_at


def test_the_menu_footer_and_support_guide_link_the_same_page() -> None:
    config = read(".vitepress/config.ts")
    assert "{ text: 'Buy me a coffee', link: supportProjectUrl }" in config
    footer = read(".vitepress/theme/components/landing/LandingFooter.vue")
    assert ':href="supportProjectUrl"' in footer
    assert f"]({SUPPORT_URL})" in read("docs/support.md")


def test_the_link_check_guards_the_banner_and_the_address() -> None:
    check = read("scripts/check-links.mjs")
    assert "supportProjectUrl" in check
    assert "supportBannerProblems" in check
    placement = read("scripts/support-banner.mjs")
    assert "no support banner" in placement
    assert "inside the article" in placement
    assert 'directly under the "On this page" outline' in placement
    assert "comes before the Download section" in placement
    assert ".support-banner-end" in check
    assert "node --test scripts/*.test.mjs" in read("package.json")
    assert "buymeacoffee" in check


def test_no_page_links_another_donation_page() -> None:
    sources = [
        path
        for folder in ("docs", ".vitepress", "data")
        for path in (WEBSITE / folder).rglob("*")
        if path.suffix in {".md", ".vue", ".ts"}
        and "dist" not in path.parts
        and "cache" not in path.parts
    ]
    pattern = re.compile(r"https?://(?:www\.)?buymeacoffee\.com/[^\s'\")\]]*", re.IGNORECASE)
    for path in sources:
        for link in pattern.findall(path.read_text(encoding="utf8")):
            assert link == SUPPORT_URL, f"{path.relative_to(REPO_ROOT)} links {link}"
