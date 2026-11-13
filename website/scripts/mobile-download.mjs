// The Raticode Mobile part of `npm run check:downloads`. The phone app publishes from its
// own `mobile-vX.Y.Z` tags, and only signed APKs are ever published. The site always links
// the APK named in data/releases.ts, so this check decides whether that link works yet:
//
// - the release is missing or still a draft: `waiting`, and check:downloads exits 3, so the
//   deploy holds the old site until the release is public;
// - the release is public but lacks the APK or checksums, a newer phone release exists, or
//   its certificate differs from the one the site shows: a problem (exit 1).
//
// checkMobile() takes the manifest entries, a `get(url)` that returns parsed API JSON or null
// for 404, and a `download(url)` that returns a public release asset's parsed JSON, so it can
// be tested without the network.

const api = 'https://api.github.com/repos/zacharyivie/Raticode/releases'

export const APK_CHECKS = 'apk-checks.json'

export function fingerprint(value) {
  const text = String(value ?? '')
    .toLowerCase()
    .replace(/[\s:]/g, '')
  return /^[0-9a-f]{64}$/.test(text) ? text : null
}

export async function checkMobile({ mobileRelease, mobileApk, mobileSigningCertificate }, get, download) {
  const problems = []
  const wanted = [mobileApk.assetName, mobileApk.checksums]
  const expected = fingerprint(mobileSigningCertificate)
  if (!expected) problems.push('data/releases.ts: mobileSigningCertificate is not a SHA-256 fingerprint')

  // Drafts aren't visible without credentials, so a draft and a missing tag both read as 404.
  const release = await get(`${api}/tags/${mobileRelease.tag}`)
  if (!release || release.draft) {
    return {
      problems,
      waiting: true,
      checked: 0,
      note: `${mobileRelease.tag} has no public release yet, so the site can't link raticode-mobile APK ${mobileRelease.version}`,
    }
  }

  const assets = new Map((release.assets ?? []).map((asset) => [asset.name, asset]))
  for (const name of wanted) {
    if (!assets.has(name)) problems.push(`missing on ${mobileRelease.tag}: ${name}`)
  }

  // The publish job writes the APK's signing certificate into apk-checks.json.
  const checksAsset = assets.get(APK_CHECKS)
  if (!checksAsset) {
    problems.push(`missing on ${mobileRelease.tag}: ${APK_CHECKS}, which records the signing certificate`)
  } else if (expected) {
    const checks = await download(checksAsset.browser_download_url)
    const actual = fingerprint(checks?.certificate_sha256)
    if (!checks?.signed || !actual) {
      problems.push(`${mobileRelease.tag}: ${APK_CHECKS} doesn't record a signed APK`)
    } else if (checks.version !== mobileRelease.version) {
      problems.push(`${mobileRelease.tag}: ${APK_CHECKS} describes version ${checks.version}`)
    } else if (actual !== expected) {
      problems.push(
        `${mobileRelease.tag} is signed with certificate ${actual}, but data/releases.ts shows ` +
          `${expected}. Fix mobileSigningCertificate.`,
      )
    }
  }

  const releases = (await get(`${api}?per_page=50`)) ?? []
  const newest = releases.find((r) => !r.draft && !r.prerelease && /^mobile-v\d/.test(r.tag_name))
  if (newest && newest.tag_name !== mobileRelease.tag) {
    problems.push(
      `data/releases.ts names ${mobileRelease.tag}, but the newest published mobile release is ${newest.tag_name}`,
    )
  }
  return {
    problems,
    waiting: false,
    checked: wanted.length + 1,
    note: `The signed APK and its checksums exist on ${mobileRelease.tag}.`,
  }
}
