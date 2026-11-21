import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the UI runs on :5173 and proxies API calls to the FastAPI server on :8000.
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
});
