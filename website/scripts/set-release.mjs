// Moves the website to a desktop release: `npm run set-release -- X.Y.Z [--date YYYY-MM-DD]`.
// Run it while preparing the release, before pushing to `stage` (docs/releasing.md). It
// updates the manifest in data/releases.ts and the versioned file names in the install
// guide, then lists any other mention of the old version for you to reword by hand.
// The deploy job keeps the old site live until the new release is published.
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const args = process.argv.slice(2)

// `npm run set-release -- --mobile X.Y.Z` points the site's Raticode Mobile download at that
// version's APK. Run it only after the `mobile-vX.Y.Z` tag has published a signed release;
// `npm run check:downloads` then confirms the APK is there.
if (args[0] === '--mobile') {
  const mobileVersion = args[1]
  if (!mobileVersion || !/^\d+\.\d+\.\d+$/.test(mobileVersion)) {
    console.error('Usage: npm run set-release -- --mobile X.Y.Z')
    process.exit(2)
  }
  const path = join(root, 'data/releases.ts')
  const source = readFileSync(path, 'utf8')
  const line = /^export const mobileRelease = \{[^\n]*\}$/m
  if (!line.test(source)) {
    console.error('data/releases.ts has no one-line `export const mobileRelease = { ... }`')
    process.exit(2)
  }
  const entry = `export const mobileRelease = { version: '${mobileVersion}', tag: 'mobile-v${mobileVersion}', published: true }`
  writeFileSync(path, source.replace(line, entry))
  console.log(`The site's Raticode Mobile download now points at mobile-v${mobileVersion}. Run \`npm run check:downloads\`.`)
  process.exit(0)
}
const version = args.find((arg) => !arg.startsWith('--'))
const dateFlag = args.indexOf('--date')
const date = dateFlag >= 0 ? args[dateFlag + 1] : new Date().toISOString().slice(0, 10)

function fail(message) {
  console.error(message)
  process.exit(2)
}

if (!version || !/^\d+\.\d+\.\d+$/.test(version)) fail('Usage: npm run set-release -- X.Y.Z [--date YYYY-MM-DD]')
if (!/^\d{4}-\d{2}-\d{2}$/.test(date ?? '') || Number.isNaN(Date.parse(date))) fail(`--date must be YYYY-MM-DD (got "${date}")`)

const manifestPath = join(root, 'data/releases.ts')
let manifest = readFileSync(manifestPath, 'utf8')
const current = manifest.match(/^ {2}version: '(\d+\.\d+\.\d+)',$/m)?.[1]
if (!current) fail('data/releases.ts has no `version:` line in desktopRelease')

function newer(a, b) {
  const [x, y] = [a, b].map((s) => s.split('.').map(Number))
  for (let i = 0; i < 3; i += 1) if (x[i] !== y[i]) return x[i] > y[i]
  return false
}
if (version !== current && !newer(version, current)) fail(`${version} is older than the site's current release ${current}`)

function setField(source, field, value) {
  const pattern = new RegExp(`^( {2}${field}: )[^,\\n]+,$`, 'm')
  if (!pattern.test(source)) fail(`data/releases.ts has no \`${field}:\` line in desktopRelease`)
  return source.replace(pattern, `$1${value},`)
}
manifest = setField(manifest, 'version', `'${version}'`)
manifest = setField(manifest, 'tag', `'v${version}'`)
manifest = setField(manifest, 'publishedAt', `'${date}'`)
// Every desktop release after 0.5.2 is Developer ID signed and notarized; the release
// workflow refuses to publish one that isn't (docs/releasing.md).
manifest = setField(manifest, 'macNotarized', newer(version, '0.5.2') ? 'true' : 'false')
writeFileSync(manifestPath, manifest)

// Versioned file names in shell examples, such as ./Raticode-0.5.2-x86_64.AppImage.
const escaped = current.replace(/\./g, '\\.')
const fileNames = new RegExp(`((?:Raticode|raticode-cli)[-_](?:Setup-)?)${escaped}(?=[-_.])`, 'g')
const guidePath = join(root, 'docs/installation.md')
const guide = readFileSync(guidePath, 'utf8')
const renamed = guide.replace(fileNames, `$1${version}`)
writeFileSync(guidePath, renamed)
const renamedCount = (guide.match(fileNames) ?? []).length

function markdownFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return markdownFiles(path)
    return name.endsWith('.md') ? [path] : []
  })
}
const leftovers = []
for (const file of [join(root, 'index.md'), ...markdownFiles(join(root, 'docs'))]) {
  readFileSync(file, 'utf8').split('\n').forEach((line, index) => {
    if (new RegExp(`(?<![\\d.])${escaped}(?![\\d])`).test(line)) {
      leftovers.push(`  ${relative(root, file)}:${index + 1}: ${line.trim().slice(0, 140)}`)
    }
  })
}

console.log(`Site moved from ${current} to ${version} (published ${date}); ${renamedCount} file name(s) renamed in docs/installation.md.`)
if (leftovers.length) {
  console.log(`\nThese lines still mention ${current}. Reword any that describe the newest release:`)
  console.log(leftovers.join('\n'))
}
console.log('\nThen run `npm run check`.')
