import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { defineConfig, type HeadConfig } from 'vitepress'
import { siteLocation } from '../data/site'
import { docsSidebar } from './sidebar'

// Project Pages serves the site from https://zacharyivie.github.io/Raticode/. A custom
// domain sets the WEBSITE_URL repository variable instead; see data/site.ts.
const { url: siteUrl, base } = siteLocation(process.env)
const repo = 'https://github.com/zacharyivie/Raticode'
const description =
  'Raticode is a local studio for agentic coding workflows. Wire shell commands, scripts, HTTP calls and coding agents into workflows you can see, schedule and run anywhere.'
const socialImage = `${siteUrl}brand/roundel-512.png`

// The address of a page, matching the clean URLs VitePress serves.
function pageUrl(relativePath: string): string {
  const path = relativePath.replace(/(^|\/)index\.md$/, '$1').replace(/\.md$/, '')
  return new URL(path, siteUrl).href
}

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
    ['meta', { property: 'og:image', content: socialImage }],
    ['meta', { property: 'og:image:alt', content: 'The Raticode roundel' }],
    ['meta', { name: 'twitter:card', content: 'summary' }],
  ],

  sitemap: { hostname: siteUrl },

  // Each page names its own address, title and summary for search engines and link previews.
  transformHead({ pageData }): HeadConfig[] {
    if (pageData.isNotFound || pageData.relativePath === '404.md') return []
    const url = pageUrl(pageData.relativePath)
    const title = pageData.title && pageData.relativePath !== 'index.md' ? `${pageData.title} | Raticode` : 'Raticode'
    return [
      ['link', { rel: 'canonical', href: url }],
      ['meta', { property: 'og:url', content: url }],
      ['meta', { property: 'og:title', content: title }],
      ['meta', { property: 'og:description', content: pageData.description || description }],
    ]
  },

  // robots.txt must name the sitemap by its full address, so it is written per build.
  buildEnd({ outDir }) {
    writeFileSync(join(outDir, 'robots.txt'), `User-agent: *\nAllow: /\n\nSitemap: ${siteUrl}sitemap.xml\n`)
  },

  themeConfig: {
    logo: { src: '/brand/roundel-64.png', alt: '' },
    siteTitle: 'Raticode',

    nav: [
      { text: 'Docs', link: '/docs/', activeMatch: '^/docs/' },
      { text: 'Download', link: '/#download' },
      { text: 'Changelog', link: `${repo}/releases` },
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
