// Builds Raticode's macOS speech helper (frontend/native/macos/RaticodeSpeech.swift) as a
// universal binary in dist/macos-speech/raticode-speech. Mac release builds bundle it in
// Raticode.app/Contents/Resources/speech; the desktop app passes its path to the backend.
//
// Needs Xcode or the Command Line Tools (swiftc, lipo). On other systems it does nothing
// unless --require is given.

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");
const source = path.join(repoRoot, "frontend", "native", "macos", "RaticodeSpeech.swift");
const outDir = path.join(repoRoot, "dist", "macos-speech");
const output = path.join(outDir, "raticode-speech");
const required = process.argv.includes("--require");
// Electron's own minimum macOS version.
const minimumMacos = "12.0";

// macOS reads a command-line tool's Info.plist from this section. Raticode.app's own
// Info.plist also carries the purpose string, since macOS attributes the permission to it.
const infoPlist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key>
  <string>dev.raticode.speech-helper</string>
  <key>CFBundleName</key>
  <string>Raticode speech helper</string>
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>Raticode turns your dictation into text on this Mac.</string>
</dict>
</plist>
`;

function run(command, args) {
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited with ${result.status}`);
}

if (process.platform !== "darwin") {
  if (required) {
    console.error("The macOS speech helper can only be built on macOS.");
    process.exit(1);
  }
  console.log("Skipping the macOS speech helper: not building on macOS.");
  process.exit(0);
}

try {
  fs.mkdirSync(outDir, { recursive: true });
  const plistPath = path.join(outDir, "Info.plist");
  fs.writeFileSync(plistPath, infoPlist);
  const slices = [];
  for (const arch of ["arm64", "x86_64"]) {
    const slice = path.join(outDir, `raticode-speech-${arch}`);
    run("xcrun", [
      "swiftc", "-O",
      "-target", `${arch}-apple-macos${minimumMacos}`,
      "-framework", "Speech",
      "-framework", "AVFoundation",
      "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist", "-Xlinker", plistPath,
      "-o", slice,
      source,
    ]);
    slices.push(slice);
  }
  run("xcrun", ["lipo", "-create", "-output", output, ...slices]);
  for (const slice of slices) fs.rmSync(slice, { force: true });
  fs.chmodSync(output, 0o755);
  console.log(`Built ${output}`);
} catch (error) {
  console.error(`The macOS speech helper could not be built: ${error.message}`);
  if (required) process.exit(1);
  console.error("Dictation will use Raticode's offline speech model instead.");
}
