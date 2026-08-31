import { readFileSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Version's single source of truth is __version__ in backend/hushcast/__init__.py.
function appVersion(): string {
  const init = readFileSync(new URL("../backend/hushcast/__init__.py", import.meta.url), "utf-8");
  const match = init.match(/__version__\s*=\s*"([^"]+)"/);
  if (!match) throw new Error("could not find __version__ in backend/hushcast/__init__.py");
  return match[1];
}

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion()),
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:4874",
      // Regex, not a bare "/p" prefix that would swallow SPA routes like /podcasts/1.
      "^/p/": "http://127.0.0.1:4874",
    },
  },
});
