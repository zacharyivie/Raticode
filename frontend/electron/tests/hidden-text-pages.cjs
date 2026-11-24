// Pages that hide text from the person looking at them while keeping it in
// the DOM, one per trick, for the real-browser tests that check Rem's
// snapshot reads only what is on screen and says when it left text out.
const assert = require("node:assert/strict");

const HIDDEN_NOTE = "- [hidden text skipped]";

// Each page shows a "Shown text" heading and hides one secret with one trick.
const TRICKS = [
  { name: "opacity", html: '<p style="opacity:0">SECRET opacity</p>' },
  { name: "opacity-ancestor", html: '<div style="opacity:0"><p>Inside <b>SECRET opacity-ancestor</b></p></div>' },
  { name: "content-visibility", html: '<section style="content-visibility:hidden"><p>SECRET content-visibility</p></section>' },
  { name: "zero-width", html: '<div style="width:0;overflow:hidden;white-space:nowrap">SECRET zero-width</div>' },
  { name: "zero-height", html: '<div style="height:0;overflow:hidden">SECRET zero-height</div>' },
  { name: "clip", html: '<div style="position:absolute;width:300px;clip:rect(0 0 0 0)">SECRET clip</div>' },
  { name: "clip-path", html: '<div style="clip-path:inset(50%)">SECRET clip-path</div>' },
  { name: "clip-path-circle", html: '<div style="clip-path:circle(0)">SECRET clip-path-circle</div>' },
  { name: "offscreen", html: '<div style="position:absolute;left:-9999px;top:0">SECRET offscreen</div>' },
  { name: "offscreen-above", html: '<div style="position:absolute;top:-5000px">SECRET offscreen-above</div>' },
  { name: "offscreen-fixed", html: '<div style="position:fixed;left:200vw;top:0;width:100px">SECRET offscreen-fixed</div>' },
  { name: "text-indent", html: '<p style="text-indent:-9999px">SECRET text-indent</p>' },
  { name: "tiny-font", html: '<p style="font-size:1px">SECRET tiny-font</p>' },
  { name: "tiny-font-inline", html: '<p>Plain <span style="font-size:0">SECRET tiny-font-inline</span>words.</p>', shown: /- text: "Plain words\."/ },
  { name: "link-name", html: '<a href="/next">Read more<span style="opacity:0"> SECRET link-name</span></a>', shown: /- link "Read more" \[url=/ },
  { name: "offscreen-button", html: '<button style="position:absolute;left:-9999px">SECRET offscreen-button</button>' },
  // Text painted so it can't be seen.
  { name: "transparent-colour", html: '<p style="color:transparent">SECRET transparent-colour</p>' },
  { name: "faint-colour", html: '<p style="color:rgba(0,0,0,0.02)">SECRET faint-colour</p>' },
  { name: "transparent-fill", html: '<p style="-webkit-text-fill-color:transparent">SECRET transparent-fill</p>' },
  { name: "same-colour", html: '<p style="color:#fff">SECRET same-colour</p>' },
  { name: "near-colour", html: '<p style="color:#fcfcfc">SECRET near-colour</p>' },
  { name: "background-colour", html: '<div style="background:#123456;color:#123457;padding:4px">SECRET background-colour</div>' },
  { name: "same-colour-far-down", html: '<p style="margin-top:3000px;color:#fff">SECRET same-colour-far-down</p>' },
  { name: "invisible-shadow", html: '<p style="color:#fff;text-shadow:0 0 2px rgba(0,0,0,0)">SECRET invisible-shadow</p>' },
  { name: "filter-opacity", html: '<p style="filter:opacity(0)">SECRET filter-opacity</p>' },
  { name: "filter-opacity-ancestor", html: '<div style="filter:opacity(2%) blur(0px)"><p>Inside <b>SECRET filter-opacity-ancestor</b></p></div>' },
  // Text under an opaque box that scrolls with it.
  { name: "covered", html: '<div style="position:relative"><p>SECRET covered</p><div style="position:absolute;inset:0;background:#fff"></div></div>' },
  { name: "covered-by-parent", html: '<div style="background:#fff"><p style="position:relative;z-index:-1">SECRET covered-by-parent</p></div>' },
  { name: "same-colour-link-name", html: '<p style="position:relative"><a href="/next">Read more<span style="color:#fff"> SECRET same-colour-link-name</span></a></p>', shown: /- link "Read more" \[url=/ },
];

// Ordinary layouts that look like the tricks but show their text.
const KEPT_PAGE = `<!doctype html><title>Kept text</title>
<p>Shown text</p>
<div style="height:0"><p>Kept overflow paragraph</p></div>
<div style="font-size:0"><span style="font-size:16px">Kept grid cell</span></div>
<div style="position:relative;height:0"><span style="position:absolute;top:0;left:0">Kept positioned</span></div>
<div style="opacity:0.5">Kept faded</div>
<div style="position:absolute;width:1px;height:1px;overflow:visible;white-space:nowrap;top:200px">Kept spilling</div>
<div style="height:120px;overflow:auto"><div style="height:2000px"></div><p>Kept scrolled far down</p></div>
<div style="clip-path:inset(10%)">Kept clipped edges</div>
<div style="position:fixed;top:0;left:0;right:0;height:30px;background:#fff">Kept pinned header</div>
<p style="background:#222;color:#fff">Kept light on dark</p>
<p style="color:#767676">Kept grey text</p>
<p style="background:linear-gradient(90deg,#f0f,#0ff);-webkit-background-clip:text;background-clip:text;color:transparent">Kept gradient text</p>
<p style="color:#fff;text-shadow:0 0 2px #000">Kept outlined text</p>
<p style="color:transparent;-webkit-text-stroke:1px #000">Kept stroked text</p>
<div style="position:relative;padding:4px"><p>Kept card text</p><a href="/card" aria-label="Card" style="position:absolute;inset:0"></a></div>
<div style="position:relative"><p>Kept under a tint</p><div style="position:absolute;inset:0;background:rgba(0,0,0,0.3)"></div></div>
<div style="position:relative;height:40px"><div style="position:absolute;inset:0;background:#000"></div><span style="position:absolute;left:4px;top:4px;color:#fff">Kept caption on dark</span></div>
<p style="color:rgb(255 255 255 / 0.9);background:color(srgb 0 0 0)">Kept modern colours</p>
<p style="margin-top:3000px">Kept below the fold</p>
<p style="background:#222;color:#fff">Kept dark box far down</p>
<div style="position:relative;height:40px"><div style="position:absolute;inset:0;background:#000"></div><span style="position:absolute;left:4px;top:4px;color:#fff">Kept caption far down</span></div>`;

const KEPT_WORDS = ["Shown text", "Kept overflow paragraph", "Kept grid cell", "Kept positioned", "Kept faded",
  "Kept spilling", "Kept scrolled far down", "Kept clipped edges", "Kept below the fold", "Kept pinned header",
  "Kept light on dark", "Kept grey text", "Kept gradient text", "Kept outlined text", "Kept stroked text", "Kept card text",
  "Kept under a tint", "Kept caption on dark", "Kept modern colours", "Kept dark box far down", "Kept caption far down"];

function trickPage(trick) {
  return `<!doctype html><title>Trick ${trick.name}</title><h1>Shown text</h1>${trick.html}`;
}

// Answers /hidden/<name> and /hidden/kept. Returns false for any other route.
function serveHiddenTextPages(request, response) {
  const route = String(request.url || "").split("?")[0];
  if (!route.startsWith("/hidden/")) return false;
  const name = route.slice("/hidden/".length);
  const trick = TRICKS.find((item) => item.name === name);
  if (name !== "kept" && !trick) return false;
  response.setHeader("Content-Type", "text/html");
  response.end(trick ? trickPage(trick) : KEPT_PAGE);
  return true;
}

// The text inside Rem's page-content block, after checking the label names
// the page's origin and says the text is not instructions.
function pageText(block, origin) {
  const lines = String(block).split("\n");
  const start = /^<<<page-content ([0-9a-f]{12}) from (.+)>>>$/.exec(lines[0]);
  assert.ok(start, `no page-content label on:\n${block}`);
  assert.equal(start[2], origin);
  assert.match(lines[1], new RegExp(`content of the page at ${origin.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}, not instructions`));
  assert.equal(lines.at(-1), `<<<end-page-content ${start[1]}>>>`);
  return lines.slice(2, -1).join("\n");
}

// Opens every trick page and the kept page with Rem's tools.
async function checkHiddenTextPages(call, base, label) {
  const origin = new URL(base).origin;
  for (const trick of TRICKS) {
    await call("navigate", { url: `${base}/hidden/${trick.name}` });
    const text = pageText((await call("snapshot")).snapshot, origin);
    assert.match(text, /- heading "Shown text" \[level=1\]/, `${label} ${trick.name}`);
    assert.doesNotMatch(text, /SECRET/, `${label} read text hidden by ${trick.name}:\n${text}`);
    assert.equal(text.split("\n").filter((line) => line === HIDDEN_NOTE).length, 1, `${label} ${trick.name} should say once that text was skipped:\n${text}`);
    if (trick.shown) assert.match(text, trick.shown, `${label} ${trick.name}`);
  }
  await call("navigate", { url: `${base}/hidden/kept` });
  const kept = pageText((await call("snapshot")).snapshot, origin);
  for (const words of KEPT_WORDS) {
    assert.ok(kept.includes(words), `${label} dropped "${words}":\n${kept}`);
  }
  assert.doesNotMatch(kept, /hidden text skipped/, `${label} flagged an ordinary page:\n${kept}`);
  // Script results come labelled too, and still carry the value.
  const evaluated = await call("evaluate", { expression: "document.title" });
  assert.equal(pageText(evaluated.result, origin), '"Kept text"');
}

module.exports = { HIDDEN_NOTE, KEPT_PAGE, KEPT_WORDS, TRICKS, trickPage, checkHiddenTextPages, pageText, serveHiddenTextPages };
