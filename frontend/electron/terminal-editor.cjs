const net = require("node:net");
const path = require("node:path");

const MAX_RESPONSE_BYTES = 16 * 1024;

function runClient({ argv = process.argv, env = process.env, stderr = process.stderr } = {}) {
  const endpoint = env.RATICODE_EDITOR_ENDPOINT;
  const token = env.RATICODE_EDITOR_TOKEN;
  const terminalId = env.RATICODE_TERMINAL_ID;
  const targetArg = editorTargetArg(argv.slice(2));
  if (!endpoint || !token || !terminalId || !targetArg) {
    stderr.write("Raticode could not open the Git editor.\n");
    return Promise.resolve(1);
  }

  const targetPath = path.resolve(env.RATICODE_EDITOR_CWD || process.cwd(), targetArg);
  return new Promise((resolve) => {
    const socket = net.createConnection(endpoint);
    let response = "";
    let settled = false;
    const finish = (exitCode, message = "") => {
      if (settled) return;
      settled = true;
      if (message) stderr.write(`${message}\n`);
      socket.destroy();
      resolve(exitCode);
    };
    socket.setEncoding("utf8");
    socket.on("connect", () => {
      socket.write(`${JSON.stringify({ targetPath, terminalId, token })}\n`);
    });
    socket.on("data", (chunk) => {
      response += chunk;
      if (response.length > MAX_RESPONSE_BYTES) {
        finish(1, "Raticode returned an invalid editor response.");
        return;
      }
      const newline = response.indexOf("\n");
      if (newline < 0) return;
      try {
        const payload = JSON.parse(response.slice(0, newline));
        finish(payload.ok === true ? 0 : 1, payload.ok === true ? "" : payload.error);
      } catch {
        finish(1, "Raticode returned an invalid editor response.");
      }
    });
    socket.on("error", (error) => finish(1, `Raticode could not open the Git editor: ${error.message}`));
    socket.on("end", () => finish(1, "Raticode closed the Git editor before it finished."));
  });
}

function editorTargetArg(args) {
  return [...args].reverse().find((value) => typeof value === "string" && value.trim()) || "";
}

if (require.main === module) {
  runClient().then((exitCode) => {
    process.exitCode = exitCode;
  });
}

module.exports = { editorTargetArg, runClient };
