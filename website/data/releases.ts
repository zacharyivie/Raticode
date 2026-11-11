// Reviewed release manifest. Every URL points at an immutable asset on a published
// GitHub release. Move it to a new release with `npm run set-release -- X.Y.Z` (see
// docs/releasing.md); file names below follow from the version.

export type DesktopOs = 'macos' | 'windows' | 'linux'

export interface Artifact {
  label: string
  detail: string
  assetName: string
}

export interface DesktopPlatform {
  os: DesktopOs
  name: string
  arch: string
  primary: Artifact
  alternates: Artifact[]
  checksums: string
  note: string
}

export interface CliBinary {
  os: DesktopOs
  label: string
  assetName: string
}

const repo = 'https://github.com/zacharyivie/Raticode'

export const desktopRelease = {
  version: '0.5.3',
  tag: 'v0.5.3',
  publishedAt: '2026-11-11',
  // Mac builds are Developer ID signed and notarized from 0.5.3 on.
  macNotarized: true,
}

const v = desktopRelease.version

// Raticode Mobile (Android) releases separately, from `mobile-vX.Y.Z` tags. Its publish job
// makes a staged candidate public only when the APK is signed with the release key, because
// Android won't install an unsigned APK. Keep `published: false` until that tag's release is
// public; `npm run set-release -- --mobile X.Y.Z` then switches the download card to the APK.
// Kept on one line so the desktop `set-release` never edits it.
export const mobileRelease = { version: '0.32.0', tag: 'mobile-v0.32.0', published: false }

export const mobileApk = {
  label: 'Download APK',
  assetName: `raticode-mobile-${mobileRelease.version}.apk`,
  checksums: 'checksums-android.txt',
}

export const mobileReleaseUrl = `${repo}/releases/tag/${mobileRelease.tag}`

export function mobileAssetUrl(assetName: string): string {
  return `${repo}/releases/download/${mobileRelease.tag}/${assetName}`
}

export const releaseUrl = `${repo}/releases/tag/${desktopRelease.tag}`
export const allReleasesUrl = `${repo}/releases`

export function assetUrl(assetName: string): string {
  return `${repo}/releases/download/${desktopRelease.tag}/${assetName}`
}

export const desktopPlatforms: DesktopPlatform[] = [
  {
    os: 'macos',
    name: 'macOS',
    arch: 'Apple Silicon',
    primary: { label: 'Download .dmg', detail: 'Disk image', assetName: `Raticode-${v}-arm64.dmg` },
    alternates: [{ label: '.zip', detail: 'Zip archive', assetName: `Raticode-${v}-arm64.zip` }],
    checksums: 'checksums-macos.txt',
    note: desktopRelease.macNotarized
      ? 'Signed with a Developer ID and notarized by Apple. Drag it to Applications and open it.'
      : 'Not yet notarized. Approve it in System Settings › Privacy & Security after first launch.',
  },
  {
    os: 'windows',
    name: 'Windows',
    arch: 'x64',
    primary: { label: 'Download installer', detail: 'Setup .exe', assetName: `Raticode-Setup-${v}.exe` },
    alternates: [],
    checksums: 'checksums-windows.txt',
    note: 'Not yet code-signed. SmartScreen may show an Unknown publisher prompt.',
  },
  {
    os: 'linux',
    name: 'Linux',
    arch: 'x64',
    primary: { label: 'Download AppImage', detail: 'Portable', assetName: `Raticode-${v}-x86_64.AppImage` },
    alternates: [
      { label: '.deb', detail: 'Debian, Ubuntu', assetName: `Raticode-${v}-amd64.deb` },
      { label: '.rpm', detail: 'Fedora, openSUSE', assetName: `Raticode-${v}-x86_64.rpm` },
    ],
    checksums: 'checksums-linux.txt',
    note: 'Mark the AppImage executable, or install the .deb or .rpm package.',
  },
]

export const cliBinaries: CliBinary[] = [
  { os: 'linux', label: 'Linux x64', assetName: 'raticode-cli-linux-x64' },
  { os: 'macos', label: 'macOS Apple Silicon', assetName: 'raticode-cli-macos-arm64' },
  { os: 'windows', label: 'Windows x64', assetName: 'raticode-cli-windows-x64.exe' },
  { os: 'linux', label: '.deb', assetName: `raticode-cli_${v}_amd64.deb` },
  { os: 'linux', label: '.rpm', assetName: `raticode-cli-${v}-1.x86_64.rpm` },
]
