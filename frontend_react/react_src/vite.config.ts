import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import viteYaml from "@modyfi/vite-plugin-yaml";
import path from "path";

// Dev-server port (Vite's own default) and the static/codespace port used when
// building the notebook-session base path. Backend (FastAPI) runs on :8080 —
// matches the convention used across DataRobot's other app templates (e.g.
// datarobot-agent-application's frontend_web), which also leave Vite on 5173
// rather than pairing it adjacent to the backend port.
const VITE_DEFAULT_PORT = "5173";
const VITE_STATIC_DEFAULT_PORT = "8080";

let base: string = "";
// 1. if NOTEBOOK_ID is set, use /notebook-sessions/${NOTEBOOK_ID}/ports/5173/ for dev server
// 2. if NOTEBOOK_ID and NODE_ENV === 'development', use the static codespace port (8080)
if (process.env.NOTEBOOK_ID && process.env.NODE_ENV === "development") {
  const notebookId = process.env.NOTEBOOK_ID;
  const defaultPort = process.env.STATIC_CODESPACE
    ? VITE_STATIC_DEFAULT_PORT
    : VITE_DEFAULT_PORT;
  base = `/notebook-sessions/${notebookId}/ports/${defaultPort}/`;
}
const proxyBase: string = base === "" ? "/" : base;

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    viteYaml(),
    {
      name: "strip-base",
      apply: "serve",
      configureServer({ middlewares }) {
        middlewares.use((req, _res, next) => {
          if (base !== "" && !req.url?.startsWith(base)) {
            req.url = base.slice(0, -1) + req.url;
          }
          next();
        });
      },
    },
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "~": path.resolve(__dirname, "./src"),
    },
  },
  base: base,
  build: {
    // react_src is at frontend_react/react_src → ../../ reaches the repo root,
    // so the SPA builds into repo-root forecastic/build (served by FastAPI).
    outDir: "../../forecastic/build/",
    emptyOutDir: true,
    rollupOptions: {
      external: ["_dr_env.js"],
    },
  },
  server: {
    port: 5173,
    host: true,
    allowedHosts: ["0.0.0.0", "localhost", "127.0.0.1", ".datarobot.com"],
    proxy: {
      [`${proxyBase}api/`]: {
        target: "http://localhost:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(new RegExp(`^${proxyBase}`), ""),
      },
      [`${proxyBase}_dr_env.js`]: {
        target: "http://localhost:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(new RegExp(`^${proxyBase}`), ""),
      },
    },
  },
});
