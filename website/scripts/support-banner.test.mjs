// Run with `npm test` from website/ (part of `npm run check`).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { supportBannerProblems } from './support-banner.mjs'

const url = 'https://buymeacoffee.com/Raticode'
const banner = (variant, href = url) =>
  `<aside class="support-banner is-${variant}" aria-labelledby="support-banner-${variant}-title" data-support-banner="${variant}" data-v-1><p id="support-banner-${variant}-title" class="title">Support Raticode</p><a class="cta" href="${href}" rel="noopener">Buy me a coffee</a></aside>`
const outline = '<nav aria-labelledby="doc-outline-aria-label" class="VPDocAsideOutline" data-v-2><div class="content"><div class="outline-title">On this page</div><ul></ul></div></nav>'
const asideColumn = (inside) =>
  `<div class="aside"><div class="aside-container"><div class="aside-content"><div class="VPDocAside">${inside}<div class="spacer"></div></div></div></div></div>`
const article = (inside = '') =>
  `<main class="main"><div class="vp-doc"><h1 id="install">Install</h1><p>Raticode runs on macOS, Windows and Linux.</p>${inside}<h2 id="a">A</h2><p>More.</p></div></main>`
const end = (inside = banner('compact')) => `<footer class="VPDocFooter"></footer><div class="support-banner-end">${inside}</div>`
const docsPage = ({ aside = asideColumn(outline + banner('aside')), body = article(), after = end() } = {}) =>
  `<nav><a href="${url}">Buy me a coffee</a></nav><div class="VPDoc has-sidebar has-aside"><div class="container">${aside}<div class="content"><div class="content-container">${body}${after}</div></div></div></div>`
const docs = 'docs/installation.html'

const download = '<section id="download" class="rc-section download" aria-labelledby="download-title"><h2 id="download-title">Start</h2></section>'
const finalCta = '<section class="rc-section final" aria-labelledby="final-title"><h2 id="final-title">Run the maze</h2></section>'
const tour = '<section id="tour"><h2 id="tour-title">See it</h2></section>'
const homePage = (body) => `<header><h1>Your agents keep working</h1></header><main>${body}</main>`

test('a docs page with the banner under "On this page" and the narrow-screen one at the end passes', () => {
  assert.deepEqual(supportBannerProblems(docs, docsPage(), url), [])
})

test('a docs page with aside: false shows only the end-of-page banner', () => {
  assert.deepEqual(supportBannerProblems(docs, docsPage({ aside: '' }), url), [])
})

test('the home page with the full banner after Download, before the closing call to action, passes', () => {
  assert.deepEqual(supportBannerProblems('index.html', homePage(tour + download + banner('full') + finalCta), url), [])
})

test('a page without the banner fails', () => {
  assert.deepEqual(supportBannerProblems(docs, docsPage({ aside: asideColumn(outline), after: '' }), url), [`${docs}: no support banner`])
})

test('a banner inside the article fails', () => {
  const problems = supportBannerProblems(docs, docsPage({ body: article(banner('compact')), after: '' }), url).join('\n')
  assert.match(problems, /inside the article/)
})

test('a docs page without the aside banner fails', () => {
  const problems = supportBannerProblems(docs, docsPage({ aside: asideColumn(outline) }), url).join('\n')
  assert.match(problems, /0 times in the aside, expected once/)
})

test('an aside banner above the outline fails', () => {
  const problems = supportBannerProblems(docs, docsPage({ aside: asideColumn(banner('aside') + outline) }), url).join('\n')
  assert.match(problems, /isn't directly under the "On this page" outline/)
})

test('an aside banner below the spacer, at the bottom of the column, fails', () => {
  const column = `<div class="aside"><div class="VPDocAside">${outline}<div class="spacer"></div>${banner('aside')}</div></div>`
  const problems = supportBannerProblems(docs, docsPage({ aside: column }), url).join('\n')
  assert.match(problems, /isn't directly under the "On this page" outline/)
})

test('two aside banners fail', () => {
  const problems = supportBannerProblems(docs, docsPage({ aside: asideColumn(outline + banner('aside') + banner('aside')) }), url).join('\n')
  assert.match(problems, /2 times in the aside/)
})

test('an end-of-page banner outside its wrapper fails, since it would show on wide screens too', () => {
  const problems = supportBannerProblems(docs, docsPage({ after: banner('compact') }), url).join('\n')
  assert.match(problems, /isn't in \.support-banner-end/)
})

test('a docs page without the narrow-screen banner fails', () => {
  const problems = supportBannerProblems(docs, docsPage({ after: '' }), url).join('\n')
  assert.match(problems, /0 end-of-page support banners/)
})

test('the full banner on a docs page fails', () => {
  const problems = supportBannerProblems(docs, docsPage({ after: end(banner('compact')) + banner('full') }), url).join('\n')
  assert.match(problems, /shows the full support banner, expected the aside one/)
})

test('a home page banner above the Download section fails', () => {
  const problems = supportBannerProblems('index.html', homePage(tour + banner('full') + download + finalCta), url).join('\n')
  assert.match(problems, /comes before the Download section/)
  assert.match(problems, /1 section\(s\) follow the support banner/)
})

test('a home page banner after the first content section fails', () => {
  const problems = supportBannerProblems('index.html', homePage(tour + banner('full') + finalCta), url).join('\n')
  assert.match(problems, /comes before the Download section/)
})

test('the home page needs exactly one full banner', () => {
  assert.match(supportBannerProblems('index.html', homePage(download + banner('compact') + finalCta), url).join('\n'), /expected the full one/)
  assert.match(supportBannerProblems('index.html', homePage(download + banner('full') + banner('full') + finalCta), url).join('\n'), /2 times, expected once/)
})

test('a banner linking anywhere else fails', () => {
  const problems = supportBannerProblems(docs, docsPage({ aside: asideColumn(outline + banner('aside', 'https://buymeacoffee.com/someone')) }), url).join('\n')
  assert.match(problems, /expected only https:\/\/buymeacoffee\.com\/Raticode/)
})
