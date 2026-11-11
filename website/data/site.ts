// Where the published site lives. The Pages deploy job passes two repository variables:
//   WEBSITE_URL   the site's full address, such as https://raticode.dev/
//   WEBSITE_BASE  the path the site is served from, such as / or /Raticode/
// Both are optional. With neither, the site builds for project Pages at
// https://zacharyivie.github.io/Raticode/. With only WEBSITE_URL, the base is that URL's
// path, so a custom domain needs just WEBSITE_URL=https://<domain>/. If both are set they
// must agree. The sitemap, canonical links and social preview image use the full address.

export const defaultSiteUrl = 'https://zacharyivie.github.io/Raticode/'

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
