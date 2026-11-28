// Where the published site lives. The Pages deploy job passes two repository variables:
//   WEBSITE_URL   the site's full address, such as https://raticode.dev/
//   WEBSITE_BASE  the path the site is served from, such as / or /Raticode/
// Both are optional. With neither, the site builds for its own domain, https://raticode.dev/.
// With only WEBSITE_URL, the base is that URL's path, so another domain needs just
// WEBSITE_URL=https://<domain>/ and project Pages needs
// WEBSITE_URL=https://<owner>.github.io/<repo>/. If both are set they must agree. The
// sitemap, robots.txt, canonical links and social preview image use the full address.

export const defaultSiteUrl = 'https://raticode.dev/'

// Email for support and contact. Components use these; Markdown pages spell the address out,
// and `npm run check:links` fails if any mailto link on the built site uses another address.
export const supportEmail = 'support@raticode.dev'
export const supportMailto = `mailto:${supportEmail}`

// The one place to chip in for Raticode's development. The support banner (home page and
// the foot of every docs page), the landing hero's Support Raticode button, the top nav and
// the footer link here, and
// `npm run check:links` fails if the banner is missing or a page links another donation page.
export const supportProjectUrl = 'https://buymeacoffee.com/Raticode'

// /.well-known/security.txt (RFC 9116) tells security researchers how to report a problem.
// Expires should be less than a year ahead, so every build sets it 360 days out; the site
// is rebuilt with each release, which keeps it current. Policy points at the support
// page's security section.
export const securityTxtPath = '.well-known/security.txt'
export const securityTxtLifetimeDays = 360

export function securityTxt(siteUrl: string, now: Date = new Date()): string {
  const expires = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + securityTxtLifetimeDays))
  return [
    `Contact: ${supportMailto}`,
    `Expires: ${expires.toISOString().replace(/\.\d{3}Z$/, 'Z')}`,
    'Preferred-Languages: en',
    `Canonical: ${siteUrl}${securityTxtPath}`,
    `Policy: ${siteUrl}docs/support#report-a-security-problem`,
    '',
  ].join('\n')
}

export interface SiteLocation {
  /** Full address of the site root, ending in a slash. */
  url: string
  /** Path the site is served from, starting and ending with a slash. */
  base: string
}

type Env = Record<string, string | undefined>

function given(value: string | undefined): string | undefined {
  const trimmed = value?.trim()
  return trimmed ? trimmed : undefined
}

export function siteLocation(env: Env): SiteLocation {
  const urlInput = given(env.WEBSITE_URL)
  const baseInput = given(env.WEBSITE_BASE)

  if (baseInput !== undefined && !/^\/(?:[^/?#\s]+\/)*$/.test(baseInput)) {
    throw new Error(`WEBSITE_BASE must start and end with "/", like / or /Raticode/ (got "${baseInput}")`)
  }

  let parsed: URL
  try {
    parsed = new URL(urlInput ?? defaultSiteUrl)
  } catch {
    throw new Error(`WEBSITE_URL must be a full address such as https://raticode.dev/ (got "${urlInput}")`)
  }
  if (parsed.protocol !== 'https:' || parsed.search || parsed.hash || parsed.username || parsed.password) {
    throw new Error(`WEBSITE_URL must be a plain https:// address with no query or fragment (got "${urlInput}")`)
  }
  if (!parsed.pathname.endsWith('/')) parsed.pathname += '/'

  if (urlInput === undefined && baseInput !== undefined) {
    // Local builds for another base keep the default host.
    parsed.pathname = baseInput
  } else if (baseInput !== undefined && baseInput !== parsed.pathname) {
    throw new Error(`WEBSITE_BASE (${baseInput}) does not match the path of WEBSITE_URL (${parsed.href})`)
  }

  return { url: parsed.href, base: parsed.pathname }
}
