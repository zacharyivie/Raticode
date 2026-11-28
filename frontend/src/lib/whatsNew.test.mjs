import assert from "node:assert/strict";
import test from "node:test";

import { changelogSections, releasePageUrl } from "./whatsNew.js";

const SAMPLE = [
  "# Changelog",
  "",
  "Intro text.",
  "",
  "## 0.7.0 - 2026-12-01",
  "",
  "### Added",
  "",
  "- Seven.",
  "",
  "## 0.6.10 - 2026-11-30",
  "",
  "- Six ten.",
  "",
  "## Notes for maintainers",
  "",
  "- Not a release.",
  "",
  "## v0.6.2 - 2026-11-28",
  "",
  "- Six two.",
  "",
  "## 0.6.1 - 2026-11-26",
  "",
  "- Six one.",
].join("\n");

const versions = (sections) => sections.map((section) => section.version);

test("sections after the stored version up to the running one, compared as numbers, newest first", () => {
  assert.deepEqual(versions(changelogSections(SAMPLE, "0.6.1", "0.7.0")), ["0.7.0", "0.6.10", "0.6.2"]);
  assert.deepEqual(versions(changelogSections(SAMPLE, "0.6.1", "0.6.10")), ["0.6.10", "0.6.2"]);
  const [seven] = changelogSections(SAMPLE, "0.6.10", "0.7.0");
  assert.equal(seven.markdown, "## 0.7.0 - 2026-12-01\n\n### Added\n\n- Seven.");
});

test("a heading that isn't a version ends the section before it", () => {
  const [sixTen] = changelogSections(SAMPLE, "", "0.6.10");
  assert.equal(sixTen.markdown, "## 0.6.10 - 2026-11-30\n\n- Six ten.");
});

test("without a readable stored version only the running version's section is returned", () => {
  assert.deepEqual(versions(changelogSections(SAMPLE, "", "0.6.2")), ["0.6.2"]);
  assert.deepEqual(versions(changelogSections(SAMPLE, "garbage", "0.6.2")), ["0.6.2"]);
  assert.deepEqual(changelogSections(SAMPLE, "", "0.8.0"), []);
  assert.deepEqual(changelogSections(SAMPLE, "0.6.1", "not a version"), []);
});

test("release page links point at the version's tag", () => {
  assert.equal(releasePageUrl("0.6.1"), "https://github.com/zacharyivie/Raticode/releases/tag/v0.6.1");
  assert.equal(releasePageUrl("v0.6.1"), "https://github.com/zacharyivie/Raticode/releases/tag/v0.6.1");
});
