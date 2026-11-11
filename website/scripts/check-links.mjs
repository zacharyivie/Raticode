// Checks every internal link and asset in the built site, including links in
// landing components and #anchors, which VitePress's dead-link check does not cover.
// Run after `npm run build`: `npm run check:links`. External links are listed, not fetched.
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { join, relative, posix } from 'node:path'
import { fileURLToPath } from 'node:url'
import { siteLocation } from '../data/site.ts'

const dist = fileURLToPath(new URL('../.vitepress/dist/', import.meta.url))
// Use the same WEBSITE_URL and WEBSITE_BASE as the build.
const { url: siteUrl, base } = siteLocation(process.env)
const siteOrigin = new URL(siteUrl).origin

if (!existsSync(dist)) {
  console.error('No build found. Run `npm run build` first.')
  process.exit(2)
}

function htmlFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return htmlFiles(path)
    return name.endsWith('.html') ? [path] : []
  })
}

// Resolve a site path (without base) to a built file, following cleanUrls.
function resolveFile(sitePath) {
  const clean = decodeURIComponent(sitePath).replace(/^\/+/, '')
  const candidates = clean === '' || clean.endsWith('/')
    ? [join(dist, clean, 'index.html')]
    : [join(dist, clean), join(dist, `${clean}.html`), join(dist, clean, 'index.html')]
  return candidates.find((path) => existsSync(path) && statSync(path).isFile())
}

const idCache = new Map()
function idsIn(file) {
  if (!idCache.has(file)) {
    const html = readFileSync(file, 'utf8')
    idCache.set(file, new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1])))
  }
  return idCache.get(file)
}

const problems = []
const external = new Set()

function checkSitePath(page, sitePath, shown) {
  const path = sitePath.split(/[?#]/)[0]
  if (!path.startsWith(base) || !resolveFile(path.slice(base.length))) {
    problems.push(`${page}: ${shown} does not exist`)
  }
}
const attr = /\s(?:href|src)="([^"]*)"/g

for (const file of htmlFiles(dist)) {
  const page = base + relative(dist, file).split('\\').join('/')
  const html = readFileSync(file, 'utf8')
  for (const [, raw] of html.matchAll(attr)) {
    const link = raw.replace(/&amp;/g, '&')
    if (!link || /^(mailto:|tel:|javascript:|data:)/.test(link)) continue
    // Absolute links to this site (canonical, og:url, og:image) must resolve like internal ones.
    if (link.startsWith(siteUrl)) { checkSitePath(page, link.slice(siteOrigin.length), link); continue }
    if (/^https?:\/\//.test(link)) { external.add(link.split('#')[0]); continue }
    if (link.startsWith('//')) { external.add(`https:${link}`); continue }

    const [pathPart, hash] = link.split('#')
    let target
    if (pathPart === '') {
      target = file
    } else {
      const absolute = pathPart.startsWith('/') ? pathPart : posix.join(posix.dirname(page), pathPart)
      const sitePath = absolute.split('?')[0]
      if (!sitePath.startsWith(base)) {
        problems.push(`${page}: ${link} is outside the site base ${base}`)
        continue
      }
      target = resolveFile(sitePath.slice(base.length))
      if (!target) { problems.push(`${page}: ${link} does not exist`); continue }
    }
    if (hash && target.endsWith('.html') && !idsIn(target).has(decodeURIComponent(hash))) {
      problems.push(`${page}: ${link} has no #${hash} anchor`)
    }
  }
  // og:url and og:image carry absolute addresses in content="".
  for (const [, link] of html.matchAll(/\scontent="(https:\/\/[^"]*)"/g)) {
    if (link.startsWith(siteUrl)) checkSitePath(page, link.slice(siteOrigin.length), link)
  }
}

// The sitemap lists every page under the full site address, and robots.txt points at it.
const sitemapFile = join(dist, 'sitemap.xml')
if (!existsSync(sitemapFile)) {
  problems.push('sitemap.xml is missing')
} else {
  const entries = [...readFileSync(sitemapFile, 'utf8').matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1])
  if (entries.length === 0) problems.push('sitemap.xml lists no pages')
  for (const entry of entries) {
    if (!entry.startsWith(siteUrl)) problems.push(`sitemap.xml: ${entry} is not under ${siteUrl}`)
    else checkSitePath('sitemap.xml', entry.slice(siteOrigin.length), entry)
  }
}
const robotsFile = join(dist, 'robots.txt')
if (!existsSync(robotsFile)) {
  problems.push('robots.txt is missing')
} else if (!readFileSync(robotsFile, 'utf8').includes(`Sitemap: ${siteUrl}sitemap.xml`)) {
  problems.push(`robots.txt does not point at ${siteUrl}sitemap.xml`)
}

if (process.argv.includes('--list-external')) {
  for (const link of [...external].sort()) console.log(link)
}
if (problems.length) {
  console.error([...new Set(problems)].join('\n'))
  console.error(`\n${new Set(problems).size} broken internal link(s).`)
  process.exit(1)
}
console.log(`Internal links OK across ${htmlFiles(dist).length} pages; ${external.size} external links not fetched.`)
