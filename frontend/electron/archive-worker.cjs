const { parentPort } = require("node:worker_threads");
const { archiveConversation, forgetConversations, purgeConversations, purgeExpiredConversations } = require("./conversation-archive.cjs");
function run(root, thread, messages, options) {
  if (options?.forget) return forgetConversations(root, messages);
  if (options?.purge) return purgeConversations(root, messages);
  if (options?.purgeExpired) return purgeExpiredConversations(root, options.days);
  return archiveConversation(root, thread, messages, options);
}
parentPort.on("message", ({ id, root, thread, messages, options }) => {
  try { parentPort.postMessage({ id, result: run(root, thread, messages, options) }); }
  catch (error) { parentPort.postMessage({ id, error: error.message }); }
});
