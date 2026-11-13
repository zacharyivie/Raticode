// The versions the site shows must be the ones the releases use. The desktop publish job
// checks its `vX.Y.Z` tag against frontend/package.json, and the phone publish job checks its
// `mobile-vX.Y.Z` tag against mobile/android/version.properties (scripts/release-candidate.py
// and mobile/android/scripts/release-candidate.py). These helpers read those files so
// `npm run check` and `npm run set-release` can compare data/releases.ts with them.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const stable = /^\d+\.\d+\.\d+$/

export function readSourceVersions(repoRoot) {
  const desktop = JSON.parse(readFileSync(join(repoRoot, 'frontend/package.json'), 'utf8')).version
  const properties = readFileSync(join(repoRoot, 'mobile/android/version.properties'), 'utf8')
  const mobile = properties.match(/^versionName=(\S+)\s*$/m)?.[1]
  if (!desktop || !mobile) throw new Error('Could not read frontend/package.json or mobile/android/version.properties')
  return { desktop, mobile }
}

// Problems with data/releases.ts against the source versions. A prerelease source version
// (X.Y.Z-rc.N) is skipped: the site links stable releases only.
export function versionProblems({ desktopRelease, mobileRelease }, source) {
  const problems = []
  if (stable.test(source.desktop) && desktopRelease.version !== source.desktop) {
    problems.push(
      `data/releases.ts shows desktop ${desktopRelease.version}, but frontend/package.json is ${source.desktop}. ` +
        `Run \`npm run set-release -- ${source.desktop}\`.`,
    )
  }
  if (desktopRelease.tag !== `v${desktopRelease.version}`) {
    problems.push(`data/releases.ts: desktop tag ${desktopRelease.tag} doesn't match version ${desktopRelease.version}`)
  }
  if (stable.test(source.mobile) && mobileRelease.version !== source.mobile) {
    problems.push(
      `data/releases.ts shows Raticode Mobile ${mobileRelease.version}, but mobile/android/version.properties is ` +
        `${source.mobile}. Run \`npm run set-release -- --mobile\`.`,
    )
  }
  if (mobileRelease.tag !== `mobile-v${mobileRelease.version}`) {
    problems.push(`data/releases.ts: mobile tag ${mobileRelease.tag} doesn't match version ${mobileRelease.version}`)
  }
  return problems
}

// Rewrites the one-line `export const mobileRelease = { ... }` entry.
export function setMobileEntry(source, version) {
  const line = /^export const mobileRelease = \{[^\n]*\}$/m
  if (!line.test(source)) throw new Error('data/releases.ts has no one-line `export const mobileRelease = { ... }`')
  return source.replace(line, `export const mobileRelease = { version: '${version}', tag: 'mobile-v${version}' }`)
}

export function isStable(version) {
  return stable.test(version ?? '')
}
