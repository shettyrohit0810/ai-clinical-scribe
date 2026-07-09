import { useEffect, useRef, useState } from "react";

type StreamState = "idle" | "streaming" | "done" | "error";

/**
 * Phase 0 infrastructure check: consumes /api/dev/stream-test over SSE and
 * renders each number the moment it arrives. Watching the chips appear one
 * by one over HTTPS through nginx is the definition of done for Phase 0 —
 * it proves the exact transport the real note-generation stream will use.
 */
export default function StreamTest() {
  const [numbers, setNumbers] = useState<string[]>([]);
  const [state, setState] = useState<StreamState>("idle");
  const sourceRef = useRef<EventSource | null>(null);

  // Close any open stream if the component unmounts mid-flight.
  useEffect(() => () => sourceRef.current?.close(), []);

  function start() {
    sourceRef.current?.close();
    setNumbers([]);
    setState("streaming");

    const source = new EventSource("/api/dev/stream-test");
    sourceRef.current = source;
    source.onmessage = (e) => setNumbers((prev) => [...prev, e.data]);
    // Server sends a named "done" event; without closing here, EventSource
    // would auto-reconnect forever after the server ends the response.
    source.addEventListener("done", () => {
      source.close();
      setState("done");
    });
    source.onerror = () => {
      source.close();
      setState("error");
    };
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">
            Infrastructure check — SSE streaming
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            Streams 1–20 from the backend at 200 ms intervals. Numbers must
            appear progressively (never all at once) — including through
            nginx over HTTPS.
          </p>
        </div>
        <button
          onClick={start}
          disabled={state === "streaming"}
          className="rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:bg-slate-300"
        >
          {state === "streaming" ? "Streaming…" : "Start stream test"}
        </button>
      </div>

      <div className="mt-4 flex min-h-10 flex-wrap items-center gap-2">
        {numbers.map((n) => (
          <span
            key={n}
            className="rounded bg-slate-100 px-2.5 py-1 font-mono text-sm text-slate-700"
          >
            {n}
          </span>
        ))}
        {state === "idle" && (
          <span className="text-sm text-slate-400">
            No stream started yet.
          </span>
        )}
      </div>

      <p className="mt-3 text-xs">
        {state === "done" && (
          <span className="font-medium text-emerald-700">
            Stream completed — 20/20 events received.
          </span>
        )}
        {state === "error" && (
          <span className="font-medium text-red-700">
            Stream failed — is the backend running on port 8001?
          </span>
        )}
      </p>
    </section>
  );
}
