// Stapling Apple's ticket to the DMG changes its bytes after electron-builder wrote the
// DMG's blockmap and its latest-mac.yml entry. Rebuild both from the final file with
// electron-builder's own blockmap code, so the updater metadata matches what is uploaded.
//
// Usage: node scripts/refresh-mac-update-metadata.cjs <release-folder> <file-name>
const fs = require('node:fs');
const path = require('node:path');

const modules = path.resolve(__dirname, '../frontend/node_modules');
const yaml = require(path.join(modules, 'js-yaml'));
const { buildBlockMap } = require(path.join(modules, 'app-builder-lib/out/targets/blockmap/blockmap'));

async function refreshUpdateMetadata(root, name) {
  if (!name || name !== path.basename(name)) throw new Error(`Expected a file name, got ${name}`);
  const file = path.join(root, name);
  const blockmap = `${file}.blockmap`;
  if (!fs.existsSync(blockmap)) throw new Error(`${name} has no blockmap to rebuild`);
  const metadataPath = path.join(root, 'latest-mac.yml');
  const metadata = yaml.load(fs.readFileSync(metadataPath, 'utf8'));
  const entries = (metadata.files || []).filter((entry) => decodeURIComponent(entry.url) === name);
  if (entries.length !== 1) throw new Error(`latest-mac.yml must list ${name} exactly once`);
  const info = await buildBlockMap(file, 'gzip', blockmap);
  entries[0].sha512 = info.sha512;
  entries[0].size = info.size;
  if (metadata.path && decodeURIComponent(metadata.path) === name) metadata.sha512 = info.sha512;
  // electron-builder writes this file with the same js-yaml settings.
  fs.writeFileSync(metadataPath, yaml.dump(metadata, { lineWidth: 8000, noRefs: true }));
  return `Rebuilt ${name}.blockmap and its latest-mac.yml entry (${info.size} bytes).`;
}

module.exports = { refreshUpdateMetadata };
if (require.main === module) {
  refreshUpdateMetadata(path.resolve(process.argv[2] || ''), process.argv[3])
    .then((message) => console.log(message))
    .catch((error) => {
      console.error(error.message);
      process.exit(1);
    });
}
