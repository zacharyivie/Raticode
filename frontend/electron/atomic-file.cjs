// Replaces a file in one step: readers see the old contents or the new ones, never half.
// The data goes to a new random name beside the file first. O_EXCL and O_NOFOLLOW mean a
// file or link someone left at that name is never opened or followed.
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const CREATE_NEW = fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW || 0);

// durable fsyncs the data before the rename, so a crash can't leave an empty file.
// syncDirectory also fsyncs the folder afterwards, so the rename itself survives a crash.
function writeFileAtomicSync(file, data, { mode = 0o600, durable = true, syncDirectory = false } = {}) {
  const temporary = `${file}.${crypto.randomUUID()}.tmp`;
  const descriptor = fs.openSync(temporary, CREATE_NEW, mode);
  let renamed = false;
  try {
    try {
      fs.writeFileSync(descriptor, data);
      if (durable) fs.fsyncSync(descriptor);
    } finally { fs.closeSync(descriptor); }
    fs.renameSync(temporary, file);
    renamed = true;
    if (syncDirectory && process.platform !== "win32") {
      const directory = fs.openSync(path.dirname(file), "r");
      try { fs.fsyncSync(directory); }
      finally { fs.closeSync(directory); }
    }
  } finally {
    // Only the temp file this call created is removed, never one that was already there.
    if (!renamed) {
      try { fs.unlinkSync(temporary); } catch { /* Already gone. */ }
    }
  }
}

module.exports = { writeFileAtomicSync };
