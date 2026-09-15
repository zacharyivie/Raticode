// Deterministic delivery assets; keep the original poses as the source of truth.
// Run with Electron, using xvfb-run on a headless Linux machine.
const { app, nativeImage } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const source = path.join(__dirname, "../src/assets/rem-options");
const output = path.join(source, "display");
fs.mkdirSync(output, { recursive: true });
for (const name of ["indigo-android", "indigo-android-open", "indigo-android-wave", "indigo-android-sleep"]) {
  const original = nativeImage.createFromPath(path.join(source, `${name}.png`));
  if (original.isEmpty()) throw new Error(`Missing avatar ${name}`);
  for (const size of [224, 336]) {
    fs.writeFileSync(path.join(output, `${name}-${size}.png`), original.resize({ width: size, height: size, quality: "best" }).toPNG());
  }
}

app.quit();
