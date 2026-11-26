import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { defineConfig, type HeadConfig } from 'vitepress'
import { securityTxt, securityTxtPath, siteLocation, supportProjectUrl } from '../data/site'
import { homeStructuredData, lastChanged, markdownFor, pageUrl, repo, socialImage } from './seo'
import { docsSidebar } from './sidebar'

// The site lives at https://raticode.dev/. Another address (a fork, or project Pages) sets
// the WEBSITE_URL repository variable instead; see data/site.ts.
const { url: siteUrl, base } = siteLocation(process.env)
const description =
  'Raticode is a local studio for agentic coding workflows. Wire shell commands, scripts, HTTP calls and coding agents into workflows you can see, schedule and run anywhere.'
const image = socialImage(siteUrl)

export default defineConfig({
  lang: 'en-US',
  title: 'Raticode',
  description,
  base,
  cleanUrls: true,
  appearance: 'dark',
  lastUpdated: false,
  srcExclude: ['README.md'],
  markdown: { languageAlias: { rattish: 'yaml' } },

  head: [
    ['link', { rel: 'icon', type: 'image/png', sizes: '32x32', href: `${base}brand/roundel-32.png` }],
    ['link', { rel: 'apple-touch-icon', href: `${base}brand/roundel-192.png` }],
    ['meta', { name: 'theme-color', content: '#07061a' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:site_name', content: 'Raticode' }],
    ['meta', { property: 'og:locale', content: 'en_US' }],
    ['meta', { property: 'og:image', content: image.url }],
    ['meta', { property: 'og:image:type', content: 'image/jpeg' }],
    ['meta', { property: 'og:image:width', content: String(image.width) }],
    ['meta', { property: 'og:image:height', content: String(image.height) }],
    ['meta', { property: 'og:image:alt', content: image.alt }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    ['meta', { name: 'twitter:image', content: image.url }],
    ['meta', { name: 'twitter:image:alt', content: image.alt }],
  ],

  // Every page with the date its source last changed in Git. The 404 page is not a page
  // to index.
  sitemap: {
    hostname: siteUrl,
    lastmodDateOnly: true,
    transformItems: (items) =>
      items
        .filter((item) => item.url !== '404')
        .map((item) => ({ ...item, lastmod: lastChanged(markdownFor(item.url)) })),
  },

  // Node reference pages are titled by the node's name alone, such as "agent".
  transformPageData(pageData) {
    if (/^docs\/nodes\/(?!index\.md$)/.test(pageData.relativePath) && !/\bnode\b/i.test(pageData.title)) {
      pageData.titleTemplate = ':title node | Raticode'
    }
  },

  // Each page names its own address, title and summary for search engines and link previews.
  transformHead({ pageData, title }): HeadConfig[] {
    const pageDescription = pageData.description || description
    if (pageData.isNotFound || pageData.relativePath === '404.md') {
      return [['meta', { name: 'robots', content: 'noindex' }]]
    }
    const url = pageUrl(pageData.relativePath, siteUrl)
    const head: HeadConfig[] = [
      ['link', { rel: 'canonical', href: url }],
      ['meta', { property: 'og:url', content: url }],
      ['meta', { property: 'og:title', content: title }],
      ['meta', { property: 'og:description', content: pageDescription }],
      ['meta', { name: 'twitter:title', content: title }],
      ['meta', { name: 'twitter:description', content: pageDescription }],
    ]
    if (pageData.relativePath === 'index.md') {
      head.push(['script', { type: 'application/ld+json' }, JSON.stringify(homeStructuredData(siteUrl, pageDescription))])
    } else {
      head.push(['meta', { property: 'og:type', content: 'article' }])
    }
    return head
  },

  // robots.txt must name the sitemap by its full address, and security.txt its own address
  // and an expiry date, so both are written per build. The Pages upload step must include
  // hidden files, or .well-known/ is left out of the published site.
  buildEnd({ outDir }) {
    writeFileSync(join(outDir, 'robots.txt'), `User-agent: *\nAllow: /\n\nSitemap: ${siteUrl}sitemap.xml\n`)
    const securityFile = join(outDir, securityTxtPath)
    mkdirSync(dirname(securityFile), { recursive: true })
    writeFileSync(securityFile, securityTxt(siteUrl))
  },

  themeConfig: {
    logo: { src: '/brand/roundel-64.png', alt: '' },
    siteTitle: 'Raticode',

    nav: [
      { text: 'Docs', link: '/docs/', activeMatch: '^/docs/' },
      { text: 'Download', link: '/#download' },
      { text: 'Changelog', link: `${repo}/releases` },
      { text: 'Buy me a coffee', link: supportProjectUrl },
    ],

    socialLinks: [{ icon: 'github', link: repo, ariaLabel: 'Raticode on GitHub' }],

    search: { provider: 'local' },
    sidebar: { '/docs/': docsSidebar },
    sidebarMenuLabel: 'Browse docs',
    docFooter: { prev: 'Previous guide', next: 'Next guide' },

    editLink: {
      pattern: `${repo}/edit/main/website/:path`,
      text: 'Edit this page on GitHub',
    },

    outline: { level: [2, 3], label: 'On this page' },
  },
})
