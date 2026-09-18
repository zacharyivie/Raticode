import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { bundleBudget } from "./benchmarks/bundle-budget.js";

const apiBaseUrl = process.env.VITE_API_BASE_URL || "http://127.0.0.1:8765";

export default defineConfig({
  base: "./",
  build: {
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (/\/node_modules\/(?:react|react-dom|scheduler)\//.test(id)) return "react";
          if (id.includes("node_modules") && /(?:react-markdown|remark-|rehype-|micromark|mdast-|hast-|unist-|unified)/.test(id)) return "markdown";
        },
      },
    },
  },
  plugins: [react(), bundleBudget(), {
    name: "development-studio-csp",
    apply: "serve",
    transformIndexHtml(html) {
      return html.replace("script-src 'self';", "script-src 'self' 'unsafe-inline';")
        .replace("connect-src 'self'", "connect-src 'self' ws://127.0.0.1:* ws://localhost:*");
    },
  }],
  server: {
    proxy: {
      "/api": {
        target: apiBaseUrl,
        changeOrigin: true,
      },
    },
  },
});
