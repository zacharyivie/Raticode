import { defineConfig } from 'vitepress'
import { docsSidebar } from './sidebar'

// Project Pages serves the site from /Raticode/. A custom domain sets WEBSITE_BASE=/.
const base = process.env.WEBSITE_BASE ?? '/Raticode/'
const repo = 'https://github.com/zacharyivie/Raticode'

export default defineConfig({
  lang: 'en-US',
  title: 'Raticode',
  description:
    'Raticode is a local studio for agentic coding workflows. Wire shell commands, scripts, HTTP calls and coding agents into workflows you can see, schedule and run anywhere.',
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
    ['meta', { property: 'og:image', content: `${base}brand/roundel-512.png` }],
  ],

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
