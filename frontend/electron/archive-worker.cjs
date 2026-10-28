const { parentPort } = require("node:worker_threads");
const { archiveConversation, forgetConversations } = require("./conversation-archive.cjs");
parentPort.on("message", ({ id, root, thread, messages, options }) => {
  try { parentPort.postMessage({ id, result: options?.forget ? forgetConversations(root, messages) : archiveConversation(root, thread, messages, options) }); }
  catch (error) { parentPort.postMessage({ id, error: error.message }); }
});
