import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Loader2, Mic, Paperclip, Send, Square, X } from "lucide-react";

import { AttachmentTile } from "./AttachmentTiles.jsx";
import VoiceDots from "./VoiceDots.jsx";

import {
  appendTranscription,
  cancelStreamingTranscription,
  finishStreamingTranscription,
  startStreamingTranscription,
  streamTranscriptionChunk,
} from "../lib/chatAttachments.js";
import { PROVIDER_PERMISSIONS, defaultPermissionMode } from "../lib/providerPermissions.js";
import { audioInputConstraints, createSilenceWatch, silentInputMessage } from "../lib/audioDevices.js";
import { loadVoiceTidy, tidyTranscript } from "../lib/voiceTidy.js";
import { DictationText, QUIET_MS } from "../lib/dictation.js";
import { ProgressiveTidy, TIDY_PAUSE_MS } from "../lib/progressiveTidy.js";
import { DotMood, levelFromSamples } from "../lib/voiceDots.js";
import { ariaKeyshortcuts, registerVoiceComposer, useDictationAutoSend, useDictationMode, useVoiceBinding, voiceShortcutLabel } from "../lib/voiceShortcut.js";

const menuClass = "ml-1 h-8 min-w-0 max-w-40 rounded-md border border-line bg-transparent px-1.5 text-xs text-ink outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-brand disabled:cursor-not-allowed disabled:opacity-50";

export default function ChatComposer({
  inputLabel,
  placeholder,
  sendLabel = "Send message",
  attachments = [],
  attachmentError = "",
  attachmentWarning = "",
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
  shortcutHint = "Enter to send · Shift+Enter for a new line",
  provider = "codex",
  permissionMode = defaultPermissionMode(provider),
  permissionOptions = PROVIDER_PERMISSIONS[provider] || [["default", "CLI default"]],
  onPermissionModeChange = () => {},
  machine = "",
  machines = [],
  machineLocked = false,
  onMachineChange = () => {},
}) {
  const errorId = useId();
  const fileInputRef = useRef(null);
  const textareaRef = useRef(null);
  // The dictation in progress: the words already in the box, the words heard, and the
  // open recording session. In Press to start and stop, listening runs until the mic is
  // pressed again (or Enter); tapping the dotted line pauses and resumes without losing
  // words. In Hold to talk it runs while the mic or shortcut is held. Stopping either way
  // goes straight on to the words.
  const dictationRef = useRef(null);
  const levelRef = useRef(0);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const [voice, setVoice] = useState(null);
  const [transcriptionError, setTranscriptionError] = useState("");
  const [voiceNotice, setVoiceNotice] = useState("");

  const voicePhase = voice?.phase ?? "";
  const dictating = Boolean(voice);
  const listening = voicePhase === "listening";
  const dictationMode = useDictationMode();
  const dictationAutoSend = useDictationAutoSend();
  // With Send after dictation on: the words a finished dictation put in the box, waiting
  // for the box to show them so the send uses them. Anything else changing the box first
  // (typing, another chat) cancels it, and the words simply wait to be sent by hand.
  const autoSendRef = useRef(null);
  // This dictation listens while held: hold mode, unless it was started from the menu.
  const held = Boolean(voice?.held);

  function endDictation(dictation) {
    if (dictationRef.current !== dictation) return;
    dictation.session?.cancel();
    dictation.session = null;
    dictation.abort?.abort();
    window.clearTimeout(dictation.pauseTimer);
    window.clearTimeout(dictation.gapTimer);
    dictation.round?.abort.abort();
    dictationRef.current = null;
    levelRef.current = 0;
    setVoice(null);
  }

  useEffect(() => () => {
    const dictation = dictationRef.current;
    dictation?.session?.cancel();
    dictation?.abort?.abort();
    dictationRef.current = null;
  }, []);

  useEffect(() => {
    const dictation = dictationRef.current;
    if (dictation) endDictation(dictation);
    autoSendRef.current = null;
    setTranscriptionError("");
    setVoiceNotice("");
  }, [audioInputDeviceId, contextKey]);

  // A long silence stops listening, so the microphone isn't left on, and goes on to the
  // words as stopping does. The words stay.
  useEffect(() => {
    if (!listening) return undefined;
    const timer = window.setInterval(() => {
      const dictation = dictationRef.current;
      if (dictation?.session && Date.now() - dictation.lastWordsAt >= QUIET_MS) stopRef.current();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [listening]);

  // Holding the mic: switching away from the window counts as letting go.
  const releaseRef = useRef(() => {});
  const stopRef = useRef(() => {});
  useEffect(() => {
    if (!held) return undefined;
    const release = () => releaseRef.current();
    window.addEventListener("blur", release);
    return () => window.removeEventListener("blur", release);
  }, [held]);

  useEffect(() => {
    const pending = autoSendRef.current;
    if (!pending) return;
    if (draft === pending.text) {
      autoSendRef.current = null;
      if (!sending && !sendDisabled && draft.trim()) onSend();
    } else if (draft !== pending.before) {
      autoSendRef.current = null;
    }
  }, [draft, sending, sendDisabled, onSend]);

  useEffect(() => {
    if (!dictating || !textareaRef.current) return;
    textareaRef.current.scrollTop = textareaRef.current.scrollHeight;
  }, [draft, dictating]);

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

  // With tidying on, heard words stay out of the box until they are tidied; words that
  // were already there still show.
  function showWords(dictation) {
    if (dictationRef.current !== dictation || dictation.tidyOn) return;
    onDraftChange(appendTranscription(dictation.base, dictation.words.text));
  }

  // Recording or transcription can't go on: keep what was heard, as heard, with the reason.
  function dictationFailed(dictation, error) {
    if (dictationRef.current !== dictation) return;
    dictation.words.interrupted();
    const heard = dictation.words.text;
    const message = error instanceof Error ? error.message : "Live transcription stopped.";
    endDictation(dictation);
    if (heard) {
      onDraftChange(appendTranscription(dictation.base, heard));
      setVoiceNotice(`${message} Your words so far are kept${dictation.tidyOn ? ", untidied" : ""}.`);
    } else {
      setTranscriptionError(message);
    }
  }

  async function openSession(dictation) {
    const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    if (!navigator.mediaDevices?.getUserMedia || !AudioContextConstructor) {
      throw new Error("Audio recording is not available in this desktop build.");
    }
    const stream = await navigator.mediaDevices.getUserMedia(audioInputConstraints(audioInputDeviceId));
    let started;
    try {
      started = await startStreamingTranscription();
    } catch (error) {
      stream.getTracks?.().forEach((track) => track.stop());
      throw error;
    }
    if (started.notice) setVoiceNotice(started.notice);
    const transcriber = createStreamingTranscriber(
      started.sessionId,
      (text) => {
        if (dictationRef.current !== dictation || dictation.session?.transcriber !== transcriber) return;
        // Only new words count as speech: the recognizer answers every chunk, with the same
        // words during a pause, and those answers mustn't keep postponing the pause round.
        const newWords = Boolean(text.trim()) && text.trim() !== dictation.words.current;
        if (newWords) dictation.lastWordsAt = Date.now();
        dictation.words.partial(text);
        showWords(dictation);
        if (dictation.tidyOn && newWords) heardMore(dictation);
      },
      (error) => {
        if (dictation.session?.transcriber === transcriber) dictationFailed(dictation, error);
      },
      (notice) => setVoiceNotice(notice),
    );
    // A muted or switched-off microphone sends only zeros, so the line stays flat.
    // Say so while listening, naming the device, and keep listening for it to come back.
    const inputLabel = stream.getAudioTracks?.()[0]?.label || "";
    const noSoundNotice = silentInputMessage(inputLabel);
    dictation.inputLabel = inputLabel;
    let recorder;
    try {
      recorder = await createLocalPcmRecorder(
        stream,
        AudioContextConstructor,
        (pcm) => transcriber.push(pcm),
        (level) => { levelRef.current = level; },
        (signal) => {
          if (dictationRef.current !== dictation) return;
          if (signal === "silent") setVoiceNotice(noSoundNotice);
          else setVoiceNotice((current) => (current === noSoundNotice ? "" : current));
        },
      );
    } catch (error) {
      stream.getTracks?.().forEach((track) => track.stop());
      transcriber.cancel();
      throw error;
    }
    return {
      transcriber,
      // Resolves to the session's words; a session that heard nothing gives "".
      async stop() {
        const { recorded, signal } = await recorder.stop();
        dictation.noSignal = !signal;
        if (!recorded) {
          transcriber.cancel();
          dictation.silent = true;
          return "";
        }
        try {
          return await transcriber.finish();
        } catch (error) {
          if (NO_SPEECH.test(error?.message || "")) return "";
          throw error;
        }
      },
      cancel() {
        recorder.cancel();
        transcriber.cancel();
      },
    };
  }

  // Ends the open session and keeps its words. Used by pause, Done and the quiet timer.
  function closeSession(dictation) {
    const session = dictation.session;
    if (!session) return dictation.closing ?? Promise.resolve();
    dictation.session = null;
    levelRef.current = 0;
    const closing = (async () => {
      try {
        dictation.words.result(await session.stop());
        showWords(dictation);
      } catch (error) {
        session.cancel();
        dictationFailed(dictation, error);
      }
    })();
    dictation.closing = closing.finally(() => {
      if (dictation.closing === closing) dictation.closing = null;
    });
    return closing;
  }

  async function startDictation({ hold = false } = {}) {
    if (dictationRef.current) return;
    const dictation = {
      base: draftRef.current,
      words: new DictationText(),
      tidyOn: false,
      session: null,
      closing: null,
      abort: null,
      lastWordsAt: Date.now(),
      silent: false,
      hold,
      pressedAt: Date.now(),
      starting: true,
      stopRequested: false,
      // Tidy rounds while listening, with tidying on: what came back, and the request out.
      progress: new ProgressiveTidy(Date.now()),
      round: null,
      pauseTimer: null,
      gapTimer: null,
    };
    dictationRef.current = dictation;
    setTranscriptionError("");
    setVoiceNotice("");
    setVoice({ phase: "starting", held: hold });
    // Keyboard control follows the dictation: Space, Enter and Esc work from here. A held
    // mic or key keeps focus, so letting go of it is seen.
    if (!hold) textareaRef.current?.focus();
    // A failed check leaves tidying off, so the words show as they are heard.
    dictation.tidyOn = await loadVoiceTidy({ resolve: false }).then((payload) => payload.enabled === true, () => false);
    try {
      const session = await openSession(dictation);
      if (dictationRef.current !== dictation) {
        session.cancel();
        return;
      }
      dictation.session = session;
      dictation.starting = false;
      dictation.lastWordsAt = Date.now();
      setVoice({ phase: "listening", held: hold });
      // Stopped while the microphone was opening: go straight on to the words.
      if (dictation.stopRequested) void finishDictation();
    } catch (error) {
      if (dictationRef.current !== dictation) return;
      endDictation(dictation);
      setTranscriptionError(microphoneErrorMessage(error));
    }
  }

  async function pauseListening() {
    const dictation = dictationRef.current;
    if (!dictation?.session) return;
    setVoice({ phase: "paused", held: dictation.hold });
    await closeSession(dictation);
    // A pause is a good moment to tidy what was said so far.
    if (dictation.tidyOn) tidyRound(dictation);
  }

  async function resumeListening() {
    const dictation = dictationRef.current;
    if (!dictation || dictation.session || dictation.opening) return;
    dictation.opening = true;
    dictation.starting = true;
    setVoice({ phase: "starting", held: dictation.hold });
    try {
      await dictation.closing;
      if (dictationRef.current !== dictation) return;
      const session = await openSession(dictation);
      if (dictationRef.current !== dictation || dictation.finishing) {
        session.cancel();
        return;
      }
      if (dictation.stopRequested) {
        // Stopped while the microphone reopened: keep the earlier words and go on.
        session.cancel();
        dictation.starting = false;
        void finishDictation();
        return;
      }
      dictation.session = session;
      dictation.lastWordsAt = Date.now();
      setVoice({ phase: "listening", held: dictation.hold });
    } catch (error) {
      if (dictationRef.current !== dictation) return;
      setVoice({ phase: "paused", held: dictation.hold });
      setTranscriptionError(microphoneErrorMessage(error));
    } finally {
      dictation.opening = false;
      dictation.starting = false;
    }
  }

  function toggleListening() {
    if (voicePhase === "listening") void pauseListening();
    else if (voicePhase === "paused") void resumeListening();
  }

  // Stopped: wait for the last words, then tidy them when tidying is on.
  async function finishDictation() {
    const dictation = dictationRef.current;
    if (!dictation || dictation.finishing || dictation.starting) return;
    dictation.finishing = true;
    setVoice({ phase: "finishing" });
    await closeSession(dictation);
    if (dictationRef.current !== dictation) return;
    const heard = dictation.words.text;
    if (!heard) {
      endDictation(dictation);
      onDraftChange(dictation.base);
      setVoiceNotice("");
      setTranscriptionError(dictation.noSignal
        ? silentInputMessage(dictation.inputLabel)
        : dictation.silent
        ? "No microphone signal was recorded. Test the input in Settings > Devices."
        : "No speech was detected. Test the microphone in Settings > Devices, then speak closer to the selected input.");
      return;
    }
    if (!dictation.tidyOn) {
      putDictatedWords(appendTranscription(dictation.base, heard), true);
      endDictation(dictation);
      textareaRef.current?.focus();
      return;
    }
    setVoice({ phase: "tidying" });
    window.clearTimeout(dictation.pauseTimer);
    window.clearTimeout(dictation.gapTimer);
    let result;
    const ready = dictation.progress.ready(heard);
    const round = dictation.round;
    if (ready !== null) {
      // A round while listening already tidied everything said: no wait.
      result = { text: ready, tidied: true, reason: "" };
    } else if (round && round.source === heard.trim()) {
      // The round that is out covers it all: wait for it rather than asking twice.
      result = await round.answer;
      if (!result) result = { text: heard, tidied: false, reason: "Tidying was stopped." };
    } else {
      round?.abort.abort();
      const abort = new AbortController();
      dictation.abort = abort;
      try {
        result = await tidyTranscript(heard, { signal: abort.signal });
      } catch (error) {
        if (error?.name === "AbortError") return;
        result = { text: heard, tidied: false, reason: "Tidying isn't available right now." };
      }
    }
    if (dictationRef.current !== dictation) return;
    // Words that couldn't be tidied wait in the box even with Send after dictation on.
    putDictatedWords(appendTranscription(dictation.base, result.text), result.tidied);
    if (!result.tidied) setVoiceNotice(`Not tidied: ${result.reason}`);
    endDictation(dictation);
    textareaRef.current?.focus();
  }

  // New words while listening, with tidying on: tidy what was said so far once enough new
  // words and time have passed, or once the speaker has been quiet for a moment.
  function heardMore(dictation) {
    window.clearTimeout(dictation.pauseTimer);
    dictation.pauseTimer = window.setTimeout(() => {
      dictation.pauseTimer = null;
      tidyRound(dictation);
    }, TIDY_PAUSE_MS);
    considerRound(dictation);
  }

  // A round now if enough words and time have passed; with the words in but not the time, a
  // round when it has.
  function considerRound(dictation) {
    if (dictationRef.current !== dictation || dictation.finishing) return;
    const wait = dictation.progress.waitMs(dictation.words.text, Date.now());
    if (wait === null) return;
    window.clearTimeout(dictation.gapTimer);
    if (wait === 0) tidyRound(dictation);
    else dictation.gapTimer = window.setTimeout(() => {
      dictation.gapTimer = null;
      considerRound(dictation);
    }, wait);
  }

  // One tidy round while listening: everything said so far goes, and the answer shows in the
  // (read-only) box. A failure changes nothing; stopping tidies again, so no words are lost.
  function tidyRound(dictation) {
    if (dictationRef.current !== dictation || dictation.finishing || !dictation.tidyOn) return;
    window.clearTimeout(dictation.pauseTimer);
    window.clearTimeout(dictation.gapTimer);
    const progress = dictation.progress;
    const source = progress.next(dictation.words.text, Date.now());
    if (!source) return;
    const abort = new AbortController();
    const answer = tidyTranscript(source, { signal: abort.signal }).catch((error) => (
      error?.name === "AbortError" ? null : { text: source, tidied: false, reason: "Tidying isn't available right now." }
    ));
    const round = { source, abort, answer };
    dictation.round = round;
    void answer.then((result) => {
      if (dictation.round === round) dictation.round = null;
      if (!result) {
        progress.dropped(source);
        return;
      }
      const changed = progress.answered(source, result);
      if (dictationRef.current !== dictation || dictation.finishing) return;
      if (changed) onDraftChange(appendTranscription(dictation.base, progress.shown));
      // Words said while it was out: a round now if it has gone quiet, otherwise when enough
      // words and time have passed.
      if (Date.now() - dictation.lastWordsAt >= TIDY_PAUSE_MS) tidyRound(dictation);
      else considerRound(dictation);
    });
  }

  // The finished words go in the box to edit; with Send after dictation on, and words fit
  // to send, they are sent once the box shows them.
  function putDictatedWords(text, sendable) {
    autoSendRef.current = dictationAutoSend && sendable && text.trim() ? { text, before: draftRef.current } : null;
    onDraftChange(text);
  }

  // Throws away only what was said in this dictation; the words already in the box stay.
  function discardDictation() {
    const dictation = dictationRef.current;
    if (!dictation) return;
    endDictation(dictation);
    onDraftChange(dictation.base);
    setVoiceNotice("");
  }

  // Stop listening and go straight on to the words; there is no Done step.
  function stopDictation() {
    const dictation = dictationRef.current;
    if (!dictation || dictation.finishing) return;
    if (dictation.starting) {
      // Stopped before listening began. Nothing new was heard, so a first start just ends;
      // a restart after a pause keeps the earlier words and goes on once it can.
      if (!dictation.words.text) discardDictation();
      else dictation.stopRequested = true;
      return;
    }
    void finishDictation();
  }

  // Press to start and stop: the mic button, the menu and, in that mode, the shortcut.
  function toggleDictation() {
    if (!dictationRef.current) {
      if (!sending) void startDictation();
      return;
    }
    stopDictation();
  }

  // Hold to talk: listening starts as the mic or the shortcut goes down...
  function pressDictation() {
    const current = dictationRef.current;
    if (current) {
      if (!current.hold) stopDictation();
      return;
    }
    if (!sending) void startDictation({ hold: true });
  }

  // ...and stops as it comes up. A quick tap, or letting go before the microphone opened,
  // says how holding works instead of reporting that nothing was heard.
  function releaseDictation() {
    const dictation = dictationRef.current;
    if (!dictation?.hold || dictation.released) return;
    dictation.released = true;
    if (!dictation.words.text && (dictation.starting || Date.now() - dictation.pressedAt < HOLD_TAP_MS)) {
      discardDictation();
      setVoiceNotice(HOLD_HINT);
      return;
    }
    stopDictation();
  }
  releaseRef.current = releaseDictation;
  stopRef.current = stopDictation;

  const voiceApiRef = useRef(null);
  voiceApiRef.current = {
    toggle: toggleDictation,
    press: dictationMode === "hold" ? pressDictation : toggleDictation,
    release: releaseDictation,
    available: () => !sending || Boolean(dictationRef.current),
  };
  const composerRef = useRef(null);
  const composerEntryRef = useRef(null);
  useEffect(() => {
    const entry = {
      root: () => composerRef.current,
      toggle: () => voiceApiRef.current.toggle(),
      press: () => voiceApiRef.current.press(),
      release: () => voiceApiRef.current.release(),
      available: () => voiceApiRef.current.available(),
      dictating: () => Boolean(dictationRef.current),
      lastUsed: 0,
    };
    composerEntryRef.current = entry;
    return registerVoiceComposer(entry);
  }, []);

  // While dictating: Space pauses or resumes, Esc discards what was just said. Enter
  // (handled by the message box) stops and goes on to the words. Returns whether it used the key.
  // The message box calls this itself; the composer's onKeyDown covers its other controls.
  function handleDictationKeys(event) {
    if (!dictationRef.current || event.nativeEvent?.isComposing) return false;
    const plain = !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey;
    if (event.key === "Escape" && plain) {
      event.preventDefault();
      event.stopPropagation();
      discardDictation();
      textareaRef.current?.focus();
      return true;
    }
    if ((event.key === " " || event.code === "Space") && plain && event.target === textareaRef.current && !dictationRef.current.hold) {
      event.preventDefault();
      toggleListening();
      return true;
    }
    return false;
  }

  const voiceBinding = useVoiceBinding();
  const voiceKeys = voiceShortcutLabel(voiceBinding);
  const voiceAriaKeys = ariaKeyshortcuts(voiceBinding);
  const error = attachmentError || transcriptionError;
  const busyVoice = voicePhase === "starting" || voicePhase === "finishing" || voicePhase === "tidying";
  const holdMode = dictationMode === "hold";
  const micLabel = !dictating ? "Dictate a message"
    : busyVoice && voicePhase !== "starting" ? "Working on your words"
    : held ? "Let go to stop dictating" : "Stop dictating";
  const micTitle = !dictating
    ? (holdMode ? `Hold to dictate${voiceKeys ? ` (or hold ${voiceKeys})` : ""}` : voiceKeys ? `Dictate (${voiceKeys})` : "Dictate")
    : held ? "Let go to stop and use what you said"
    : `Stop and use what you said (Enter${voiceKeys ? ` or ${voiceKeys}` : ""})`;
  const dictationHint = busyVoice ? "Esc discards what you said"
    : held ? "Let go to finish · Esc discards"
    : `Space ${voicePhase === "paused" ? "keeps talking" : "pauses"} · Enter finishes · Esc discards`;
  const idleHint = voiceKeys ? `${shortcutHint} · ${holdMode ? `hold ${voiceKeys}` : voiceKeys} to dictate` : shortcutHint;
  return (
    <>
      <div
        ref={composerRef}
        data-chat-composer
        onFocus={() => { if (composerEntryRef.current) composerEntryRef.current.lastUsed = Date.now(); }}
        onKeyDown={(event) => { if (event.target !== textareaRef.current) handleDictationKeys(event); }}
        className="overflow-hidden rounded-[14px] border border-line bg-white transition focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-500/10"
      >
        {attachmentWarning ? <p role="status" className="px-2.5 pt-2.5 text-[11px] text-amber-700 dark:text-amber-300">{attachmentWarning}</p> : null}
        {attachments.length ? (
          <div className="flex flex-wrap gap-1.5 px-2.5 pt-2.5">
            {attachments.map((attachment) => (
              <AttachmentTile
                key={attachment.id}
                attachment={attachment}
                variant="composer"
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
          aria-label={inputLabel}
          aria-describedby={error ? errorId : undefined}
          className="block min-h-14 max-h-32 w-full resize-none bg-transparent px-3 pb-1 pt-3 text-sm leading-5 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed disabled:text-muted"
          readOnly={dictating}
          placeholder={dictating ? "" : placeholder ?? (sending ? "Steer Rem while it works" : attachments.length ? "Add a note about the attached file" : "Message this workflow")}
          rows={2}
          value={draft}
          onChange={(event) => { setVoiceNotice(""); autoSendRef.current = null; onDraftChange(event.target.value); }}
          onKeyDown={(event) => {
            if (handleDictationKeys(event)) return;
            if (event.key !== "Enter" || event.shiftKey || event.nativeEvent?.isComposing || event.isComposing) return;
            event.preventDefault();
            // While dictating, Enter stops listening and goes on to the words.
            if (dictating) {
              if (listening || voicePhase === "paused") stopDictation();
              return;
            }
            if (sending) {
              if ((draft.trim() || attachments.length) && !steeringPending) onSteer?.();
            } else if (!sendDisabled && (draft.trim() || attachments.length)) onSend(null, { background: event.ctrlKey });
          }}
        />
        {dictating ? (
          <div className="px-2.5 pt-1" data-dictation={voicePhase}>
            {held && listening ? (
              <VoiceDots mood={DotMood.LISTENING} level={() => levelRef.current} height={48} />
            ) : voicePhase === "listening" || voicePhase === "paused" ? (
              <VoiceDots
                mood={listening ? DotMood.LISTENING : DotMood.PAUSED}
                level={() => levelRef.current}
                label={listening ? "Pause listening" : "Keep talking"}
                title={`${listening ? "Pause listening" : "Keep talking"} (Space)`}
                keyshortcuts="Space"
                onToggle={toggleListening}
                height={48}
              />
            ) : (
              <VoiceDots mood={DotMood.THINKING} height={48} />
            )}
            {/* The line says what is happening; screen readers hear it here. */}
            <p role="status" className="sr-only">{VOICE_STATUS[voicePhase]}</p>
          </div>
        ) : null}
        <div className="flex items-center justify-between px-1.5 pb-1.5 pt-0.5">
          <div className="flex min-w-0 flex-1 items-center gap-0.5">
            <button
              aria-disabled={busyVoice && voicePhase !== "starting" ? "true" : undefined}
              aria-keyshortcuts={dictating && !held ? (voiceAriaKeys ? `Enter ${voiceAriaKeys}` : "Enter") : voiceAriaKeys}
              aria-label={micLabel}
              aria-pressed={dictating}
              className={`grid h-8 w-8 touch-none select-none place-items-center rounded-lg transition disabled:cursor-not-allowed disabled:opacity-50 ${
                dictating ? "bg-brand text-white hover:bg-indigo-700" : "text-muted hover:bg-slate-100 hover:text-ink"
              }`}
              data-dictation-mode={dictationMode}
              disabled={sending && !dictating}
              title={micTitle}
              type="button"
              onClick={(event) => {
                // Holding starts and stops on press and release, not on click.
                if (dictationMode === "hold" || (busyVoice && voicePhase !== "starting")) return;
                event.preventDefault();
                toggleDictation();
              }}
              onKeyDown={(event) => {
                if (dictationMode !== "hold" || (event.key !== " " && event.key !== "Enter")) return;
                event.preventDefault();
                if (!event.repeat) pressDictation();
              }}
              onKeyUp={(event) => {
                if (dictationMode !== "hold" || (event.key !== " " && event.key !== "Enter")) return;
                event.preventDefault();
                releaseDictation();
              }}
              onPointerDown={(event) => {
                if (dictationMode !== "hold" || event.button !== 0 || (busyVoice && voicePhase !== "starting")) return;
                event.preventDefault();
                // Keeps the release coming here even if the pointer slides off the button.
                event.currentTarget.setPointerCapture?.(event.pointerId);
                pressDictation();
              }}
              onPointerUp={() => { if (dictationMode === "hold") releaseDictation(); }}
              onPointerCancel={() => { if (dictationMode === "hold") releaseDictation(); }}
              onLostPointerCapture={() => { if (dictationMode === "hold") releaseDictation(); }}
            >
              {busyVoice && voicePhase !== "starting" ? <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={15} /> : <Mic aria-hidden="true" size={16} />}
            </button>
            {dictating ? (
              <button
                aria-keyshortcuts="Escape"
                aria-label="Discard dictation"
                className="grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
                title="Discard what you said and keep the rest of the message (Esc)"
                type="button"
                onClick={discardDictation}
              >
                <X aria-hidden="true" size={16} />
              </button>
            ) : null}
            <button
              aria-label="Attach files"
              className="grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
              disabled={steeringPending || dictating}
              title="Attach files"
              type="button"
              onClick={() => fileInputRef.current?.click()}
            >
              <Paperclip aria-hidden="true" size={16} />
            </button>
            {permissionOptions.length ? (
              <select
                aria-label="Rem permissions"
                className={menuClass}
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
            {machines.length ? (
              <select
                aria-label="Run on"
                className={menuClass}
                disabled={sending || machineLocked}
                value={machine}
                title={machineLocked ? "This thread keeps running on the computer it started on" : "Computer that runs this thread"}
                onChange={(event) => onMachineChange(event.target.value)}
              >
                <option value="">This computer</option>
                {machines.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
                {machine && !machines.some(({ id }) => id === machine) ? <option value={machine}>Unpaired computer</option> : null}
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
          <>
              {sending ? <button
                aria-label="Steer Rem"
                className="mr-2 rounded-lg bg-brand px-3 py-2 text-xs text-white disabled:opacity-50"
                disabled={(!draft.trim() && !attachments.length) || steeringPending}
                type="button"
                onClick={onSteer}
              >{steeringPending ? "Submitting…" : "Steer"}</button> : null}
              <button
                aria-label={sending ? "Stop Rem" : sendLabel}
                className={`grid h-9 w-9 place-items-center rounded-[10px] transition disabled:cursor-not-allowed disabled:opacity-60 ${
                  sending
                    ? "border border-line bg-white text-red-600 hover:border-red-200 hover:bg-red-50"
                    : "bg-brand text-white hover:bg-indigo-700"
                }`}
                disabled={!sending && (dictating || sendDisabled || (!draft.trim() && !attachments.length))}
                title={sending ? "Stop Rem" : sendLabel}
                type="button"
                onClick={sending ? onStop : onSend}
              >
                {sending ? (
                  <Square aria-hidden="true" fill="currentColor" size={13} strokeWidth={1.7} />
                ) : (
                  <Send aria-hidden="true" size={15} />
                )}
              </button>
          </>
        </div>
      </div>
      {error ? <p id={errorId} className="mt-1.5 px-1 text-[10px] text-red-600">{error}</p> : null}
      {voiceNotice ? <p role="status" className="mt-1.5 px-1 text-[10px] text-amber-700 dark:text-amber-300">{voiceNotice}</p> : null}
      <p className="mt-1.5 whitespace-normal break-words px-1 text-[9px] leading-snug text-muted">
        {dictating ? dictationHint : sending ? "Enter to steer · Stop cancels the response" : idleHint}
      </p>
    </>
  );
}

const VOICE_STATUS = {
  starting: "Starting the microphone",
  listening: "Listening",
  paused: "Paused",
  finishing: "Finishing",
  tidying: "Tidying your words",
};
const NO_SPEECH = /No speech was detected/;
// How long stopping waits for the last buffer of audio to arrive: one buffer period plus margin.
const RECORDER_TAIL_MS = 150;
// In Hold to talk, a press shorter than this with nothing heard is a tap, not a dictation.
const HOLD_TAP_MS = 400;
const HOLD_HINT = "Hold to talk: keep the mic pressed while you speak, and let go when you're done.";

export function resizeChatComposerTextarea(textarea) {
  if (!textarea) return;
  textarea.style.height = "auto";
  const contentHeight = Number(textarea.scrollHeight);
  if (!Number.isFinite(contentHeight)) return;
  const height = Math.min(Math.max(contentHeight, 56), 128);
  textarea.style.height = `${height}px`;
  textarea.style.overflowY = contentHeight > 128 ? "auto" : "hidden";
}

// Files sent with a message: thumbnails for pictures and videos, file-type tiles for
// the rest, each opening a preview.
export function MessageAttachments({ attachments = [], inverse = false, onOpenLink }) {
  if (!attachments.length) return null;
  return (
    <div className={`flex flex-wrap items-start gap-1.5 ${inverse ? "mb-2" : "mt-2"}`}>
      {attachments.map((attachment) => (
        <AttachmentTile key={attachment.id ?? attachment.name} attachment={attachment} inverse={inverse} onOpenLink={onOpenLink} />
      ))}
    </div>
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

function createStreamingTranscriber(sessionId, onText, onError, onNotice = () => {}) {
  let closed = false;
  let failure = null;
  let queue = Promise.resolve();

  return {
    push(pcm) {
      if (closed || failure) return;
      queue = queue.then(async () => {
        try {
          const payload = await streamTranscriptionChunk(sessionId, pcm);
          // The OS recognizer failed and Raticode's offline model took over.
          if (payload.notice) onNotice(String(payload.notice));
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
      if (payload.notice) onNotice(String(payload.notice));
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

async function createLocalPcmRecorder(stream, AudioContextConstructor, onPcmChunk, onLevel = () => {}, onSignal = () => {}) {
  const context = new AudioContextConstructor();
  await context.resume?.();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const sink = context.createGain();
  let chunks = [];
  let bufferedSamples = 0;
  let recordedSamples = 0;
  let peakLevel = 0;
  let anySound = false;
  // Stop is waiting for the audio still on its way (see stop()).
  let tail = null;
  const watchSilence = createSilenceWatch();
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
    let hasSound = false;
    for (const sample of chunk) {
      if (sample !== 0) hasSound = true;
      peakLevel = Math.max(peakLevel, Math.abs(sample));
    }
    anySound ||= hasSound;
    onLevel(levelFromSamples(chunk));
    const signal = watchSilence(hasSound, (chunk.length * 1000) / context.sampleRate);
    if (signal) onSignal(signal);
    if (bufferedSamples >= streamChunkSamples) flushChunks();
    if (tail) {
      const arrived = tail;
      tail = null;
      arrived();
    }
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
    // recorded is false when nothing, or only silence, reached the microphone.
    async stop() {
      // When stop comes, up to one buffer of audio (4096 samples, about 85 ms) is still on its
      // way to the processor, often the end of the last word, especially when letting go of a
      // held mic. Let it arrive before closing; a stuck or closed audio context never delays stop.
      if (!closed && context.state === "running") {
        await new Promise((resolve) => {
          tail = resolve;
          setTimeout(resolve, RECORDER_TAIL_MS);
        });
        tail = null;
      }
      await close();
      // signal is false when the microphone sent only zeros: muted or switched off.
      if (!recordedSamples || peakLevel < 0.003) return { recorded: false, signal: anySound || !recordedSamples };
      flushChunks();
      return { recorded: true, signal: true };
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
