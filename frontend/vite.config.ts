import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev-only proxy: the browser talks to a single origin (5173) and /api is
// forwarded to the backend on 8001 — the same shape as production nginx,
// so CORS never needs configuring in either environment.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8001",
    },
  },
});
