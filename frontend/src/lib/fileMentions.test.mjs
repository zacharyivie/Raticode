import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { isLocalFileLink, resolveMarkdownLinkPath } from "./fileLinks.js";
import { fileMentionHref, remarkFileMentions } from "./fileMentions.js";

const video = "/home/me/My Videos/dock duplicate you know.MP4";
const report = "/home/me/second-brain/reports/plan.html";
const mentions = [{ text: video, path: video }, { text: report, path: report }];

function render(markdown, found = mentions) {
  return renderToStaticMarkup(h(ReactMarkdown, {
    remarkPlugins: [remarkGfm, remarkFileMentions(found)],
    urlTransform: url => url,
  }, markdown));
}

test("full paths Rem writes become links, spaces included", () => {
  const html = render(`The video is at ${video}, and the plan is **${report}**.`);
  assert.match(html, /<a href="\/home\/me\/My%20Videos\/dock%20duplicate%20you%20know\.MP4" title="[^"]+">\/home\/me\/My Videos\/dock duplicate you know\.MP4<\/a>, and/);
  assert.match(html, /<strong><a href="\/home\/me\/second-brain\/reports\/plan\.html"[^>]*>\/home\/me\/second-brain\/reports\/plan\.html<\/a><\/strong>/);
});

test("inline code that is exactly a path links; code blocks and existing links don't change", () => {
  const html = render([
    `Open \`${report}\` or [the plan](https://example.com/plan).`,
    "",
    "```",
    report,
    "```",
    "",
    `[my label](<${video}>)`,
  ].join("\n"));
  assert.match(html, /<a href="\/home\/me\/second-brain\/reports\/plan\.html"[^>]*><code>\/home\/me\/second-brain\/reports\/plan\.html<\/code><\/a>/);
  assert.match(html, /<pre><code>\/home\/me\/second-brain\/reports\/plan\.html\n<\/code><\/pre>/);
  assert.match(html, /<a href="https:\/\/example\.com\/plan">the plan<\/a>/);
  assert.match(html, /<a href="\/home\/me\/My%20Videos\/dock%20duplicate%20you%20know\.MP4">my label<\/a>/);
  assert.equal((html.match(/<a /g) || []).length, 3);
});

test("a path inside a longer path, or text with no mentions, stays plain", () => {
  assert.doesNotMatch(render(`Copied to /backup${report} only.`), /<a /);
  assert.equal(render(`Nothing here: ${report}`, []), `<p>Nothing here: ${report}</p>`);
  assert.equal(render(`Nothing here: ${report}`, [{ text: "", path: report }, null]), `<p>Nothing here: ${report}</p>`);
});

test("a clicked mention opens exactly the file it names", () => {
  for (const path of [video, "/home/me/odd #1 ?.txt", "/home/me/100% done.md"]) {
    const href = fileMentionHref(path);
    assert.equal(isLocalFileLink(href), true);
    assert.equal(resolveMarkdownLinkPath("", href), path);
  }
});
