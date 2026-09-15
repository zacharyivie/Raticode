const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");

function usage(exitCode = 1) {
  const output = exitCode === 0 ? console.log : console.error;
  output(`Usage:
  node scripts/bump-version.cjs <version> [--appimage-sha256 <sha256>] [--cli-sha256 <sha256>]

Examples:
  node scripts/bump-version.cjs 0.1.1
  node scripts/bump-version.cjs 0.1.1 --appimage-sha256 0123abcd...
  node scripts/bump-version.cjs 0.1.1 --appimage-sha256 0123abcd... --cli-sha256 abcd0123...
`);
  process.exit(exitCode);
}

const args = process.argv.slice(2);
if (args.includes("--help") || args.includes("-h")) {
  usage(0);
}

const version = args[0];
if (!version || version.startsWith("-")) {
  usage();
}

const semverPattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$/;
if (!semverPattern.test(version)) {
  console.error(`Invalid version "${version}". Expected SemVer like 0.1.1.`);
  process.exit(1);
}

let appImageSha256;
let cliSha256;
for (let index = 1; index < args.length; index += 1) {
  const arg = args[index];
  if (arg === "--appimage-sha256") {
    appImageSha256 = args[index + 1];
    index += 1;
    if (!appImageSha256) {
      console.error("--appimage-sha256 requires a checksum value.");
      process.exit(1);
    }
    continue;
  }

  if (arg === "--cli-sha256") {
    cliSha256 = args[index + 1];
    index += 1;
    if (!cliSha256) {
      console.error("--cli-sha256 requires a checksum value.");
      process.exit(1);
    }
    continue;
  }

  {
    console.error(`Unknown argument: ${arg}`);
    usage();
  }
}

if (appImageSha256 && !/^[a-fA-F0-9]{64}$/.test(appImageSha256)) {
  console.error("--appimage-sha256 must be a 64-character SHA-256 hex digest.");
  process.exit(1);
}

if (cliSha256 && !/^[a-fA-F0-9]{64}$/.test(cliSha256)) {
  console.error("--cli-sha256 must be a 64-character SHA-256 hex digest.");
  process.exit(1);
}

const updates = [];

updateTextFile("pyproject.toml", (text) =>
  replaceOnce(
    text,
    /^version = "([^"]+)"$/m,
    `version = "${version}"`,
    "pyproject.toml project version",
  ),
);

updateTextFile("uv.lock", (text) =>
  replaceOnce(
    text,
    /^(name = "gofer-flow"\nversion = ")[^"]+("$)/m,
    `$1${version}$2`,
    "uv.lock project version",
  ),
);

updateJsonFile("frontend/package.json", (json) => {
  json.version = version;
  return json;
});

updateJsonFile("frontend/package-lock.json", (json) => {
  json.version = version;
  if (json.packages?.[""]) {
    json.packages[""].version = version;
  }
  return json;
});

updateTextFile("packaging/arch/PKGBUILD", (text) => {
  let next = replaceOnce(
    text,
    /^pkgver=.*$/m,
    `pkgver=${version}`,
    "PKGBUILD pkgver",
  );

  if (appImageSha256) {
    next = replaceOnce(
      next,
      /(sha256sums_x86_64=\(\s*['"])[a-fA-F0-9]{64}(['"]\s*\))/,
      `$1${appImageSha256.toLowerCase()}$2`,
      "PKGBUILD AppImage checksum",
    );
  }

  return next;
});

updateTextFile("packaging/arch/.SRCINFO", (text) => {
  let next = replaceAll(
    text,
    /pkgver = [^\n]+/g,
    `pkgver = ${version}`,
    ".SRCINFO pkgver",
  );

  next = replaceOnce(
    next,
    /source_x86_64 = Raticode-.+-x86_64\.AppImage::https:\/\/github\.com\/zacharyivie\/gofer-flow\/releases\/download\/v[^/]+\/Raticode-.+-x86_64\.AppImage/,
    `source_x86_64 = Raticode-${version}-x86_64.AppImage::https://github.com/zacharyivie/gofer-flow/releases/download/v${version}/Raticode-${version}-x86_64.AppImage`,
    ".SRCINFO AppImage source URL",
  );

  if (appImageSha256) {
    next = replaceOnce(
      next,
      /sha256sums_x86_64 = [a-fA-F0-9]{64}/,
      `sha256sums_x86_64 = ${appImageSha256.toLowerCase()}`,
      ".SRCINFO AppImage checksum",
    );
  }

  return next;
});

updateTextFile("packaging/arch-cli/PKGBUILD", (text) => {
  let next = replaceOnce(
    text,
    /^pkgver=.*$/m,
    `pkgver=${version}`,
    "CLI PKGBUILD pkgver",
  );

  if (cliSha256) {
    next = replaceOnce(
      next,
      /(sha256sums_x86_64=\(\n\s+")(?:[a-fA-F0-9]{64}|SKIP)("\n\))/,
      `$1${cliSha256.toLowerCase()}$2`,
      "CLI PKGBUILD checksum",
    );
  }

  return next;
});

updateTextFile("packaging/arch-cli/.SRCINFO", (text) => {
  let next = replaceAll(
    text,
    /pkgver = [^\n]+/g,
    `pkgver = ${version}`,
    "CLI .SRCINFO pkgver",
  );

  next = replaceOnce(
    next,
    /source_x86_64 = gof-linux-x64-[^:]+::https:\/\/github\.com\/zacharyivie\/gofer-flow\/releases\/download\/v[^/]+\/gof-linux-x64/,
    `source_x86_64 = gof-linux-x64-${version}::https://github.com/zacharyivie/gofer-flow/releases/download/v${version}/gof-linux-x64`,
    "CLI .SRCINFO source URL",
  );

  if (cliSha256) {
    next = replaceOnce(
      next,
      /sha256sums_x86_64 = (?:[a-fA-F0-9]{64}|SKIP)/,
      `sha256sums_x86_64 = ${cliSha256.toLowerCase()}`,
      "CLI .SRCINFO checksum",
    );
  }

  return next;
});

console.log(`Bumped Raticode version to ${version}.`);
for (const update of updates) {
  console.log(`- ${update}`);
}

if (!appImageSha256) {
  console.log(
    "\nNote: Arch AppImage checksum was not changed. After building the release AppImage, run this script again with --appimage-sha256 <sha256> or update packaging/arch manually.",
  );
}

if (!cliSha256) {
  console.log(
    "\nNote: Arch CLI checksum was not changed. After building the release CLI binary, run this script again with --cli-sha256 <sha256> or update packaging/arch-cli manually.",
  );
}

function updateJsonFile(relativePath, transform) {
  const filePath = path.join(repoRoot, relativePath);
  const original = fs.readFileSync(filePath, "utf8");
  const json = JSON.parse(original);
  const next = `${JSON.stringify(transform(json), null, 2)}\n`;
  if (next !== original) {
    fs.writeFileSync(filePath, next);
    updates.push(relativePath);
  }
}

function updateTextFile(relativePath, transform) {
  const filePath = path.join(repoRoot, relativePath);
  const original = fs.readFileSync(filePath, "utf8");
  const next = transform(original);
  if (next !== original) {
    fs.writeFileSync(filePath, next);
    updates.push(relativePath);
  }
}

function replaceOnce(text, pattern, replacement, label) {
  const matches = text.match(pattern);
  if (!matches) {
    console.error(`Could not find ${label}.`);
    process.exit(1);
  }

  return text.replace(pattern, replacement);
}

function replaceAll(text, pattern, replacement, label) {
  if (!text.match(pattern)) {
    console.error(`Could not find ${label}.`);
    process.exit(1);
  }

  return text.replace(pattern, replacement);
}
