const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const { helpMenuTemplate } = require("../help-menu.cjs");

test("the macOS Help menu lists the window's Help items in the same order and sends each one to the window", () => {
  const sent = [];
  const menu = helpMenuTemplate((item) => sent.push(item));
  assert.equal(menu.role, "help");

  // The window's own Help menu, read from AppChrome.jsx.
  const chrome = fs.readFileSync(path.join(__dirname, "../../src/components/AppChrome.jsx"), "utf8");
  const helpBlock = chrome.match(/\{ label: "Help", items: \[([\s\S]*?)\] \}/)[1];
  const windowItems = [...helpBlock.matchAll(/\["(help\.[A-Za-z]+)", "([^"]+)"|null/g)]
    .map((match) => match[1] ? [match[1], match[2]] : null);
  assert.deepEqual(
    menu.submenu.map((item) => item.type === "separator" ? null : item.label),
    windowItems.map((item) => item && item[1]),
  );

  for (const item of menu.submenu) item.click?.();
  assert.deepEqual(sent, windowItems.filter(Boolean).map((item) => item[0]));
  assert.deepEqual(
    windowItems.filter(Boolean).map((item) => item[1]),
    ["Documentation", "Keyboard Shortcuts", "Troubleshooting", "Report a Problem...", "What's New", "Check for Updates...", "Settings", "About Raticode"],
  );
});
