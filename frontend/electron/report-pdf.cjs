// Render authored HTML using the Chromium already shipped with the desktop app.
const http = require("node:http");
const crypto = require("node:crypto");

async function renderPdf(html, { BrowserWindow, session }) {
  const isolated = session.fromPartition(`report-pdf-${crypto.randomUUID()}`);
  isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  isolated.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !details.url.startsWith("data:") && details.url !== "about:blank" }));
  const win = new BrowserWindow({ show: false, webPreferences: { session: isolated,
    sandbox: true, contextIsolation: true, nodeIntegration: false, javascript: false,
    webSecurity: true, backgroundThrottling: false } });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", event => event.preventDefault());
  let timer;
  try {
    return await Promise.race([
      (async () => {
        const csp = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; font-src data:; base-uri \'none\'; form-action \'none\'">';
        const title = /<title[\s>]/i.test(html) ? "" : "<title>Report</title>";
        const document = /<head[\s>]/i.test(html)
          ? html.replace(/<head\b[^>]*>/i, head => head + csp + title)
          : csp + title + html;
        await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(document)}`);
        return win.webContents.printToPDF({ printBackground: true, preferCSSPageSize: true,
          pageSize: "A4", margins: { top: 0, bottom: 0, left: 0, right: 0 }, displayHeaderFooter: false, generateTaggedPDF: true });
      })(),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("PDF rendering timed out.")), 45000); }),
    ]);
  } finally {
    clearTimeout(timer);
    win.destroy();
    await isolated.clearStorageData();
  }
}

async function startReportPdfService(electron) {
  const token = crypto.randomBytes(32).toString("hex");
  let busy = false;
  const server = http.createServer(async (request, response) => {
    if (request.method !== "POST" || request.url !== "/pdf" || request.headers.authorization !== `Bearer ${token}` || request.headers.origin) {
      response.writeHead(403); response.end("Forbidden"); return;
    }
    if (busy) { response.writeHead(503); response.end("Another PDF is rendering. Retry shortly."); return; }
    busy = true;
    try {
      const chunks = []; let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 2 * 1024 * 1024) throw new Error("Report HTML exceeds 2 MB.");
        chunks.push(chunk);
      }
      const html = Buffer.concat(chunks).toString("utf8");
      if (!/<html[\s>]/i.test(html)) throw new Error("Supply a complete HTML document.");
      const pdf = await renderPdf(html, electron);
      response.writeHead(200, { "Content-Type": "application/pdf" }); response.end(pdf);
    } catch (error) {
      response.writeHead(400); response.end(error.message);
    } finally { busy = false; }
  });
  server.requestTimeout = 60000;
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  server.unref();
  return { server, env: { RATICODE_REPORT_PDF_URL: `http://127.0.0.1:${server.address().port}/pdf`, RATICODE_REPORT_PDF_TOKEN: token } };
}
module.exports = { renderPdf, startReportPdfService };
