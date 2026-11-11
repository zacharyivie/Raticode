const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");

function usage(exitCode = 1) {
  const output = exitCode === 0 ? console.log : console.error;
  output(`Usage:
  node scripts/bump-version.cjs <version>

Example:
  node scripts/bump-version.cjs 0.1.1

The AUR packages in packaging/aur are not bumped here. They describe the newest
published release, and their checksums come from that release's checksums-linux.txt:
  python3 scripts/aur-packages.py update --version <version> --checksums checksums-linux.txt
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

for (const arg of args.slice(1)) {
  if (arg === "--appimage-sha256" || arg === "--cli-sha256") {
    console.error(
      `${arg} is no longer accepted: a checksum typed in before the release is built goes stale. ` +
        "After the release is published, run scripts/aur-packages.py update with its checksums-linux.txt.",
    );
  } else {
    console.error(`Unknown argument: ${arg}`);
  }
  usage();
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
    /^(name = "raticode"\nversion = ")[^"]+("$)/m,
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

console.log(`Bumped Raticode version to ${version}.`);
for (const update of updates) {
  console.log(`- ${update}`);
}
console.log(
  "\nThe AUR packages in packaging/aur still describe the last published release. " +
    "After this version is published, update them from its checksums-linux.txt (docs/releasing.md).",
);

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
