import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";
import { createServer } from "vite";
import { developmentApiProxy } from "../../dev/api-proxy.js";

test("development proxy reports an outage once, returns JSON, and recovers", async () => {
  const backend = http.createServer((_request, response) => response.end('{"ready":true}'));
  await new Promise(resolve => backend.listen(0, "127.0.0.1", resolve));
  const port = backend.address().port;
  await new Promise(resolve => backend.close(resolve));
  const messages = [];
  const logger = { info() {}, warn: text => messages.push(text), error: text => messages.push(text), clearScreen() {}, hasErrorLogged: () => false, hasWarned: false };
  const proxy = developmentApiProxy(`http://127.0.0.1:${port}`, logger);
  const vite = await createServer({ configFile: false, customLogger: proxy.customLogger, server: { host: "127.0.0.1", port: 0, proxy: { "/api": proxy.proxy } } });
  try {
    await vite.listen();
    const origin = `http://127.0.0.1:${vite.httpServer.address().port}`;
    for (const route of ["workflows", "queue", "workflows"]) {
      const response = await fetch(`${origin}/api/${route}`);
      assert.equal(response.status, 503);
      assert.match((await response.json()).error, /VITE_API_BASE_URL/);
    }
    assert.equal(messages.length, 1);
    await new Promise(resolve => backend.listen(port, "127.0.0.1", resolve));
    assert.equal((await fetch(`${origin}/api/queue`)).status, 200);
    await new Promise(resolve => backend.close(resolve));
    assert.equal((await fetch(`${origin}/api/queue`)).status, 503);
    assert.equal(messages.length, 2);
    proxy.customLogger.error("unrelated error", { error: { code: "EOTHER" } });
    assert.equal(messages.at(-1), "unrelated error");
  } finally {
    await vite.close();
    if (backend.listening) await new Promise(resolve => backend.close(resolve));
  }
});
