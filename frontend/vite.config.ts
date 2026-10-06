/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built dashboard is served by the gateway itself (backend/src/hestia/static),
// so the whole product is one origin: no CORS, strict CSP, one HTTPS port.
// In development, `npm run dev` proxies /api to a gateway on :8000.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../backend/src/hestia/static",
    emptyOutDir: true,
    sourcemap: false,
    // Never inline assets as data: URIs; the gateway's CSP (font-src/img-src 'self') would block them.
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 800,
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
