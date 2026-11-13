// Confirms that every download in data/releases.ts exists on its published GitHub release:
// the desktop files on the newest published `vX.Y.Z` release, and the signed Raticode Mobile
// APK on its `mobile-vX.Y.Z` release. Needs network access: `npm run check:downloads`. Uses
// the public API without credentials (GITHUB_TOKEN, when set, only raises the API rate limit
// and is never sent with asset downloads).
//
// Exit codes: 0 all good; 1 a problem; 3 the manifest names a desktop or mobile release that
// isn't published yet. The Pages deploy job treats 3 as "wait for the release": the site is
// moved to new versions before their tags publish them, and it deploys only once every
// download it links exists.
import {
  assetUrl,
  cliBinaries,
  desktopPlatforms,
  desktopRelease,
  mobileApk,
  mobileRelease,
  mobileSigningCertificate,
} from '../data/releases.ts'
import { checkMobile } from './mobile-download.mjs'

const api = 'https://api.github.com/repos/zacharyivie/Raticode/releases'
const headers = { 'User-Agent': 'raticode-website-check', Accept: 'application/vnd.github+json' }
if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`

const NOT_PUBLISHED = 3

async function get(url) {
  const response = await fetch(url, { headers })
  if (response.status === 404) return null
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`)
  return response.json()
}

// Public release assets, fetched without the token.
async function download(url) {
  const response = await fetch(url, { headers: { 'User-Agent': headers['User-Agent'] } })
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`)
  return JSON.parse(await response.text())
}

const waiting = []
const problems = []
let wanted = []

// Drafts aren't visible without credentials, so a draft and a missing tag both read as 404.
const release = await get(`${api}/tags/${desktopRelease.tag}`)
if (!release || release.draft) {
  waiting.push(`${desktopRelease.tag} ${release ? 'is still a draft' : 'has no published release yet'}`)
} else {
  const published = new Set(release.assets.map((asset) => asset.name))
  wanted = [
    ...desktopPlatforms.flatMap((p) => [p.primary.assetName, ...p.alternates.map((a) => a.assetName), p.checksums]),
    ...cliBinaries.map((binary) => binary.assetName),
  ]
  for (const name of wanted) {
    if (!published.has(name)) problems.push(`missing on ${desktopRelease.tag}: ${name} (${assetUrl(name)})`)
  }
  const releases = (await get(`${api}?per_page=20`)) ?? []
  const newest = releases.find((r) => !r.draft && !r.prerelease && /^v\d/.test(r.tag_name))
  if (newest && newest.tag_name !== desktopRelease.tag) {
    problems.push(`data/releases.ts names ${desktopRelease.tag}, but the newest published desktop release is ${newest.tag_name}`)
  }
}

const mobile = await checkMobile({ mobileRelease, mobileApk, mobileSigningCertificate }, get, download)
problems.push(...mobile.problems)
if (mobile.waiting) waiting.push(mobile.note)

if (problems.length) {
  console.error(problems.join('\n'))
  process.exit(1)
}
if (waiting.length) {
  console.error(`${waiting.join('\n')}\nThe site waits until every release it links is published.`)
  process.exit(NOT_PUBLISHED)
}
console.log(`All ${wanted.length} downloads exist on ${desktopRelease.tag}, the newest desktop release.`)
console.log(mobile.note)
