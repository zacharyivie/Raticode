// Search engine helpers for the VitePress config: page addresses, sitemap dates and the
// structured data on the home page. Everything here describes what the site and the
// release manifest already say; keep it in step with data/releases.ts.
import { execFileSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  desktopPlatforms,
  desktopRelease,
  mobileApk,
  mobileAssetUrl,
  mobileRelease,
  releaseUrl,
} from '../data/releases'

export const repo = 'https://github.com/zacharyivie/Raticode'
const websiteDir = dirname(dirname(fileURLToPath(import.meta.url)))

// The address of a page, matching the clean URLs VitePress serves.
export function pageUrl(relativePath: string, siteUrl: string): string {
  const path = relativePath.replace(/(^|\/)index\.md$/, '$1').replace(/\.md$/, '')
  return new URL(path, siteUrl).href
}

// The 1200 x 630 link preview image, made from the brand roundel and a real app screenshot.
export function socialImage(siteUrl: string) {
  return {
    url: `${siteUrl}brand/social-card.jpg`,
    width: 1200,
    height: 630,
    alt: 'Raticode: build the maze, let the agents run it. A local studio for agentic coding workflows, with a workflow graph and run timeline from the desktop app.',
  }
}

// The files a page is built from, relative to website/. The home page is a set of
// landing components fed by the release manifest.
function sourcesOf(page: string): string[] {
  if (page === 'index.md') return ['index.md', '.vitepress/theme/components/landing', 'data/releases.ts']
  return [page]
}

let shallow: boolean | undefined
function isShallowClone(): boolean {
  if (shallow === undefined) {
    try {
      shallow = execFileSync('git', ['rev-parse', '--is-shallow-repository'], { cwd: websiteDir, encoding: 'utf8' }).trim() === 'true'
    } catch {
      shallow = true
    }
  }
  return shallow
}

// The date a page last changed in Git, as YYYY-MM-DD. In a shallow clone every file
// would look as new as the latest commit, so there is no date rather than a wrong one.
export function lastChanged(relativePath: string): string | undefined {
  if (isShallowClone()) return undefined
  try {
    const out = execFileSync('git', ['log', '-1', '--format=%cI', '--', ...sourcesOf(relativePath)], {
      cwd: websiteDir,
      encoding: 'utf8',
    }).trim()
    return out ? out.slice(0, 10) : undefined
  } catch {
    return undefined
  }
}

// Sitemap URLs are page paths without the base; map one back to its Markdown file.
export function markdownFor(sitemapUrl: string): string {
  if (sitemapUrl === '' || sitemapUrl.endsWith('/')) return `${sitemapUrl}index.md`
  return `${sitemapUrl}.md`
}

// schema.org data for the home page: the site, the desktop app and the Android app.
export function homeStructuredData(siteUrl: string, description: string): object {
  const image = socialImage(siteUrl).url
  const free = { '@type': 'Offer', price: '0', priceCurrency: 'USD' }
  const license = 'https://spdx.org/licenses/AGPL-3.0-only.html'
  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'WebSite',
        '@id': `${siteUrl}#website`,
        name: 'Raticode',
        url: siteUrl,
        description,
        inLanguage: 'en-US',
      },
      {
        '@type': 'SoftwareApplication',
        '@id': `${siteUrl}#desktop`,
        name: 'Raticode',
        description,
        url: siteUrl,
        image,
        screenshot: `${siteUrl}images/screenshots/studio-workflow-run-1440.webp`,
        applicationCategory: 'DeveloperApplication',
        operatingSystem: desktopPlatforms.map((platform) => platform.name).join(', '),
        softwareVersion: desktopRelease.version,
        datePublished: desktopRelease.publishedAt,
        downloadUrl: releaseUrl,
        releaseNotes: releaseUrl,
        license,
        isAccessibleForFree: true,
        offers: free,
        sameAs: [repo],
      },
      {
        '@type': 'MobileApplication',
        '@id': `${siteUrl}#mobile`,
        name: 'Raticode Mobile',
        description: 'The Android companion to the Raticode desktop app: talk to Rem, follow threads and tasks, and work with projects on a paired computer.',
        url: `${siteUrl}docs/mobile/`,
        applicationCategory: 'DeveloperApplication',
        operatingSystem: 'Android 10 or newer',
        softwareVersion: mobileRelease.version,
        downloadUrl: mobileAssetUrl(mobileApk.assetName),
        license,
        isAccessibleForFree: true,
        offers: free,
      },
    ],
  }
}
