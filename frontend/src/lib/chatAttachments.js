import { apiUrl } from "./api.js";

export const CHAT_ATTACHMENT_MAX_COUNT = 5;
export const CHAT_ATTACHMENT_MAX_FILE_BYTES = 20 * 1024 * 1024;
export const CHAT_ATTACHMENT_MAX_TOTAL_BYTES = 40 * 1024 * 1024;
export const CHAT_IMAGE_WARNING_MS = 4000;

export function providerSupportsChatImages(provider, capability, model) {
  const modelId = !model || model === "cli-default" ? capability?.defaultModel : model;
  const selectedModel = capability?.models?.find(item => item.id === modelId);
  if (selectedModel?.supportsImages === false) return false;
  return capability?.supportsImages ?? ["codex", "claude_code", "cursor", "copilot", "opencode", "grok"].includes(provider);
}

export function isChatImage(file) {
  return String(file?.type || "").startsWith("image/") || /\.(png|jpe?g|gif|webp|bmp|tiff?|svg|avif|heic)$/i.test(file?.name || "");
}

export function transferContainsFiles(dataTransfer) {
  return Array.from(dataTransfer?.types ?? []).includes("Files")
    || Boolean(dataTransfer?.files?.length);
}

export function clipboardAttachmentFiles(clipboardData) {
  const itemFiles = Array.from(clipboardData?.items ?? [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile?.())
    .filter(Boolean);
  return itemFiles.length ? itemFiles : Array.from(clipboardData?.files ?? []);
}

export function readChatAttachments(fileList, existingAttachments = []) {
  const files = [...(fileList ?? [])];
  const attachments = [...existingAttachments];
  const errors = [];
  let totalBytes = attachments.reduce((total, attachment) => total + attachment.size, 0);

  for (const file of files) {
    if (attachments.length >= CHAT_ATTACHMENT_MAX_COUNT) {
      errors.push(`You can attach up to ${CHAT_ATTACHMENT_MAX_COUNT} files.`);
      break;
    }
    if (file.size > CHAT_ATTACHMENT_MAX_FILE_BYTES) {
      errors.push(`${file.name} is larger than ${formatAttachmentBytes(CHAT_ATTACHMENT_MAX_FILE_BYTES)}.`);
      continue;
    }
    if (totalBytes + file.size > CHAT_ATTACHMENT_MAX_TOTAL_BYTES) {
      errors.push(`Attachments cannot exceed ${formatAttachmentBytes(CHAT_ATTACHMENT_MAX_TOTAL_BYTES)} in one message.`);
      break;
    }
    attachments.push({
      file,
      id: chatAttachmentId(file),
      name: String(file.name || "attachment"),
      size: file.size,
      type: String(file.type || "application/octet-stream"),
    });
    totalBytes += file.size;
  }
  return { attachments, error: [...new Set(errors)].join(" ") };
}

export async function encodeChatAttachments(attachments) {
  return Promise.all(attachments.map(async (attachment) => ({
    data: arrayBufferToBase64(await fileArrayBuffer(attachment.file)),
    name: attachment.name,
    type: attachment.type,
  })));
}

export async function uploadChatAttachments(attachments, threadId, fetchImpl = fetch) {
  const pending = attachments.filter((attachment) => attachment.file);
  if (!pending.length) return attachments;
  const files = await encodeChatAttachments(pending);
  const response = await fetchImpl(apiUrl("/chat/attachments"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files, threadId }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Attachment upload returned ${response.status}`);
  const uploaded = payload.attachments ?? [];
  let index = 0;
  return attachments.map(attachment => attachment.file ? uploaded[index++] : attachment).filter(Boolean);
}

export async function transcribeAudioBlob(blob, fetchImpl = fetch) {
  const response = await fetchImpl(apiUrl("/chat/transcribe"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      data: arrayBufferToBase64(await blob.arrayBuffer()),
      name: "recording.wav",
      type: "audio/wav",
    }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Transcription returned ${response.status}`);
  return String(payload.text || "").trim();
}

export async function startStreamingTranscription(fetchImpl = fetch) {
  const payload = await postTranscriptionRequest("/chat/transcribe/start", {}, fetchImpl);
  const sessionId = String(payload.sessionId || "");
  if (!sessionId) throw new Error("The local transcription session did not start.");
  return sessionId;
}

export async function streamTranscriptionChunk(sessionId, pcm, fetchImpl = fetch) {
  return postTranscriptionRequest("/chat/transcribe/chunk", {
    data: arrayBufferToBase64(pcm),
    sessionId,
  }, fetchImpl);
}

export async function finishStreamingTranscription(sessionId, fetchImpl = fetch) {
  return postTranscriptionRequest("/chat/transcribe/finish", { sessionId }, fetchImpl);
}

export async function cancelStreamingTranscription(sessionId, fetchImpl = fetch) {
  return postTranscriptionRequest("/chat/transcribe/cancel", { sessionId }, fetchImpl);
}

export function chatMessageForRequest(message) {
  const attachments = Array.isArray(message?.attachments) ? message.attachments : [];
  return {
    role: message.role,
    body: String(message?.body ?? "").trim(),
    ...(attachments.length ? {
      attachments: attachments.map(({ id, name, size, storageName, type }) => ({
        id, name, size, storageName, type,
      })),
    } : {}),
  };
}

export function formatAttachmentBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function appendTranscription(base, transcript) {
  return [String(base ?? "").trimEnd(), String(transcript ?? "").trim()]
    .filter(Boolean)
    .join(" ");
}

function arrayBufferToBase64(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

async function postTranscriptionRequest(path, body, fetchImpl) {
  const response = await fetchImpl(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Transcription returned ${response.status}`);
  return payload;
}

async function fileArrayBuffer(file) {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  if (typeof file.text === "function") return new TextEncoder().encode(await file.text()).buffer;
  throw new Error(`${file.name || "The selected file"} could not be read.`);
}

function chatAttachmentId(file) {
  const random = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
  return `attachment:${file.name}:${file.size}:${random}`;
}

// Keep large reference text out of the textarea, localStorage, and prompt body.
export const CHAT_PASTE_FILE_THRESHOLD = 16 * 1024;
export function largePasteFile(text) {
  if (!text || new TextEncoder().encode(text).byteLength < CHAT_PASTE_FILE_THRESHOLD) return null;
  return new File([text], `pasted-text-${Date.now()}.txt`, { type: "text/plain" });
}
