const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const { realpathForContainment, isPathInside } = require("./security.cjs");

function sameFile(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

async function readBounded(handle, stat, maxBytes) {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) throw new Error("A finite read size limit is required.");
  const tooLarge = () => Object.assign(new Error("File exceeds the read size limit."), { code: "ERR_FILE_TOO_LARGE" });
  if (stat.size > maxBytes) throw tooLarge();
  const chunks = [];
  let total = 0;
  while (total <= maxBytes) {
    const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, maxBytes + 1 - total));
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
    if (!bytesRead) break;
    total += bytesRead;
    if (total > maxBytes) throw tooLarge();
    chunks.push(buffer.subarray(0, bytesRead));
  }
  return Buffer.concat(chunks, total);
}

// Keep the parent descriptor open throughout each mutation. Linux's descriptor
// namespace makes child lookup relative to that directory even if it is renamed.
async function withDirectory(directory, authorize, operation) {
  const canonical = realpathForContainment(directory);
  authorize(canonical);
  const handle = await fsp.open(canonical, fs.constants.O_RDONLY | (fs.constants.O_DIRECTORY || 0) | (fs.constants.O_NOFOLLOW || 0));
  try {
    const stat = await handle.stat();
    if (!stat.isDirectory()) throw new Error("The destination is not a directory.");
    const verify = async () => {
      if (realpathForContainment(canonical) !== canonical || !sameFile(stat, await fsp.lstat(canonical))) {
        throw new Error("Directory changed during the file operation. Retry the action.");
      }
      authorize(canonical);
      if (process.platform === "linux") {
        const actual = await fsp.realpath(`/proc/self/fd/${handle.fd}`);
        authorize(actual);
        if (actual !== canonical) throw new Error("Directory moved during the file operation.");
      }
    };
    await verify();
    const anchored = process.platform === "linux" ? `/proc/self/fd/${handle.fd}` : canonical;
    return await operation(anchored, verify);
  } finally { await handle.close(); }
}

// Read only the already-authorized canonical path. Never resolve a replacement
// link into a new input, and enforce the limit on bytes read as well as stat size.
async function readFile(target, { maxBytes, authorize = () => {} }) {
  const canonical = path.resolve(target);
  const parentPath = path.dirname(canonical);
  const check = (candidate) => {
    if (candidate !== canonical && candidate !== parentPath) throw new Error("File path changed during read.");
    authorize(candidate);
  };
  check(realpathForContainment(canonical));
  return withDirectory(parentPath, check, async (parent, verify) => {
    const anchored = path.join(parent, path.basename(canonical));
    const handle = await fsp.open(anchored, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
    try {
      const stat = await handle.stat();
      const verifyFile = async () => {
        await verify();
        const entry = await fsp.lstat(anchored);
        if (!stat.isFile() || entry.isSymbolicLink() || !sameFile(stat, entry)) throw new Error("Cannot read a linked or replaced file.");
        if (process.platform === "linux") check(await fsp.realpath(`/proc/self/fd/${handle.fd}`));
      };
      await verifyFile();
      const content = await readBounded(handle, stat, maxBytes);
      await verifyFile();
      return content;
    } finally { await handle.close(); }
  });
}

async function writeFile(target, content, { authorize = () => {}, exclusive = false, expectedHash, digest, maxBytes } = {}) {
  const canonical = realpathForContainment(target);
  authorize(canonical);
  return withDirectory(path.dirname(canonical), authorize, async (parent, verify) => {
    const anchored = path.join(parent, path.basename(canonical));
    let handle;
    let created = false;
    try {
      // Never truncate before checking the descriptor. O_EXCL protects creation
      // against a link appearing after containment was checked.
      try {
        handle = await fsp.open(anchored, fs.constants.O_RDWR | fs.constants.O_NOFOLLOW | fs.constants.O_CREAT | fs.constants.O_EXCL, 0o666);
        created = true;
      } catch (error) {
        if (exclusive || error.code !== "EEXIST") throw error;
        handle = await fsp.open(anchored, fs.constants.O_RDWR | (fs.constants.O_NOFOLLOW || 0));
      }
      const stat = await handle.stat();
      const entry = await fsp.lstat(anchored);
      if (!stat.isFile() || stat.nlink !== 1 || entry.isSymbolicLink() || !sameFile(stat, entry)) {
        throw new Error("Cannot write a linked or replaced file.");
      }
      await verify();
      if (process.platform === "linux") authorize(await fsp.realpath(`/proc/self/fd/${handle.fd}`));
      if (expectedHash && digest(await readBounded(handle, stat, maxBytes)) !== expectedHash) throw new Error("File changed since the search. Refresh the results.");
      const bytes = Buffer.isBuffer(content) ? content : Buffer.from(content, "utf8");
      let offset = 0;
      while (offset < bytes.length) {
        const { bytesWritten } = await handle.write(bytes, offset, bytes.length - offset, offset);
        if (!bytesWritten) throw new Error("File write made no progress.");
        offset += bytesWritten;
      }
      await handle.truncate(bytes.length);
    } catch (error) {
      if (created && handle) {
        try {
          await verify();
          const owned = await handle.stat();
          const current = await fsp.lstat(anchored);
          if (!current.isSymbolicLink() && sameFile(owned, current)) await fsp.unlink(anchored);
        } catch { /* A changed path must not redirect cleanup to someone else's file. */ }
      }
      throw error;
    } finally { if (handle) await handle.close(); }
  });
}

async function createDirectory(target, authorize, { mode = 0o777 } = {}) {
  authorize(realpathForContainment(target));
  return withDirectory(path.dirname(target), authorize, async (parent, verify) => {
    await verify();
    await fsp.mkdir(path.join(parent, path.basename(target)), { mode });
  });
}

async function copyPath(source, destination, { authorizeSource, authorizeDestination, replace = false }) {
  if (replace) return replacePath(source, destination, { authorizeSource, authorizeDestination, move: false });
  const sourceRoot = realpathForContainment(source);
  const destinationRoot = realpathForContainment(destination);
  authorizeSource(sourceRoot);
  const authorizeCopyDestination = (target) => {
    // Check later directory resolutions too, in case an ancestor becomes a link.
    if (isPathInside(target, sourceRoot)) throw new Error("Cannot copy a directory into itself.");
    authorizeDestination(target);
  };
  authorizeCopyDestination(destinationRoot);
  async function copy(currentSource, currentDestination) {
    const stat = await fsp.lstat(currentSource);
    if (stat.isSymbolicLink()) {
      // Preserve internal links, but never install links that escape the grant.
      const link = await fsp.readlink(currentSource);
      const resolved = realpathForContainment(currentSource);
      authorizeSource(resolved);
      const newTarget = path.resolve(path.dirname(currentDestination), link);
      authorizeDestination(realpathForContainment(newTarget));
      await withDirectory(path.dirname(currentDestination), authorizeCopyDestination, async (parent, verify) => {
        await verify();
        await fsp.symlink(link, path.join(parent, path.basename(currentDestination)), process.platform === "win32" && (await fsp.stat(currentSource)).isDirectory() ? "junction" : undefined);
      });
    } else if (stat.isDirectory()) {
      await createDirectory(currentDestination, authorizeCopyDestination, { mode: stat.mode & 0o777 });
      await withDirectory(currentSource, authorizeSource, async (parent, verify) => {
        for (const entry of await fsp.readdir(parent)) {
          await verify();
          await copy(path.join(currentSource, entry), path.join(currentDestination, entry));
        }
      });
    } else if (stat.isFile()) {
      await withDirectory(path.dirname(currentSource), authorizeSource, async (parent, verify) => {
        // A regular file can become a FIFO after lstat. Do not block before the
        // descriptor identity/type check has a chance to reject its replacement.
        const handle = await fsp.open(path.join(parent, path.basename(currentSource)), fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
        try {
          await verify();
          const opened = await handle.stat();
          if (!opened.isFile() || !sameFile(stat, opened)) throw new Error("Source changed while copying.");
          // Stream large files using a bounded buffer; destination stays pinned.
          await withDirectory(path.dirname(currentDestination), authorizeCopyDestination, async (destinationParent, verifyDestination) => {
            const output = await fsp.open(path.join(destinationParent, path.basename(currentDestination)), fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW || 0), stat.mode & 0o777);
            try {
              await verifyDestination();
              const buffer = Buffer.allocUnsafe(64 * 1024);
              for (;;) {
                const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
                if (!bytesRead) break;
                let offset = 0;
                while (offset < bytesRead) offset += (await output.write(buffer, offset, bytesRead - offset)).bytesWritten;
              }
            } finally { await output.close(); }
          });
        } finally { await handle.close(); }
      });
    } else throw new Error("Only ordinary files and directories can be copied.");
  }
  await copy(sourceRoot, destinationRoot);
}

async function renamePath(source, destination, authorize) {
  return withDirectory(path.dirname(source), authorize, async (parent, verify) => {
    const sourceEntry = path.join(parent, path.basename(source));
    const destinationEntry = path.join(parent, path.basename(destination));
    const stat = await fsp.lstat(sourceEntry);
    if (stat.isSymbolicLink()) throw new Error("Cannot rename a symbolic link through the editor.");
    let destinationStat;
    try { destinationStat = await fsp.lstat(destinationEntry); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
    if (destinationStat) {
      // Only the same directory entry under different casing may already exist.
      // A hard link or distinct case-sensitive entry must never be overwritten.
      const names = await fsp.readdir(parent);
      const caseChange = path.basename(source) !== path.basename(destination)
        && path.basename(source).toLowerCase() === path.basename(destination).toLowerCase()
        && !names.includes(path.basename(destination));
      if (!caseChange || destinationStat.isSymbolicLink() || !sameFile(stat, destinationStat)) {
        throw new Error("Destination already exists.");
      }
    }
    await verify();
    await fsp.rename(sourceEntry, destinationEntry);
  });
}

async function movePath(source, destination, { authorizeSource, authorizeDestination, replace = false }) {
  if (replace) return replacePath(source, destination, { authorizeSource, authorizeDestination, move: true });
  const sourceRoot = realpathForContainment(source);
  const destinationRoot = realpathForContainment(destination);
  if (sourceRoot === destinationRoot) return;
  if (isPathInside(destinationRoot, sourceRoot)) throw new Error("Cannot move a directory into itself.");
  authorizeSource(sourceRoot);
  authorizeDestination(destinationRoot);
  return withDirectory(path.dirname(sourceRoot), authorizeSource, async (parent, verifySource) => {
    const entry = path.join(parent, path.basename(sourceRoot));
    const stat = await fsp.lstat(entry);
    if (stat.isSymbolicLink() || !(stat.isDirectory() || stat.isFile())) throw new Error("Only ordinary files and folders can be moved.");
    return withDirectory(path.dirname(destinationRoot), authorizeDestination, async (targetParent, verifyDestination) => {
      const target = path.join(targetParent, path.basename(destinationRoot));
      try { await fsp.lstat(target); throw new Error("Destination already exists."); }
      catch (error) { if (error.code !== "ENOENT") throw error; }
      await verifySource();
      await verifyDestination();
      if (!sameFile(stat, await fsp.lstat(entry))) throw new Error("Source changed while moving.");
      try { await fsp.rename(entry, target); }
      catch (error) {
        if (error.code !== "EXDEV") throw error;
        // Across volumes, retain the original until the entire copy succeeds.
        const beforeCopy = await treeVersion(sourceRoot, authorizeSource);
        await copyPath(sourceRoot, destinationRoot, { authorizeSource, authorizeDestination });
        await verifySource();
        await verifyDestination();
        if (!sameFile(stat, await fsp.lstat(entry)) || beforeCopy !== await treeVersion(sourceRoot, authorizeSource)) throw new Error("Source changed while moving. The original and copied destination were kept.");
        await fsp.rm(entry, { recursive: stat.isDirectory() });
      }
    });
  });
}

// Stage the incoming contents before moving the old destination aside. Keep a
// backup until installation succeeds, so a failed copy never destroys it.
async function replacePath(source, destination, { authorizeSource, authorizeDestination, move }) {
  const sourceRoot = realpathForContainment(source);
  const destinationRoot = path.join(realpathForContainment(path.dirname(destination)), path.basename(destination));
  authorizeSource(sourceRoot);
  authorizeDestination(destinationRoot);
  if (sourceRoot === destinationRoot) throw new Error("A file cannot replace itself.");
  if (isPathInside(destinationRoot, sourceRoot) || isPathInside(sourceRoot, destinationRoot)) throw new Error("Cannot replace a folder with itself or one of its descendants.");
  const sourceStat = await fsp.lstat(sourceRoot);
  if (!(sourceStat.isFile() || sourceStat.isDirectory()) || sourceStat.isSymbolicLink()) throw new Error("Only ordinary files and folders can replace a destination.");
  return withDirectory(path.dirname(destinationRoot), authorizeDestination, async (parent, verifyDestination) => {
    const target = path.join(parent, path.basename(destinationRoot));
    let original;
    try { original = await fsp.lstat(target); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
    if (original?.isSymbolicLink() || (original && !(original.isFile() || original.isDirectory()))) throw new Error("Cannot replace a linked or special file.");
    if (original && sameFile(sourceStat, original)) throw new Error("A file cannot replace itself.");
    await verifyDestination();
    const holder = await fsp.mkdtemp(path.join(parent, ".raticode-transfer-"));
    const holderName = path.basename(holder);
    // The staging path is a sibling of the final path, so relative links retain
    // the same depth and interpretation when a copied folder is installed.
    const stagingName = `${holderName}.incoming`;
    const staging = path.join(path.dirname(destinationRoot), stagingName);
    const stagedEntry = path.join(parent, stagingName);
    const backup = path.join(holder, "original");
    let backedUp = false, installed = false, preserveBackup = false;
    try {
      const version = await treeVersion(sourceRoot, authorizeSource);
      await copyPath(sourceRoot, staging, { authorizeSource, authorizeDestination });
      await verifyDestination();
      if (version !== await treeVersion(sourceRoot, authorizeSource)) throw new Error("Source changed while transferring. Retry the action.");
      await withDirectory(path.dirname(sourceRoot), authorizeSource, async (sourceParent, verifySource) => {
        await verifySource();
        const sourceEntry = path.join(sourceParent, path.basename(sourceRoot));
        if (!sameFile(sourceStat, await fsp.lstat(sourceEntry))) throw new Error("Source changed while transferring.");
        let current;
        try { current = await fsp.lstat(target); }
        catch (error) { if (error.code !== "ENOENT") throw error; }
        if (original ? !current || !sameFile(original, current) || original.mtimeMs !== current.mtimeMs || original.size !== current.size : current) throw new Error("Destination changed while transferring. Retry the action.");
        await verifyDestination();
        if (original) { await fsp.rename(target, backup); backedUp = true; }
        try { await fsp.rename(stagedEntry, target); installed = true; }
        catch (error) {
          if (backedUp) {
            try { await fsp.rename(backup, target); backedUp = false; }
            catch { preserveBackup = true; throw new Error(`Replacement failed. The original is saved in ${path.join(path.dirname(destinationRoot), holderName, "original")}.`); }
          }
          throw error;
        }
        if (move) {
          await verifySource();
          if (version !== await treeVersion(sourceRoot, authorizeSource)) throw new Error("Source changed while moving. The original and copied destination were kept.");
          await fsp.rm(sourceEntry, { recursive: sourceStat.isDirectory() });
        }
      });
    } finally {
      // Cleanup uses the pinned parent. A changed directory must not redirect it.
      await verifyDestination();
      if (!installed) await fsp.rm(stagedEntry, { recursive: true, force: true });
      if (!preserveBackup) await fsp.rm(holder, { recursive: true, force: true });
    }
  });
}

async function treeVersion(target, authorize) {
  authorize(target);
  const stat = await fsp.lstat(target, { bigint: true });
  const version = [target, String(stat.dev), String(stat.ino), String(stat.size), String(stat.mtimeNs), String(stat.ctimeNs)];
  if (stat.isDirectory()) {
    await withDirectory(target, authorize, async (parent, verify) => {
      for (const name of (await fsp.readdir(parent)).sort()) {
        await verify();
        version.push(await treeVersion(path.join(target, name), authorize));
      }
    });
  }
  return JSON.stringify(version);
}

module.exports = { copyPath, createDirectory, movePath, readFile, renamePath, withDirectory, writeFile };
