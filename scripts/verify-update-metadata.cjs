// Check electron-builder metadata against the final packaged bytes before staging.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const yaml = require('../frontend/node_modules/js-yaml');

function verifyMetadata(root, version, platform) {
  const metadata = { linux: 'latest-linux.yml', win32: 'latest.yml', darwin: 'latest-mac.yml' }[platform];
  const doc = yaml.load(fs.readFileSync(path.join(root, metadata), 'utf8'));
  if (doc.version !== version || !Array.isArray(doc.files) || !doc.files.length) {
    throw new Error(`Invalid updater version/file list in ${metadata}`);
  }
  function verify(name, hash, size) {
    const decoded = decodeURIComponent(name);
    if (!decoded || decoded !== path.basename(decoded) || /[\\/]/.test(decoded)) {
      throw new Error(`Updater target must be a local filename: ${name}`);
    }
    const bytes = fs.readFileSync(path.join(root, decoded));
    if (crypto.createHash('sha512').update(bytes).digest('base64') !== hash) {
      throw new Error(`Updater hash does not match final file: ${name}`);
    }
    if (size !== undefined && bytes.length !== size) throw new Error(`Wrong updater size: ${name}`);
    // A blockmap describes the file's chunks. Stapling or re-signing after it was built
    // changes the file's length, so its chunks no longer add up to the final size.
    const blockmap = path.join(root, `${decoded}.blockmap`);
    if (fs.existsSync(blockmap)) {
      const map = JSON.parse(zlib.gunzipSync(fs.readFileSync(blockmap)).toString('utf8'));
      const total = (map.files || []).reduce(
        (sum, entry) => sum + (entry.sizes || []).reduce((a, b) => a + b, 0), 0);
      if (total !== bytes.length) throw new Error(`Blockmap does not match final file: ${name}`);
    }
  }
  for (const file of doc.files) verify(file.url, file.sha512, file.size);
  if (doc.path || doc.sha512) verify(doc.path, doc.sha512);
  return `${metadata}: all updater hashes match final ${version} packages.`;
}

module.exports = { verifyMetadata };
if (require.main === module) {
  console.log(verifyMetadata(path.resolve('frontend/release'), require('../frontend/package.json').version, process.platform));
}
