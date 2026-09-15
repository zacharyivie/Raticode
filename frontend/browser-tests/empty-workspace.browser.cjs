/* global document, getComputedStyle, localStorage, requestAnimationFrame, window */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

module.exports = async function exerciseEmptyWorkspace({ windowRef, evaluate, waitFor }) {
  await evaluate(() => localStorage.setItem("raticode.studioSession.v1", JSON.stringify({
    projectRoot: `/workspace/${"long_project_name_".repeat(12)}`,
    view: "graph",
    workflowId: "",
  })));
  await windowRef.reload();
  await waitFor(() => evaluate(() => Boolean(document.querySelector(".empty-workspace-panel"))));
  assert.equal(await evaluate(() => document.querySelector(".empty-import-zone strong")?.textContent), "long_project_name_".repeat(12));

  async function setTheme(theme) {
    await evaluate(theme => document.querySelector(`button[title='${theme === "dark" ? "Dark" : "Light"} mode']`)?.click(), theme);
    await waitFor(() => evaluate(theme => document.documentElement.classList.contains("dark") === (theme === "dark"), theme));
  }

  async function checkLayout(label) {
    // Allow resize observers and the browser's container queries to settle.
    await evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const layout = await evaluate(() => {
      const root = document.querySelector(".empty-workspace");
      root.scrollTop = 0;
      const bounds = root.getBoundingClientRect();
      const panel = root.querySelector(".empty-workspace-panel");
      const zone = root.querySelector(".empty-import-zone");
      const button = zone.querySelector("button").getBoundingClientRect();
      const copy = zone.querySelector("div").getBoundingClientRect();
      const overflow = [...panel.querySelectorAll("*")].filter(element => {
        if (!element.getClientRects().length) return false;
        const rect = element.getBoundingClientRect();
        return rect.left < bounds.left - 1 || rect.right > bounds.right + 1;
      }).map(element => element.outerHTML.slice(0, 200));
      const heading = root.querySelector("h2").getBoundingClientRect();
      const featureColumns = getComputedStyle(root.querySelector(".empty-workspace-features")).gridTemplateColumns.split(" ").length;
      const lastFeature = root.querySelector(".empty-workspace-features").lastElementChild;
      root.scrollTop = root.scrollHeight;
      const lastRect = lastFeature.getBoundingClientRect();
      return {
        width: bounds.width,
        overflow,
        horizontalOverflow: root.scrollWidth > root.clientWidth + 1,
        headingReachable: heading.top >= bounds.top && heading.top < bounds.bottom,
        lastFeatureReachable: lastRect.bottom <= bounds.bottom + 1,
        importOverlap: button.left < copy.right && button.right > copy.left && button.top < copy.bottom && button.bottom > copy.top,
        featureColumns,
      };
    });
    if (layout.overflow.length) fs.writeFileSync(path.join(os.tmpdir(), "raticode-empty-workspace-failure.png"), (await windowRef.webContents.capturePage()).toPNG());
    assert.deepEqual(layout.overflow, [], `${label}, pane ${layout.width}px: content stays inside the pane`);
    assert.equal(layout.horizontalOverflow, false, `${label}: no horizontal scrolling`);
    assert.equal(layout.headingReachable, true, `${label}: heading reachable at scroll start`);
    assert.equal(layout.lastFeatureReachable, true, `${label}: last feature reachable at scroll end`);
    assert.equal(layout.importOverlap, false, `${label}: import button does not overlap text`);
    if (layout.width < 860) assert.equal(layout.featureColumns, 1, `${label}: narrow features stack`);
    return layout;
  }

  for (const [width, height, zoom] of [[1440, 900, 1], [1024, 768, 1], [1280, 720, 1], [1450, 1020, 2], [1024, 768, 2]]) {
    windowRef.setContentSize(width, height);
    windowRef.webContents.setZoomFactor(zoom);
    await waitFor(() => evaluate(({ width, height, zoom }) => Math.abs(window.innerWidth - width / zoom) < 2 && Math.abs(window.innerHeight - height / zoom) < 2, { width, height, zoom }));
    await checkLayout(`${width}x${height} at ${zoom * 100}% zoom`);
  }

  // Isolate available pane width from viewport width, as with resized sidebars.
  windowRef.setContentSize(1600, 1000);
  windowRef.webContents.setZoomFactor(1);
  await waitFor(() => evaluate(() => window.innerWidth === 1600));
  for (const theme of ["light", "dark"]) {
    await setTheme(theme);
    for (const width of [280, 360, 440, 720, 960]) {
      await evaluate(width => {
        const root = document.querySelector(".empty-workspace");
        root.style.width = `${width}px`;
        root.style.maxWidth = "none";
      }, width);
      const layout = await checkLayout(`${theme} pane at ${width}px`);
      if (width === 960) assert.equal(layout.featureColumns, 3, "Wide pane uses three readable feature columns");
    }
  }

  // Capture a typical project after exercising the pathological long name.
  await evaluate(() => localStorage.setItem("raticode.studioSession.v1", JSON.stringify({
    projectRoot: "/workspace/gofer-flow", view: "graph", workflowId: "",
  })));
  await windowRef.reload();
  await waitFor(() => evaluate(() => Boolean(document.querySelector(".empty-workspace-panel"))));
  for (const theme of ["light", "dark"]) {
    await setTheme(theme);
    await evaluate(() => {
      document.querySelector(".empty-workspace").style.width = "440px";
    });
    await checkLayout(`${theme} typical project`);
    await evaluate(async () => {
      const root = document.querySelector(".empty-workspace");
      root.scrollTop = 0;
      await Promise.all(root.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => {})));
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    fs.writeFileSync(path.join(os.tmpdir(), `raticode-empty-workspace-${theme}.png`), (await windowRef.webContents.capturePage()).toPNG());
  }
};
