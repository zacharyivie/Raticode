// Electron rejects a failed ipcRenderer.invoke with "Error invoking remote
// method '<channel>': Error: <message>". People only need <message>.
const REMOTE_METHOD_PREFIX = /^Error invoking remote method '[^']+': (?:Error: )?/;

export function ipcErrorMessage(cause, fallback = "That didn't work.") {
  const message = cause instanceof Error ? cause.message : cause?.message || cause;
  return String(message || fallback).replace(REMOTE_METHOD_PREFIX, "");
}
