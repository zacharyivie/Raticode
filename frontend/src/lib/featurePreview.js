import { SIDEBAR_PLUGINS } from "./plugins.js";

// Feature Preview lists every experimental feature. A feature that is off is
// hidden everywhere else in the app. Experimental sidebar plugins join this
// list automatically; `backend` features are read by the desktop backend at
// startup, so changing one restarts it.
export const FEATURE_PREVIEWS = Object.freeze([
  ...SIDEBAR_PLUGINS.filter((plugin) => plugin.experimental).map((plugin) => ({
    id: plugin.id,
    label: plugin.label,
    description: plugin.description,
    plugin: plugin.id,
    backend: plugin.id === "organizations",
  })),
  {
    id: "device-network",
    label: "Phone and desktop pairing",
    description: "Pair phones and other desktops over your local network to chat with Rem and share work.",
    plugin: null,
    backend: true,
  },
].map(Object.freeze));

export function featurePreview(id) {
  return FEATURE_PREVIEWS.find((feature) => feature.id === id) ?? null;
}
