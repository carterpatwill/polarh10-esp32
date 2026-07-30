import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Front-end build config.
//   npm run dev   → dev server on :5173, proxies /api to Flask on :8000
//   npm run build → static bundle into ../static/dist, which Flask serves
export default defineConfig({
  plugins: [react()],
  build: {
    // Emitted next to app.py (Raspberrypi/dashboard/static/dist) and rsynced to
    // the Pi by deploy-dashboard.sh. Gitignored — it's a build artifact.
    outDir: "../static/dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
