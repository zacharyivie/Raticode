// A page whose iframe never finishes loading, for the real-browser tests that
// check Rem's browser actions return once the main document is ready instead
// of waiting for every part of the page.
const assert = require("node:assert/strict");

const SLOW_PAGE = `<!doctype html><title>Slow frame</title>
<h1>Slow frame</h1>
<button onclick="document.title='clicked'">Click me</button>
<a href="/slow-next">Next slow page</a>
<iframe src="/never" title="Never finishes"></iframe>`;
const NEXT_PAGE = `<!doctype html><title>Next slow page</title>
<h1>Next slow page</h1>
<iframe src="/never" title="Never finishes"></iframe>`;

function createSlowFramePages() {
  const hanging = new Set();
  return {
    // Answers the slow pages' routes. Returns false for any other route.
    serve(request, response) {
      const route = String(request.url || "").split("?")[0];
      if (route === "/slow" || route === "/slow-next") {
        response.setHeader("Content-Type", "text/html");
        response.end(route === "/slow" ? SLOW_PAGE : NEXT_PAGE);
        return true;
      }
      if (route === "/never") {
        // Starts the frame's document and never finishes it.
        response.writeHead(200, { "Content-Type": "text/html", "Cache-Control": "no-store" });
        response.write("<!doctype html><body><p>Still loading");
        hanging.add(response);
        response.on("close", () => hanging.delete(response));
        return true;
      }
      return false;
    },
    // Ends the frames that are still loading so the test server can close.
    stop() {
      for (const response of hanging) response.destroy();
      hanging.clear();
    },
  };
}

// Opens the slow page through Rem's browser actions and checks that open,
// a click that stays on the page, and a click that loads another slow page
// all return without waiting for the frame.
async function checkSlowFramePage(call, base, label) {
  let started = Date.now();
  const opened = await call("open", { url: `${base}/slow` });
  const openMs = Date.now() - started;
  assert.ok(openMs < 5000, `${label}: open took ${openMs} ms on a page with a frame that never finishes`);
  assert.equal(opened.title, "Slow frame", label);
  assert.equal(opened.loading, true, `${label}: the page should report that its frame is still loading`);
  assert.match(opened.note, /snapshot/i, label);

  const { snapshot } = await call("snapshot");
  const ref = (pattern) => {
    const match = snapshot.match(pattern);
    assert.ok(match, `${label}: ${pattern} is missing from the snapshot:\n${snapshot}`);
    return match[1];
  };
  started = Date.now();
  const clicked = await call("click", { ref: ref(/- button "Click me" \[ref=(e\d+)\]/) });
  const clickMs = Date.now() - started;
  assert.ok(clickMs < 2000, `${label}: a click took ${clickMs} ms on a page with a frame that never finishes`);
  assert.equal(clicked.title, "clicked", label);

  started = Date.now();
  const next = await call("click", { ref: ref(/- link "Next slow page" .*\[ref=(e\d+)\]/) });
  const navigateMs = Date.now() - started;
  assert.ok(navigateMs < 5000, `${label}: a click that opened another slow page took ${navigateMs} ms`);
  assert.equal(next.title, "Next slow page", label);
  assert.equal(next.loading, true, label);
  await call("close");
  console.log(`${label}: open ${openMs} ms, click ${clickMs} ms, click to a new page ${navigateMs} ms.`);
}

module.exports = { checkSlowFramePage, createSlowFramePages };
