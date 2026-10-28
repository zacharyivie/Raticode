// Reviewed release manifest. Every URL points at an immutable asset on a published
// GitHub release; update this file when a new release is approved.

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
  version: '0.3.9',
  tag: 'v0.3.9',
  publishedAt: '2026-10-03',
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
    primary: { label: 'Download .dmg', detail: 'Disk image', assetName: 'Raticode-0.3.9-arm64.dmg' },
    alternates: [{ label: '.zip', detail: 'Zip archive', assetName: 'Raticode-0.3.9-arm64.zip' }],
    checksums: 'checksums-macos.txt',
    note: 'Not yet notarized. Approve it in System Settings › Privacy & Security after first launch.',
  },
  {
    os: 'windows',
    name: 'Windows',
    arch: 'x64',
    primary: { label: 'Download installer', detail: 'Setup .exe', assetName: 'Raticode-Setup-0.3.9.exe' },
    alternates: [],
    checksums: 'checksums-windows.txt',
    note: 'Not yet code-signed. SmartScreen may show an Unknown publisher prompt.',
  },
  {
    os: 'linux',
    name: 'Linux',
    arch: 'x64',
    primary: { label: 'Download AppImage', detail: 'Portable', assetName: 'Raticode-0.3.9-x86_64.AppImage' },
    alternates: [
      { label: '.deb', detail: 'Debian, Ubuntu', assetName: 'Raticode-0.3.9-amd64.deb' },
      { label: '.rpm', detail: 'Fedora, openSUSE', assetName: 'Raticode-0.3.9-x86_64.rpm' },
    ],
    checksums: 'checksums-linux.txt',
    note: 'Mark the AppImage executable, or install the .deb or .rpm package.',
  },
]

export const cliBinaries: CliBinary[] = [
  { os: 'linux', label: 'Linux x64', assetName: 'gof-linux-x64' },
  { os: 'macos', label: 'macOS Apple Silicon', assetName: 'gof-macos-arm64' },
  { os: 'windows', label: 'Windows x64', assetName: 'gof-windows-x64.exe' },
  { os: 'linux', label: '.deb', assetName: 'gofer-flow-cli_0.3.9_amd64.deb' },
  { os: 'linux', label: '.rpm', assetName: 'gofer-flow-cli-0.3.9-1.x86_64.rpm' },
]
