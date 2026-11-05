import { createLogger, defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { developmentApiProxy } from "./dev/api-proxy.js";
import { bundleBudget } from "./benchmarks/bundle-budget.js";

const apiBaseUrl = process.env.VITE_API_BASE_URL || "http://127.0.0.1:8765";

const apiProxy = developmentApiProxy(apiBaseUrl, createLogger());

export default defineConfig({
  customLogger: apiProxy.customLogger,
  base: "./",
  build: {
    chunkSizeWarningLimit: 500,
    rolldownOptions: {
      output: {
        // A group also takes in the modules it imports. React ranks higher so the markdown
        // group cannot pull React (and with it markdown) onto the startup path.
        codeSplitting: {
          groups: [
            { name: "react", priority: 2, test: /\/node_modules\/(?:react|react-dom|scheduler)\// },
            {
              name: "markdown",
              priority: 1,
              test: (id) => id.includes("node_modules") && /(?:react-markdown|remark-|rehype-|micromark|mdast-|hast-|unist-|unified)/.test(id),
            },
          ],
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
      "/api": apiProxy.proxy,
    },
  },
});
