const test = require("node:test");
const assert = require("node:assert/strict");
const { canOpenInExternalBrowser } = require("../browser-utils.cjs");

test("external browser accepts local HTML documents and web URLs only", () => {
  for (const url of ["https://example.com", "http://localhost:3000", "file:///tmp/My%20Report.html#page-2", "file:///C:/Reports/DEMO.HTM", "file:///tmp/report%2Ehtml"]) {
    assert.equal(canOpenInExternalBrowser(url), true, url);
  }
  for (const url of ["about:blank", "data:text/html,test", "javascript:alert(1)", "file:///tmp/run.sh", "file:///C:/run.exe", "file:///tmp/report.html.exe", "file:///tmp/report.html/run", "file:///tmp/%zz.html", "invalid"]) {
    assert.equal(canOpenInExternalBrowser(url), false, url);
  }
});
