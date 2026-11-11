// Confirms that every download in data/releases.ts exists on the published GitHub release
// (and, once data/releases.ts marks a Raticode Mobile release as published, its APK too)
// and that the manifest names the newest published desktop release. Needs network access:
// `npm run check:downloads`. Uses the public API without credentials (GITHUB_TOKEN, when
// set, only raises the rate limit).
//
// Exit codes: 0 all good; 1 a problem; 3 the manifest names a release that isn't published
// yet. The Pages deploy job treats 3 as "wait for the release", because the site is moved to
// a new version before that version's tag publishes it.
import { assetUrl, cliBinaries, desktopPlatforms, desktopRelease, mobileApk, mobileRelease } from '../data/releases.ts'
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

function waitForRelease(reason) {
  console.error(`${reason}. The site waits for ${desktopRelease.tag} to be published.`)
  process.exit(NOT_PUBLISHED)
}

const problems = []
// Drafts aren't visible without credentials, so a draft and a missing tag both read as 404.
const release = await get(`${api}/tags/${desktopRelease.tag}`)
if (!release) waitForRelease(`${desktopRelease.tag} has no published release yet`)
if (release.draft) waitForRelease(`${desktopRelease.tag} is still a draft`)
const published = new Set(release.assets.map((asset) => asset.name))

const wanted = [
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

// Raticode Mobile publishes separately; its APK is checked only once the site links to it.
const mobile = await checkMobile({ mobileRelease, mobileApk }, get)
problems.push(...mobile.problems)

if (problems.length) {
  console.error(problems.join('\n'))
  process.exit(1)
}
console.log(`All ${wanted.length} downloads exist on ${desktopRelease.tag}, the newest desktop release.`)
console.log(mobile.note)
