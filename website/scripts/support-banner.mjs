// Where the support banner sits on a built page. Each page shows it once, linking only to
// the support page:
// - the home page (index.html): the full banner at the bottom, after the Download section,
//   with only the closing call to action after it;
// - a docs page: the aside banner in the right-hand column, directly under the "On this page"
//   outline, never inside the article. The page also ends with a compact banner in
//   `.support-banner-end`, which docs.css shows only while that column is off screen (narrow
//   windows, or a page with `aside: false`), so a reader sees one banner at any width.
// check-links.mjs runs this on every built page; support-banner.test.mjs tests it.

const bannerPattern = /<aside class="support-banner[^"]*"[^>]*data-support-banner="([a-z]+)"[^>]*>[\s\S]*?<\/aside>/g

function linkProblems(name, banner, supportUrl) {
  const links = [...banner[0].matchAll(/\shref="([^"]*)"/g)].map((match) => match[1].replace(/&amp;/g, '&'))
  if (links.length === 1 && links[0] === supportUrl) return []
  return [`${name}: the support banner links ${links.join(', ') || 'nowhere'}, expected only ${supportUrl}`]
}

function homeProblems(name, html, banners) {
  if (banners.length !== 1) return [`${name}: shows the support banner ${banners.length} times, expected once`]
  const [banner] = banners
  const problems = []
  if (banner[1] !== 'full') problems.push(`${name}: shows the ${banner[1]} support banner, expected the full one`)
  const download = html.indexOf('id="download"')
  if (download < 0 || download > banner.index) {
    problems.push(`${name}: the support banner comes before the Download section; it belongs at the bottom of the page, after it`)
  }
  const later = [...html.slice(banner.index).matchAll(/<section\b[^>]*>/g)].filter((match) => !match[0].includes('aria-labelledby="final-title"'))
  if (later.length > 0) {
    problems.push(`${name}: ${later.length} section(s) follow the support banner; only the closing call to action belongs after it`)
  }
  return problems
}

function docsProblems(name, html, banners) {
  const problems = []
  const main = html.indexOf('<main class="main"')
  const mainEnd = html.indexOf('</main>', main)
  const inArticle = banners.filter((banner) => banner.index > main && banner.index < mainEnd)
  if (inArticle.length > 0) problems.push(`${name}: the support banner is inside the article; it belongs under "On this page"`)

  const asideBanners = banners.filter((banner) => banner[1] === 'aside')
  const endBanners = banners.filter((banner) => banner[1] === 'compact')
  const others = banners.filter((banner) => banner[1] !== 'aside' && banner[1] !== 'compact')
  if (others.length > 0) problems.push(`${name}: shows the ${others.map((banner) => banner[1]).join(', ')} support banner, expected the aside one`)

  const aside = html.indexOf('class="VPDocAside"')
  if (aside < 0) {
    // A page with `aside: false` has no right-hand column, so the end-of-page banner, which
    // then always shows, is its only one.
    if (asideBanners.length > 0) problems.push(`${name}: has an aside support banner but no aside`)
  } else if (asideBanners.length !== 1) {
    problems.push(`${name}: shows the support banner ${asideBanners.length} times in the aside, expected once`)
  } else {
    const outlineEnd = html.indexOf('</nav>', html.indexOf('class="VPDocAsideOutline', aside))
    const spacer = html.indexOf('<div class="spacer"', aside)
    const at = asideBanners[0].index
    if (outlineEnd < 0 || at < outlineEnd || (spacer >= 0 && at > spacer) || (main >= 0 && at > main)) {
      problems.push(`${name}: the aside support banner isn't directly under the "On this page" outline`)
    }
  }

  if (endBanners.length !== 1) {
    problems.push(`${name}: has ${endBanners.length} end-of-page support banners for narrow screens, expected one`)
  } else {
    const at = endBanners[0].index
    if (!/<div class="support-banner-end"[^>]*>\s*$/.test(html.slice(0, at))) {
      problems.push(`${name}: the end-of-page support banner isn't in .support-banner-end, so it would show next to the aside one`)
    }
    if (mainEnd < 0 || at < mainEnd) problems.push(`${name}: the end-of-page support banner comes before the end of the article`)
  }
  return problems
}

export function supportBannerProblems(name, html, supportUrl) {
  const banners = [...html.matchAll(bannerPattern)]
  if (banners.length === 0) return [`${name}: no support banner`]
  const problems = banners.flatMap((banner) => linkProblems(name, banner, supportUrl))
  if (name === 'index.html') return [...problems, ...homeProblems(name, html, banners)]
  if (html.includes('class="VPDoc')) return [...problems, ...docsProblems(name, html, banners)]
  if (banners.length > 1) problems.push(`${name}: shows the support banner ${banners.length} times, expected once`)
  return problems
}
