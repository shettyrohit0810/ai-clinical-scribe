import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  WebSpeechTranscriptionProvider,
  type TranscriptionHandlers,
  type TranscriptionProvider,
} from "./transcription";

export type VoiceEditState = "idle" | "listening" | "paused";

export interface VoiceEditNote {
  subjective: string;
  objective: string;
  assessment: string;
  plan: string;
}

interface UseVoiceEditOptions {
  encounterId: string | number;
  onPatchApplied: (note: VoiceEditNote, patch: Record<string, unknown>) => void;
}

// Separate hook from useDictation, on purpose: this is a genuinely different
// mode (editing an already-generated note by spoken command, not dictating
// new transcript) with its own transport (WebSocket, not SSE) and its own
// output (a JSON patch applied server-side, not streamed SOAP text). It
// reuses the SAME TranscriptionProvider interface for STT — the browser
// still does all the listening — just in "one utterance = one command"
// mode instead of "append everything to a buffer" mode.
export function useVoiceEdit(opts: UseVoiceEditOptions) {
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const providerRef = useRef<TranscriptionProvider>(new WebSpeechTranscriptionProvider());
  const socketRef = useRef<WebSocket | null>(null);

  const [state, setState] = useState<VoiceEditState>("idle");
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const [interim, setInterim] = useState("");
  const [lastHeard, setLastHeard] = useState("");
  const [lastMessage, setLastMessage] = useState("");
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hadErrorRef = useRef(false);

  const speak = useCallback((text: string) => {
    if (!text || typeof window.speechSynthesis === "undefined") return;
    window.speechSynthesis.cancel(); // never queue behind a stale confirmation
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
  }, []);

  // New speech is a deliberate interruption signal: a clinician talking
  // over the assistant's own TTS confirmation means "stop talking, listen
  // to me" — not "queue this after you finish." Cancelling on the FIRST
  // interim result (not waiting for onFinal) is what makes the interrupt
  // feel immediate rather than lagging a full utterance behind.
  const interruptSpeech = useCallback(() => {
    if (typeof window.speechSynthesis !== "undefined" && window.speechSynthesis.speaking) {
      window.speechSynthesis.cancel();
    }
  }, []);

  function clearSocket() {
    const ws = socketRef.current;
    if (ws) {
      ws.onmessage = null;
      ws.onclose = null;
      ws.onerror = null;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
    }
    socketRef.current = null;
  }

  function connectSocket() {
    clearSocket();
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${proto}//${window.location.host}/ws/encounters/${optsRef.current.encounterId}/voice-edit`,
    );
    ws.onmessage = (event) => {
      setProcessing(false);
      let msg: { type?: string; note?: VoiceEditNote; patch?: Record<string, unknown>; message?: string };
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      if (msg.type === "patch_applied" && msg.note) {
        setLastMessage(msg.message ?? "");
        optsRef.current.onPatchApplied(msg.note, msg.patch ?? {});
        speak(msg.message ?? "");
      } else if (msg.type === "error") {
        setLastMessage(msg.message ?? "");
        speak(msg.message ?? "");
      }
    };
    ws.onclose = () => {
      setProcessing(false);
      socketRef.current = null;
    };
    ws.onerror = () => {
      setError("Voice edit connection lost. Try Start again.");
    };
    socketRef.current = ws;
  }

  const handlers: TranscriptionHandlers = useMemo(() => {
    const h: TranscriptionHandlers = {
      onInterim: (text) => {
        setInterim(text);
        if (text.trim()) interruptSpeech();
      },
      onFinal: (text) => {
        const command = text.trim();
        setInterim("");
        if (!command) return;
        interruptSpeech();
        setLastHeard(command);
        const ws = socketRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) {
          setProcessing(true);
          ws.send(JSON.stringify({ type: "command", text: command }));
        }
      },
      onError: (message) => {
        setError(message);
        hadErrorRef.current = true;
        setState("idle");
      },
      onEnd: () => {
        // Same restart-if-still-listening contract as useDictation: Chrome
        // can end a "continuous" session on its own after silence.
        if (stateRef.current === "listening" && !hadErrorRef.current) {
          providerRef.current.start(h);
        }
      },
    };
    return h;
  }, [interruptSpeech]);

  function start() {
    setError(null);
    hadErrorRef.current = false;
    setLastHeard("");
    setLastMessage("");
    connectSocket();
    providerRef.current.start(handlers);
    setState("listening");
  }

  function pause() {
    setState("paused");
    providerRef.current.stop();
    setInterim("");
  }

  function resume() {
    setState("listening");
    providerRef.current.start(handlers);
  }

  function stop() {
    setState("idle");
    providerRef.current.stop();
    setInterim("");
    setProcessing(false);
    clearSocket();
    if (typeof window.speechSynthesis !== "undefined") window.speechSynthesis.cancel();
  }

  useEffect(
    () => () => {
      providerRef.current.stop();
      clearSocket();
    },
    [],
  );

  return {
    state,
    interim,
    lastHeard,
    lastMessage,
    processing,
    error,
    supported: providerRef.current.isSupported,
    start,
    pause,
    resume,
    stop,
  };
}
