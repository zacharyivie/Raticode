// The site used to be served from /Raticode/. An old address on the new domain moves to
// the same page without that prefix. The result must stay on this site: a path such as
// /Raticode//evil.example or /Raticode/\evil.example would otherwise be read by the
// browser as another host, turning the 404 page into an open redirect.
// No imports, so tests can load this file directly with Node.

export const oldBase = '/Raticode'

/** Where an old /Raticode/... address now lives on this site, or null to stay put. */
export function newAddress(location: { origin: string; pathname: string; search: string; hash: string }, base: string): string | null {
  if (base !== '/') return null
  const prefix = `${oldBase}/`
  if (!location.pathname.startsWith(prefix)) return null
  const rest = location.pathname.slice(oldBase.length)
  // Exactly one leading slash; a second slash or a backslash would start a new host.
  if (!/^\/(?![/\\])/.test(rest) || rest.includes('\\')) return null
  let target: URL
  try {
    target = new URL(rest + location.search + location.hash, location.origin)
  } catch {
    return null
  }
  if (target.origin !== location.origin) return null
  return target.pathname + target.search + target.hash
}
