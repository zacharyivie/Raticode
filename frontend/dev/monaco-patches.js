import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { URL, fileURLToPath } from "node:url";

// Two fixes to how Monaco 0.57's files are bundled, used by the app build, the dev server's
// dependency pre-bundling and the editor benchmark.
//
// 1. Monaco bundles its own copy of DOMPurify 3.4.15 (the npm build, byte for byte), and that
//    release has published advisories. Monaco's import is pointed at the npm `dompurify`
//    package instead, which package.json overrides to a fixed release.
// 2. The CSS, HTML, JSON and TypeScript language features import createWebWorker from
//    internal/common/workers.js, which also imports nearly every editor feature. Loading it
//    when a language starts adds about 480 kB and registers features after editors exist,
//    so new editors fail with "depends on UNKNOWN service". Those imports get
//    src/lib/monacoWebWorker.js, the same helper without the extra features.
const MONACO_FILE = /[\\/]node_modules[\\/]monaco-editor[\\/]/;
const DOMPURIFY_IMPORT = /(?:^|\/)dompurify\/dompurify\.js$/;
const WORKERS_IMPORT = /(?:^|\/)internal\/common\/workers\.js$/;
const WORKER_MANAGER = /[\\/]languages[\\/]features[\\/][a-z]+[\\/]workerManager\.js$/;
const WEB_WORKER_HELPER = fileURLToPath(new URL("../src/lib/monacoWebWorker.js", import.meta.url));
const require = createRequire(import.meta.url);

function versionParts(version) {
  return version.split(".").map(part => Number.parseInt(part, 10));
}

function checkDompurifyVersion() {
  const vendoredPath = fileURLToPath(new URL("../node_modules/monaco-editor/esm/vs/base/browser/dompurify/dompurify.js", import.meta.url));
  const vendored = /DOMPurify (\d+\.\d+\.\d+)/.exec(readFileSync(vendoredPath, "utf8").slice(0, 300))?.[1];
  // dompurify's exports map hides package.json, so read it from the module's own folder.
  const installed = JSON.parse(readFileSync(join(dirname(require.resolve("dompurify")), "..", "package.json"), "utf8")).version;
  if (!vendored) throw new Error(`Cannot read the DOMPurify version Monaco bundles in ${vendoredPath}`);
  const [want, have] = [versionParts(vendored), versionParts(installed)];
  const older = have[1] < want[1] || (have[1] === want[1] && have[2] < want[2]);
  if (have[0] !== want[0] || older) {
    throw new Error(`Monaco bundles DOMPurify ${vendored} but dompurify ${installed} is installed; update the dompurify override in package.json`);
  }
}

export function monacoPatches() {
  return {
    name: "monaco-patches",
    // Run before Vite resolves Monaco's relative imports to its own files.
    enforce: "pre",
    buildStart() {
      checkDompurifyVersion();
    },
    resolveId(source, importer) {
      if (!importer || !MONACO_FILE.test(importer)) return null;
      if (DOMPURIFY_IMPORT.test(source)) return this.resolve("dompurify", importer, { skipSelf: true });
      if (WORKERS_IMPORT.test(source) && WORKER_MANAGER.test(importer)) return WEB_WORKER_HELPER;
      return null;
    },
  };
}
