import { useEffect, useMemo, useRef, useState } from "react";
import {
  WebSpeechTranscriptionProvider,
  type TranscriptionHandlers,
  type TranscriptionProvider,
} from "./transcription";

// "on each pause OR every ~6s of new finalized text" (spec). We deliberately
// don't rely on the browser's own onspeechend/onaudioend events for pause
// detection — their firing semantics differ enough across Chrome/Safari's
// webkitSpeechRecognition implementations in continuous mode that timing
// would be unpredictable across browsers. Instead we self-time: a short
// poll (CHECK_INTERVAL_MS) checks two independent, easy-to-reason-about
// conditions against wall-clock timestamps we control ourselves.
const PAUSE_TRIGGER_MS = 2000; // "a pause" = this long since the last final chunk
const MAX_INTERVAL_MS = 6000; // force a regen at least this often during continuous speech
const CHECK_INTERVAL_MS = 1000;

export type DictationState = "idle" | "listening" | "paused";

interface UseDictationOptions {
  getTranscript: () => string;
  onCommitTranscript: (next: string) => void;
  onRollingRegenerate: () => void;
  onFinalRegenerate: () => void;
}

export function useDictation(opts: UseDictationOptions) {
  // A dictation session (its interval timer, its recognition callbacks) can
  // outlive several Workspace re-renders — e.g. every time the transcript
  // changes, Workspace's generate()/onRollingRegenerate identities change
  // too. Closing over `opts` directly at start()-time would freeze those
  // callbacks to whatever `transcript`/`noteDirty` existed at that moment.
  // Routing every read through this ref (updated on every render, but never
  // read during render) is what keeps a long-running session's timer and
  // recognition handlers always acting on current state.
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const providerRef = useRef<TranscriptionProvider>(new WebSpeechTranscriptionProvider());

  const [state, setState] = useState<DictationState>("idle");
  const stateRef = useRef(state); // onEnd reads this to avoid a stale closure
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Timing state for the rolling-regeneration trigger. Refs, not state —
  // these are read/written on every recognizer event and every 1s tick;
  // routing them through React state would cause needless re-renders.
  const lastFinalAtRef = useRef<number | null>(null);
  const lastRegenAtRef = useRef(Date.now());
  const hasPendingRef = useRef(false);
  const checkTimerRef = useRef<number | undefined>(undefined);
  // Guards onEnd's auto-restart: without this, a permission denial (or any
  // other recognition error) fires onerror followed by onend, and a naive
  // "still listening? restart" rule would retry the exact same failing
  // permission forever. Reset at the start of every fresh session.
  const hadErrorRef = useRef(false);

  // Created exactly once: every callback goes through optsRef/stateRef, so
  // there is nothing here that needs to change across renders.
  const handlers: TranscriptionHandlers = useMemo(() => {
    const h: TranscriptionHandlers = {
      onInterim: setInterim,
      onFinal: (text) => {
        const chunk = text.trim();
        if (!chunk) return;
        // Append-only: the transcript is a single editable buffer dictation
        // appends to. Reading getTranscript() fresh here (rather than
        // capturing a stale value) is what makes manual edits made between
        // dictation bursts — including during a "paused" window — survive:
        // whatever the buffer currently holds, including any hand-typed
        // corrections, is exactly what new speech gets appended after.
        const base = optsRef.current.getTranscript();
        optsRef.current.onCommitTranscript(base ? `${base} ${chunk}` : chunk);
        setInterim("");
        lastFinalAtRef.current = Date.now();
        hasPendingRef.current = true;
      },
      onError: (message) => {
        setError(message);
        hadErrorRef.current = true;
        // Any recognition error (permission denied, no-speech, network, …)
        // ends the session unrecoverably from here — drop back to idle
        // rather than leave Listening/Pause/Stop controls displayed for a
        // recognizer that isn't actually running. The user can just click
        // Start again once they've fixed whatever's wrong (e.g. granted
        // mic access).
        setState("idle");
        clearCheckTimer();
      },
      onEnd: () => {
        // Browsers can end a "continuous" session on their own (Chrome does
        // this after a period of silence even mid-utterance) without the
        // user ever clicking Pause or Stop. If the user's intent is still
        // "listening" AND nothing errored, restart transparently — the
        // committed transcript buffer makes the restart invisible; only a
        // live interim word might flicker. If an error just fired, DO NOT
        // restart: that would retry a failing permission/condition forever.
        if (stateRef.current === "listening" && !hadErrorRef.current) {
          providerRef.current.start(h);
        }
      },
    };
    return h;
  }, []);

  function clearCheckTimer() {
    if (checkTimerRef.current !== undefined) {
      window.clearInterval(checkTimerRef.current);
      checkTimerRef.current = undefined;
    }
  }

  function beginListening() {
    setError(null);
    hadErrorRef.current = false;
    lastRegenAtRef.current = Date.now();
    hasPendingRef.current = false;
    providerRef.current.start(handlers);
    setState("listening");
    clearCheckTimer();
    checkTimerRef.current = window.setInterval(() => {
      if (!hasPendingRef.current) return;
      const now = Date.now();
      const pauseReached =
        lastFinalAtRef.current !== null && now - lastFinalAtRef.current >= PAUSE_TRIGGER_MS;
      const maxIntervalReached = now - lastRegenAtRef.current >= MAX_INTERVAL_MS;
      if (pauseReached || maxIntervalReached) {
        hasPendingRef.current = false;
        lastRegenAtRef.current = now;
        optsRef.current.onRollingRegenerate();
      }
    }, CHECK_INTERVAL_MS);
  }

  function pause() {
    // Set state BEFORE stop(): stop() triggers the provider's onend, and
    // onEnd's auto-restart only fires when state is still "listening" — so
    // ordering this first is what makes Pause actually stop listening
    // instead of being silently un-paused by the restart-on-end logic.
    setState("paused");
    providerRef.current.stop();
    clearCheckTimer();
    setInterim("");
  }

  function resume() {
    beginListening();
  }

  function stop() {
    setState("idle");
    providerRef.current.stop();
    clearCheckTimer();
    setInterim("");
    optsRef.current.onFinalRegenerate();
  }

  useEffect(
    () => () => {
      providerRef.current.stop();
      clearCheckTimer();
    },
    [],
  );

  return {
    state,
    interim,
    error,
    supported: providerRef.current.isSupported,
    start: beginListening,
    pause,
    resume,
    stop,
  };
}
