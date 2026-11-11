// Which tab the media bar at the top of the main content speaks for.
//
// A tab the user is looking at already shows its own player, so the bar skips
// it and speaks for the next one: playing tabs first, then the newest to start.
// Gecko is one real Firefox window with its own tabs. While the Raticode
// (gecko) tab is on screen, Firefox's window and its player are in view, so the
// viewed list then holds "gecko" (see viewedBrowserMedia in browserTabs.js).

// The client names a browser tab or a local page preview uses for its session.
function clientNames(path) {
  return [path, `html:${path}`];
}

export function viewedMediaClients(viewedPaths = []) {
  return new Set((Array.isArray(viewedPaths) ? viewedPaths : []).filter(Boolean).flatMap(clientNames));
}

export function mediaForBar(sessions = [], geckoMedia = null, viewedPaths = []) {
  const viewed = viewedMediaClients(viewedPaths);
  const list = [...(Array.isArray(sessions) ? sessions : []), ...(geckoMedia ? [geckoMedia] : [])];
  const geckoInView = viewed.has("gecko");
  return list
    .filter((media) => media && !(typeof media.clientId === "string" && media.clientId && viewed.has(media.clientId)))
    .filter((media) => !(geckoInView && media === geckoMedia))
    .sort((a, b) => Number(b.playing) - Number(a.playing) || b.startedAt - a.startedAt)[0] || null;
}

// The editor tabs on screen: each group's selected tab, while the editor is the area shown.
export function viewedEditorPaths(active, paths = []) {
  return active ? [...new Set(paths.filter(Boolean))] : [];
}
