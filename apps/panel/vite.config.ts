import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// @ts-expect-error — package.json is outside rootDir but vite can still import it
import pkg from "./package.json";

const BACKEND = "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    port: 5173,
    proxy: {
      "/cutmaster": BACKEND,
      "/ping": BACKEND,
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
});
