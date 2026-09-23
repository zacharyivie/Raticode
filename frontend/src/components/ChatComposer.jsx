import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { FileText, Loader2, Mic, Paperclip, Send, Square, X } from "lucide-react";

import {
  appendTranscription,
  cancelStreamingTranscription,
  finishStreamingTranscription,
  formatAttachmentBytes,
  startStreamingTranscription,
  streamTranscriptionChunk,
} from "../lib/chatAttachments.js";
import { PROVIDER_PERMISSIONS, defaultPermissionMode } from "../lib/providerPermissions.js";
import { audioInputConstraints } from "../lib/audioDevices.js";

export default function ChatComposer({
  attachments = [],
  attachmentError = "",
  audioInputDeviceId = "default",
  contextKey = "",
  draft,
  focusRequest = 0,
  onAddAttachments = () => {},
  onAttachmentErrorChange = () => {},
  onAttachmentsChange,
  onDraftChange,
  onSend,
  onStop,
  onSteer,
  steeringPending = false,
  sending = false,
  sendDisabled = false,
  provider = "codex",
  permissionMode = defaultPermissionMode(provider),
  permissionOptions = PROVIDER_PERMISSIONS[provider] || [["default", "CLI default"]],
  onPermissionModeChange = () => {},
}) {
  const fileInputRef = useRef(null);
  const recorderRef = useRef(null);
  const microphoneStreamRef = useRef(null);
  const textareaRef = useRef(null);
  const transcriptionBaseRef = useRef("");
  const [transcriptionError, setTranscriptionError] = useState("");
  const [transcribing, setTranscribing] = useState(false);
  const [transcriptionPending, setTranscriptionPending] = useState(false);

  useEffect(() => () => stopMicrophone(recorderRef, microphoneStreamRef), []);

  useEffect(() => {
    stopMicrophone(recorderRef, microphoneStreamRef);
    setTranscriptionError("");
    setTranscribing(false);
    setTranscriptionPending(false);
  }, [audioInputDeviceId, contextKey]);

  useEffect(() => {
    if (!transcribing || !textareaRef.current) return;
    textareaRef.current.scrollTop = textareaRef.current.scrollHeight;
  }, [draft, transcribing]);

  useLayoutEffect(() => {
    resizeChatComposerTextarea(textareaRef.current);
  }, [draft]);

  useEffect(() => {
    if (!focusRequest) return;
    textareaRef.current?.focus();
  }, [focusRequest]);

  function attachFiles(event) {
    const input = event.target;
    onAddAttachments(input.files);
    input.value = "";
  }

  async function toggleTranscription() {
    if (transcribing) {
      const recorder = recorderRef.current;
      recorderRef.current = null;
      setTranscribing(false);
      setTranscriptionPending(true);
      try {
        const transcript = await recorder.stop();
        onDraftChange(appendTranscription(transcriptionBaseRef.current, transcript));
      } catch (error) {
        setTranscriptionError(
          error instanceof Error ? error.message : "The recording could not be transcribed.",
        );
      } finally {
        microphoneStreamRef.current = null;
        setTranscriptionPending(false);
      }
      return;
    }
    const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    if (!navigator.mediaDevices?.getUserMedia || !AudioContextConstructor) {
      setTranscriptionError("Audio recording is not available in this desktop build.");
      return;
    }
    setTranscriptionPending(true);
    let sessionId = "";
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(
        audioInputConstraints(audioInputDeviceId),
      );
      sessionId = await startStreamingTranscription();
      transcriptionBaseRef.current = draft;
      const transcriber = createStreamingTranscriber(
        sessionId,
        (text) => {
          onDraftChange(appendTranscription(transcriptionBaseRef.current, text));
        },
        (error) => {
          const activeRecorder = recorderRef.current;
          if (activeRecorder) {
            recorderRef.current = null;
            activeRecorder.cancel();
            microphoneStreamRef.current = null;
            setTranscribing(false);
            setTranscriptionPending(false);
          }
          setTranscriptionError(
            error instanceof Error ? error.message : "Live transcription stopped.",
          );
        },
      );
      const localRecorder = await createLocalPcmRecorder(
        stream,
        AudioContextConstructor,
        (pcm) => transcriber.push(pcm),
      );
      const recorder = {
        async stop() {
          try {
            await localRecorder.stop();
            return await transcriber.finish();
          } catch (error) {
            transcriber.cancel();
            throw error;
          }
        },
        cancel() {
          localRecorder.cancel();
          transcriber.cancel();
        },
      };
      microphoneStreamRef.current = stream;
      recorderRef.current = recorder;
      setTranscriptionError("");
      setTranscribing(true);
    } catch (error) {
      stream?.getTracks?.().forEach((track) => track.stop());
      if (sessionId) void cancelStreamingTranscription(sessionId).catch(() => {});
      stopMicrophone(recorderRef, microphoneStreamRef);
      setTranscribing(false);
      setTranscriptionError(
        microphoneErrorMessage(error),
      );
    } finally {
      setTranscriptionPending(false);
    }
  }

  const error = attachmentError || transcriptionError;
  return (
    <>
      <div
        data-chat-composer
        className="overflow-hidden rounded-[14px] border border-line bg-white transition focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-500/10"
      >
        {attachments.length ? (
          <div className="flex flex-wrap gap-1.5 px-2.5 pt-2.5">
            {attachments.map((attachment) => (
              <AttachmentChip
                key={attachment.id}
                attachment={attachment}
                onRemove={() => {
                  onAttachmentsChange(attachments.filter((item) => item.id !== attachment.id));
                  onAttachmentErrorChange("");
                }}
              />
            ))}
          </div>
        ) : null}
        <textarea
          ref={textareaRef}
          aria-describedby={error ? "chat-composer-error" : undefined}
          className="block min-h-14 max-h-32 w-full resize-none bg-transparent px-3 pb-1 pt-3 text-sm leading-5 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed disabled:text-muted"
          readOnly={transcribing || transcriptionPending}
          placeholder={sending ? "Steer Rem while it works" : attachments.length ? "Add a note about the attached file" : "Message this workflow"}
          rows={2}
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent?.isComposing && !event.isComposing && !transcribing && !transcriptionPending) {
              event.preventDefault();
              if (sending) {
                if ((draft.trim() || attachments.length) && !steeringPending) onSteer?.();
              } else if (!sendDisabled && (draft.trim() || attachments.length)) onSend(null, { background: event.ctrlKey });
            }
          }}
        />
        <div className="flex items-center justify-between px-1.5 pb-1.5 pt-0.5">
          <div className="flex min-w-0 flex-1 items-center gap-0.5">
            <button
              aria-label={
                transcriptionPending
                  ? "Preparing local transcription"
                  : transcribing
                    ? "Stop transcription"
                    : "Transcribe message locally"
              }
              aria-pressed={transcribing}
              className={`grid h-8 w-8 place-items-center rounded-lg transition disabled:cursor-not-allowed disabled:opacity-50 ${
                transcribing
                  ? "bg-red-50 text-red-600 hover:bg-red-100"
                  : "text-muted hover:bg-slate-100 hover:text-ink"
              }`}
              disabled={sending || transcriptionPending}
              title={
                transcriptionPending
                  ? "Transcribing locally"
                  : transcribing
                    ? "Stop transcription"
                    : "Transcribe"
              }
              type="button"
              onClick={toggleTranscription}
            >
              {transcriptionPending ? (
                <Loader2 aria-hidden="true" className="animate-spin" size={16} />
              ) : (
                <Mic aria-hidden="true" className={transcribing ? "animate-pulse" : ""} size={16} />
              )}
            </button>
            <button
              aria-label="Attach files"
              className="grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
              disabled={steeringPending || transcribing || transcriptionPending}
              title="Attach files"
              type="button"
              onClick={() => fileInputRef.current?.click()}
            >
              <Paperclip aria-hidden="true" size={16} />
            </button>
            {permissionOptions.length ? (
              <select
                aria-label="Rem permissions"
                className="ml-1 h-8 min-w-0 max-w-40 rounded-md border border-line bg-transparent px-1.5 text-xs text-ink outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-brand disabled:cursor-not-allowed disabled:opacity-50"
                disabled={sending}
                value={permissionMode}
                title={permissionOptions.find(([id]) => id === permissionMode)?.[2]}
                onChange={(event) => onPermissionModeChange(event.target.value)}
              >
                {permissionOptions.map(([id, label]) => (
                  <option key={id} value={id}>{label}</option>
                ))}
              </select>
            ) : null}
            <input
              ref={fileInputRef}
              className="sr-only"
              multiple
              tabIndex={-1}
              type="file"
              onChange={attachFiles}
            />
          </div>
          {sending ? <button
            aria-label="Steer Rem"
            className="mr-2 rounded-lg bg-brand px-3 py-2 text-xs text-white disabled:opacity-50"
            disabled={(!draft.trim() && !attachments.length) || steeringPending || transcribing || transcriptionPending}
            type="button"
            onClick={onSteer}
          >{steeringPending ? "Submitting…" : "Steer"}</button> : null}
          <button
            aria-label={sending ? "Stop Rem" : "Send message"}
            className={`grid h-9 w-9 place-items-center rounded-[10px] transition disabled:cursor-not-allowed disabled:opacity-60 ${
              sending
                ? "border border-line bg-white text-red-600 hover:border-red-200 hover:bg-red-50"
                : "bg-brand text-white hover:bg-indigo-700"
            }`}
            disabled={
              (!sending && (sendDisabled || (!draft.trim() && !attachments.length)))
              || transcribing
              || transcriptionPending
            }
            title={sending ? "Stop Rem" : "Send message"}
            type="button"
            onClick={sending ? onStop : onSend}
          >
            {sending ? (
              <Square aria-hidden="true" fill="currentColor" size={13} strokeWidth={1.7} />
            ) : (
              <Send aria-hidden="true" size={15} />
            )}
          </button>
        </div>
      </div>
      {error ? <p id="chat-composer-error" className="mt-1.5 px-1 text-[10px] text-red-600">{error}</p> : null}
      <p className="mt-1.5 px-1 text-[10px] text-muted">{sending ? "Enter to steer · Stop cancels the response" : "Enter to send · Shift+Enter for a new line"}</p>
    </>
  );
}

export function resizeChatComposerTextarea(textarea) {
  if (!textarea) return;
  textarea.style.height = "auto";
  const contentHeight = Number(textarea.scrollHeight);
  if (!Number.isFinite(contentHeight)) return;
  const height = Math.min(Math.max(contentHeight, 56), 128);
  textarea.style.height = `${height}px`;
  textarea.style.overflowY = contentHeight > 128 ? "auto" : "hidden";
}

export function MessageAttachments({ attachments = [], inverse = false }) {
  if (!attachments.length) return null;
  return (
    <div className={`flex flex-wrap gap-1.5 ${inverse ? "mb-2" : "mt-2"}`}>
      {attachments.map((attachment) => (
        <span
          key={attachment.id ?? attachment.name}
          className={`inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] ${
            inverse
              ? "border-white/25 bg-white/10 text-white"
              : "border-line bg-slate-50 text-muted"
          }`}
          title={`${attachment.name} · ${formatAttachmentBytes(attachment.size)}`}
        >
          <FileText aria-hidden="true" className="shrink-0" size={12} />
          <span className="truncate">{attachment.name}</span>
          <span className={inverse ? "text-white/70" : "text-muted"}>{formatAttachmentBytes(attachment.size)}</span>
        </span>
      ))}
    </div>
  );
}

function AttachmentChip({ attachment, onRemove }) {
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-md border border-line bg-slate-50 py-1 pl-2 pr-1 text-[10px] text-muted">
      <FileText aria-hidden="true" className="shrink-0" size={12} />
      <span className="max-w-40 truncate text-ink">{attachment.name}</span>
      <span className="shrink-0">{formatAttachmentBytes(attachment.size)}</span>
      <button
        aria-label={`Remove ${attachment.name}`}
        className="grid h-5 w-5 shrink-0 place-items-center rounded text-muted transition hover:bg-slate-200 hover:text-ink"
        title={`Remove ${attachment.name}`}
        type="button"
        onClick={onRemove}
      >
        <X aria-hidden="true" size={11} />
      </button>
    </span>
  );
}

function microphoneErrorMessage(error) {
  if (error?.name === "NotAllowedError" || error?.name === "SecurityError") {
    return "Microphone access was denied. Allow microphone access to transcribe a message. On Mac, enable Raticode in System Settings > Privacy & Security > Microphone, then restart Raticode.";
  }
  if (error?.name === "NotFoundError") return "No microphone is available.";
  if (error?.name === "OverconstrainedError") {
    return "The selected microphone is unavailable. Choose another input in Settings > Devices.";
  }
  return error instanceof Error ? error.message : "Audio recording could not start.";
}

function stopMicrophone(recorderRef, streamRef) {
  recorderRef.current?.cancel?.();
  streamRef.current?.getTracks?.().forEach((track) => track.stop());
  recorderRef.current = null;
  streamRef.current = null;
}

function createStreamingTranscriber(sessionId, onText, onError) {
  let closed = false;
  let failure = null;
  let queue = Promise.resolve();

  return {
    push(pcm) {
      if (closed || failure) return;
      queue = queue.then(async () => {
        try {
          const payload = await streamTranscriptionChunk(sessionId, pcm);
          onText(String(payload.text || ""));
        } catch (error) {
          failure = error;
          onError(error);
        }
      });
    },
    async finish() {
      closed = true;
      await queue;
      if (failure) {
        await cancelStreamingTranscription(sessionId).catch(() => {});
        throw failure;
      }
      const payload = await finishStreamingTranscription(sessionId);
      const text = String(payload.text || "");
      onText(text);
      return text;
    },
    cancel() {
      closed = true;
      void cancelStreamingTranscription(sessionId).catch(() => {});
    },
  };
}

async function createLocalPcmRecorder(stream, AudioContextConstructor, onPcmChunk) {
  const context = new AudioContextConstructor();
  await context.resume?.();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const sink = context.createGain();
  let chunks = [];
  let bufferedSamples = 0;
  let recordedSamples = 0;
  let peakLevel = 0;
  const streamChunkSamples = Math.max(1, Math.floor(context.sampleRate / 4));
  sink.gain.value = 0;

  function flushChunks() {
    if (!bufferedSamples) return;
    onPcmChunk(pcmChunksToPcm16(chunks, context.sampleRate));
    chunks = [];
    bufferedSamples = 0;
  }

  processor.onaudioprocess = (event) => {
    const chunk = new Float32Array(event.inputBuffer.getChannelData(0));
    chunks.push(chunk);
    bufferedSamples += chunk.length;
    recordedSamples += chunk.length;
    for (const sample of chunk) peakLevel = Math.max(peakLevel, Math.abs(sample));
    if (bufferedSamples >= streamChunkSamples) flushChunks();
  };
  source.connect(processor);
  processor.connect(sink);
  sink.connect(context.destination);

  let closed = false;
  async function close() {
    if (closed) return;
    closed = true;
    processor.onaudioprocess = null;
    source.disconnect();
    processor.disconnect();
    sink.disconnect();
    stream.getTracks().forEach((track) => track.stop());
    await context.close();
  }

  return {
    state: "recording",
    async stop() {
      await close();
      if (!recordedSamples) {
        throw new Error("No audio was recorded. Check the selected microphone and try again.");
      }
      if (peakLevel < 0.003) {
        throw new Error("No microphone signal was recorded. Test the input in Settings > Devices.");
      }
      flushChunks();
    },
    cancel() {
      void close();
    },
  };
}

function pcmChunksToPcm16(chunks, inputSampleRate) {
  const inputLength = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const input = new Float32Array(inputLength);
  let inputOffset = 0;
  for (const chunk of chunks) {
    input.set(chunk, inputOffset);
    inputOffset += chunk.length;
  }
  const outputSampleRate = 16_000;
  const ratio = inputSampleRate / outputSampleRate;
  const outputLength = Math.max(1, Math.floor(input.length / ratio));
  const pcm = new ArrayBuffer(outputLength * 2);
  const view = new DataView(pcm);
  for (let index = 0; index < outputLength; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.max(start + 1, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let sample = start; sample < end && sample < input.length; sample += 1) {
      sum += input[sample];
    }
    const value = Math.max(-1, Math.min(1, sum / (end - start)));
    view.setInt16(index * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
  }
  return pcm;
}
