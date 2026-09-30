const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { canOpenInExternalBrowser } = require("../browser-utils.cjs");

test("external browser accepts local HTML documents and web URLs only", () => {
  for (const url of ["https://example.com", "http://localhost:3000", "file:///tmp/My%20Report.html#page-2", "file:///C:/Reports/DEMO.HTM", "file:///tmp/report%2Ehtml"]) {
    assert.equal(canOpenInExternalBrowser(url), true, url);
  }
  for (const url of ["about:blank", "data:text/html,test", "javascript:alert(1)", "file:///tmp/run.sh", "file:///C:/run.exe", "file:///tmp/report.html.exe", "file:///tmp/report.html/run", "file:///tmp/%zz.html", "invalid"]) {
    assert.equal(canOpenInExternalBrowser(url), false, url);
  }
});

test("external browser action opens the displayed local HTML URL", () => {
  const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  const actionSource = source.slice(source.indexOf("function browserAction("), source.indexOf("async function runBrowserOperation("));
  const calls = [];
  let url = "file:///tmp/My%20Report.html#details";
  const action = vm.runInNewContext(`${actionSource}\nbrowserAction`, {
    ownedBrowserSession: () => ({}),
    browserSessionContents: () => ({ getURL: () => url }),
    browserSessionState: () => ({}),
    canOpenInExternalBrowser,
    runBrowserOperation: (_session, operation) => operation(),
    shell: { openExternal: value => calls.push(value) },
  });
  action({}, { action: "open-external" });
  assert.deepEqual(calls, [url]);
  url = "file:///tmp/run.sh";
  action({}, { action: "open-external" });
  assert.equal(calls.length, 1);
});
