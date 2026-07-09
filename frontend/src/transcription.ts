// Speech-to-text abstraction. Browser Web Speech API is the guaranteed-
// working baseline (WebSpeechTranscriptionProvider below); a server-side
// streaming STT (mic -> WebSocket -> backend -> vendor) is a Phase 10
// stretch that would implement this SAME interface — Workspace.tsx and
// useDictation.ts never reference webkitSpeechRecognition directly, so
// swapping providers later is a one-line change at the construction site,
// not a rewrite of the dictation UI or its sync logic.

export interface TranscriptionHandlers {
  /** Not-yet-finalized text for the CURRENT utterance — replaces on every
   * call (may shrink/change as the recognizer revises its guess). */
  onInterim: (text: string) => void;
  /** A finalized chunk of speech, ready to append permanently. */
  onFinal: (text: string) => void;
  onError: (message: string) => void;
  /** The recognition session ended, for any reason (explicit stop, error,
   * or the browser's own idle/silence timeout — see WebSpeechTranscriptionProvider). */
  onEnd: () => void;
}

export interface TranscriptionProvider {
  readonly isSupported: boolean;
  start(handlers: TranscriptionHandlers): void;
  stop(): void;
}

// ---- Web Speech API (non-standard; not in TypeScript's lib.dom.d.ts) --------

interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: { readonly transcript: string };
}

interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: {
    readonly length: number;
    [index: number]: SpeechRecognitionResultLike;
  };
}

interface SpeechRecognitionErrorEventLike {
  readonly error: string;
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  }
}

export class WebSpeechTranscriptionProvider implements TranscriptionProvider {
  private recognition: SpeechRecognitionLike | null = null;

  get isSupported(): boolean {
    return typeof window !== "undefined" && !!this.ctor();
  }

  private ctor(): SpeechRecognitionCtor | undefined {
    return window.SpeechRecognition ?? window.webkitSpeechRecognition;
  }

  start(handlers: TranscriptionHandlers): void {
    const Ctor = this.ctor();
    if (!Ctor) {
      handlers.onError("Voice dictation is not supported in this browser.");
      return;
    }
    const recognition = new Ctor();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const text = result[0].transcript;
        if (result.isFinal) handlers.onFinal(text);
        else interim += text;
      }
      handlers.onInterim(interim);
    };
    recognition.onerror = (event) => {
      // "no-speech" is the API's own signal that a recognition round ended
      // without capturing audio — expected any time the mic goes quiet
      // (routinely right after stop()/pause() shuts the session down, or
      // mid-session on a lull), not a failure the clinician needs to see.
      // onend still fires right after this and drives the actual state
      // transition (restart if still "listening", otherwise no-op) — so
      // swallowing this here only skips the error banner, nothing else.
      if (event.error === "no-speech") return;
      handlers.onError(
        event.error === "not-allowed" || event.error === "permission-denied"
          ? "Microphone access was denied."
          : `Dictation error: ${event.error}`,
      );
    };
    recognition.onend = handlers.onEnd;

    this.recognition = recognition;
    recognition.start();
  }

  stop(): void {
    // onend still fires after stop() — callers that want to distinguish
    // "I asked it to stop" from "the browser ended it on its own" track
    // that themselves (see useDictation's pause/stop vs. auto-restart).
    this.recognition?.stop();
    this.recognition = null;
  }
}
