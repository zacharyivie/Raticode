// Browser development uses the configured backend. Desktop requests use their IPC session.
export function developmentApiProxy(target, logger) {
  let unavailable = false;
  const message = "The development API backend is unavailable. Use the desktop app, or start a backend and set VITE_API_BASE_URL to its address.";
  const customLogger = Object.create(logger);
  customLogger.error = (text, options) => {
    if (options?.error?.code === "ECONNREFUSED" && /http proxy error:/.test(text)) {
      if (!unavailable) logger.warn(message);
      unavailable = true;
      return;
    }
    logger.error(text, options);
  };
  return {
    customLogger,
    proxy: {
      target,
      changeOrigin: true,
      configure(proxy) {
        proxy.on("proxyRes", () => { unavailable = false; });
        proxy.on("error", (error, _request, response) => {
          if (error.code === "ECONNREFUSED" && response.writeHead && !response.headersSent && !response.writableEnded) {
            response.writeHead(503, { "Content-Type": "application/json", "Retry-After": "3" });
            response.end(JSON.stringify({ error: message }));
          }
        });
      },
    },
  };
}
