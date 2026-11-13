// Part of `npm run check`: the desktop and Raticode Mobile versions in data/releases.ts must
// match frontend/package.json and mobile/android/version.properties, the files each publish
// job checks its tag against. Offline; exits 1 on a mismatch.
import { fileURLToPath } from 'node:url'
import { desktopRelease, mobileRelease } from '../data/releases.ts'
import { readSourceVersions, versionProblems } from './versions.mjs'

const repoRoot = fileURLToPath(new URL('../..', import.meta.url))
const source = readSourceVersions(repoRoot)
const problems = versionProblems({ desktopRelease, mobileRelease }, source)
if (problems.length) {
  console.error(problems.join('\n'))
  process.exit(1)
}
console.log(
  `Site versions match the release sources: desktop ${desktopRelease.version}, Raticode Mobile ${mobileRelease.version}.`,
)
