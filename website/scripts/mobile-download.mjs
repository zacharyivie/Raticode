// The Raticode Mobile part of `npm run check:downloads`. The phone app publishes from its
// own `mobile-vX.Y.Z` tags, and only signed APKs are ever published, so the website shows
// the APK download only once data/releases.ts marks that release as published.
//
// checkMobile() takes the manifest entries and a `get(url)` that returns parsed JSON or
// null for 404, so it can be tested without the network.

const api = 'https://api.github.com/repos/zacharyivie/Raticode/releases'

export async function checkMobile({ mobileRelease, mobileApk }, get) {
  const problems = []
  const wanted = [mobileApk.assetName, mobileApk.checksums]
  if (!mobileRelease.published) {
    return {
      problems,
      checked: 0,
      note: `Raticode Mobile ${mobileRelease.version}: not published yet, so the site shows no APK download.`,
    }
  }
  const release = await get(`${api}/tags/${mobileRelease.tag}`)
  if (!release || release.draft) {
    problems.push(
      `data/releases.ts marks ${mobileRelease.tag} as published, but it has no public release. ` +
        'Publish it with its tag first, or set published: false.',
    )
    return { problems, checked: 0, note: '' }
  }
  const assets = new Set((release.assets ?? []).map((asset) => asset.name))
  for (const name of wanted) {
    if (!assets.has(name)) problems.push(`missing on ${mobileRelease.tag}: ${name}`)
  }
  const releases = (await get(`${api}?per_page=50`)) ?? []
  const newest = releases.find((r) => !r.draft && !r.prerelease && /^mobile-v\d/.test(r.tag_name))
  if (newest && newest.tag_name !== mobileRelease.tag) {
    problems.push(
      `data/releases.ts names ${mobileRelease.tag}, but the newest published mobile release is ${newest.tag_name}`,
    )
  }
  return { problems, checked: wanted.length, note: `The APK exists on ${mobileRelease.tag}.` }
}
