const fs = require("node:fs");
const path = require("node:path");

function migrateSource(source, authorize = (value) => value) {
  if (path.extname(source).toLowerCase() !== ".rad") return source;
  const target = source.slice(0, -4) + ".rattish";
  let stat;
  try { stat = fs.lstatSync(source); } catch (error) {
    if (error.code !== "ENOENT") throw error;
    return fs.existsSync(target) ? authorize(target) : source;
  }
  if (!stat.isFile() || stat.isSymbolicLink()) return source;
  authorize(source);
  authorize(target);
  try { fs.linkSync(source, target); } catch (error) {
    if (error.code === "ENOENT" && !fs.existsSync(source) && fs.existsSync(target)) return target;
    if (error.code !== "EEXIST") throw error;
    if (!fs.existsSync(source)) return target;
    const destination = fs.lstatSync(target);
    if (destination.isSymbolicLink() || stat.ino !== destination.ino || stat.dev !== destination.dev) {
      throw new Error(`Cannot migrate ${source}: ${target} already exists. Keep or rename one of the files.`);
    }
  }
  try { fs.unlinkSync(source); } catch (error) { if (error.code !== "ENOENT") throw error; }
  return target;
}
module.exports = { migrateSource };
