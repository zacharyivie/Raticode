// Checks every internal link and asset in the built site, including links in
// landing components and #anchors, which VitePress's dead-link check does not cover.
// It also checks what search engines read: the sitemap, robots.txt, each page's title,
// description, canonical address and link preview tags, the 404 page and the home page's
// structured data.
// Run after `npm run build`: `npm run check:links`. External links are listed, not fetched.
import { execFileSync } from 'node:child_process'
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { join, relative, posix } from 'node:path'
import { fileURLToPath } from 'node:url'
import { siteLocation } from '../data/site.ts'
import { desktopRelease } from '../data/releases.ts'

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
  // Responsive images: every srcset candidate must exist too.
  for (const [, set] of html.matchAll(/\ssrcset="([^"]*)"/g)) {
    for (const candidate of set.split(',')) {
      const url = candidate.trim().split(/\s+/)[0]
      if (!url || /^(https?:|data:)/.test(url)) continue
      const absolute = url.startsWith('/') ? url : posix.join(posix.dirname(page), url)
      checkSitePath(page, absolute, url)
    }
  }
  // og:url and og:image carry absolute addresses in content="".
  for (const [, link] of html.matchAll(/\scontent="(https:\/\/[^"]*)"/g)) {
    if (link.startsWith(siteUrl)) checkSitePath(page, link.slice(siteOrigin.length), link)
  }
}

// The address a built page is served at, matching cleanUrls.
function urlOf(file) {
  const path = relative(dist, file).split('\\').join('/').replace(/(^|\/)index\.html$/, '$1').replace(/\.html$/, '')
  return siteUrl + path
}
const decode = (value) => value.replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>')
function metaContent(html, key, name) {
  const match = html.match(new RegExp(`<meta ${key}="${name}" content="([^"]*)"`))
  return match ? decode(match[1]) : undefined
}

// Each page has its own title, description and canonical address, and link preview tags
// with absolute addresses. The 404 page tells search engines not to index it.
const pages = htmlFiles(dist).filter((file) => relative(dist, file) !== '404.html')
const titles = new Map()
const descriptions = new Map()
for (const file of pages) {
  const page = base + relative(dist, file).split('\\').join('/')
  const html = readFileSync(file, 'utf8')
  const head = html.split('</head>')[0]
  const url = urlOf(file)
  const title = decode(head.match(/<title>([^<]*)<\/title>/)?.[1] ?? '')
  const description = metaContent(head, 'name', 'description')
  if (!title) problems.push(`${page}: no <title>`)
  if (!description) problems.push(`${page}: no meta description`)
  else if (description.length > 170) problems.push(`${page}: meta description is ${description.length} characters; keep it under 170`)
  titles.set(title, [...(titles.get(title) ?? []), page])
  descriptions.set(description, [...(descriptions.get(description) ?? []), page])
  const canonical = [...head.matchAll(/<link rel="canonical" href="([^"]*)"/g)].map((m) => m[1])
  if (canonical.length !== 1 || canonical[0] !== url) problems.push(`${page}: canonical is ${canonical.join(', ') || 'missing'}, expected ${url}`)
  if (metaContent(head, 'property', 'og:url') !== url) problems.push(`${page}: og:url is not ${url}`)
  if (metaContent(head, 'property', 'og:title') !== title) problems.push(`${page}: og:title differs from the title`)
  if (metaContent(head, 'property', 'og:description') !== description) problems.push(`${page}: og:description differs from the description`)
  for (const [key, name] of [['property', 'og:image'], ['name', 'twitter:image']]) {
    const image = metaContent(head, key, name)
    if (!image?.startsWith(siteUrl)) problems.push(`${page}: ${name} is not an absolute address on ${siteUrl}`)
  }
  if (metaContent(head, 'name', 'twitter:card') !== 'summary_large_image') problems.push(`${page}: twitter:card is not summary_large_image`)
  if (/<meta name="robots" content="[^"]*noindex/.test(head)) problems.push(`${page}: is marked noindex`)
  if ((html.match(/<h1[\s>]/g) ?? []).length !== 1) problems.push(`${page}: has ${(html.match(/<h1[\s>]/g) ?? []).length} <h1> headings, expected 1`)
}
for (const [title, where] of titles) if (where.length > 1) problems.push(`Title "${title}" is used by ${where.join(', ')}`)
for (const [description, where] of descriptions) if (description && where.length > 1) problems.push(`Description "${description}" is used by ${where.join(', ')}`)

const notFound = join(dist, '404.html')
if (!existsSync(notFound)) {
  problems.push('404.html is missing')
} else {
  const html = readFileSync(notFound, 'utf8')
  if (!/<meta name="robots" content="noindex">/.test(html)) problems.push('404.html: no robots noindex')
  if (/rel="canonical"/.test(html)) problems.push('404.html: must not name a canonical address')
  // VitePress leaves the 404 body empty and renders the theme's NotFoundPage in the browser.
  const chunks = join(dist, 'assets', 'chunks')
  const theme = existsSync(chunks) && readdirSync(chunks).filter((name) => /^theme\..*\.js$/.test(name))
    .some((name) => readFileSync(join(chunks, name), 'utf8').includes("This page isn't in the maze"))
  if (!theme) problems.push('404.html: the custom NotFoundPage is not in the built theme')
}

// The home page describes the site and the app as schema.org data.
const homeHtml = readFileSync(join(dist, 'index.html'), 'utf8')
const ldScripts = [...homeHtml.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map((m) => m[1])
if (ldScripts.length !== 1) {
  problems.push(`index.html: expected one JSON-LD block, found ${ldScripts.length}`)
} else {
  try {
    const graph = JSON.parse(ldScripts[0])['@graph'] ?? []
    const app = graph.find((item) => item['@type'] === 'SoftwareApplication')
    if (!graph.some((item) => item['@type'] === 'WebSite' && item.url === siteUrl)) problems.push('index.html: JSON-LD has no WebSite at the site address')
    if (!app) problems.push('index.html: JSON-LD has no SoftwareApplication')
    else {
      if (app.url !== siteUrl) problems.push(`index.html: SoftwareApplication url is ${app.url}`)
      if (app.softwareVersion !== desktopRelease.version) problems.push(`index.html: SoftwareApplication version ${app.softwareVersion} is not ${desktopRelease.version}`)
      if (!String(app.image).startsWith(siteUrl)) problems.push('index.html: SoftwareApplication image is not on the site')
      else checkSitePath('index.html JSON-LD', app.image.slice(siteOrigin.length), app.image)
    }
  } catch (error) {
    problems.push(`index.html: JSON-LD does not parse: ${error.message}`)
  }
}

// The sitemap lists every page, and only pages, under the full site address, with the
// date it last changed (unless this is a shallow clone, where Git can't tell).
let shallow = true
try {
  shallow = execFileSync('git', ['rev-parse', '--is-shallow-repository'], { encoding: 'utf8' }).trim() === 'true'
} catch {}
const sitemapFile = join(dist, 'sitemap.xml')
if (!existsSync(sitemapFile)) {
  problems.push('sitemap.xml is missing')
} else {
  const xml = readFileSync(sitemapFile, 'utf8')
  const urls = [...xml.matchAll(/<url>([\s\S]*?)<\/url>/g)].map((m) => m[1])
  const entries = urls.map((url) => url.match(/<loc>([^<]+)<\/loc>/)?.[1] ?? '')
  if (entries.length === 0) problems.push('sitemap.xml lists no pages')
  for (const [index, entry] of entries.entries()) {
    if (!entry.startsWith(siteUrl)) problems.push(`sitemap.xml: ${entry} is not under ${siteUrl}`)
    else checkSitePath('sitemap.xml', entry.slice(siteOrigin.length), entry)
    const lastmod = urls[index].match(/<lastmod>([^<]+)<\/lastmod>/)?.[1]
    if (lastmod && !/^\d{4}-\d{2}-\d{2}$/.test(lastmod)) problems.push(`sitemap.xml: ${entry} lastmod ${lastmod} is not a date`)
    if (!lastmod && !shallow) problems.push(`sitemap.xml: ${entry} has no lastmod`)
  }
  const expected = new Set(pages.map(urlOf))
  for (const url of expected) if (!entries.includes(url)) problems.push(`sitemap.xml: ${url} is missing`)
  for (const entry of entries) if (!expected.has(entry)) problems.push(`sitemap.xml: ${entry} is not a page`)
}
const robotsFile = join(dist, 'robots.txt')
if (!existsSync(robotsFile)) {
  problems.push('robots.txt is missing')
} else {
  const robots = readFileSync(robotsFile, 'utf8')
  if (!robots.includes(`Sitemap: ${siteUrl}sitemap.xml`)) problems.push(`robots.txt does not point at ${siteUrl}sitemap.xml`)
  if (!/^User-agent: \*$/m.test(robots) || !/^Allow: \/$/m.test(robots)) problems.push('robots.txt does not allow every crawler')
  if (/^Disallow:\s*\S/m.test(robots)) problems.push('robots.txt disallows part of the site')
}

if (process.argv.includes('--list-external')) {
  for (const link of [...external].sort()) console.log(link)
}
if (problems.length) {
  console.error([...new Set(problems)].join('\n'))
  console.error(`\n${new Set(problems).size} problem(s) with links or search engine tags.`)
  process.exit(1)
}
console.log(`Internal links, sitemap, robots.txt and search engine tags OK across ${htmlFiles(dist).length} pages; ${external.size} external links not fetched.`)
