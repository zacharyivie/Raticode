// Map normalized search offsets back to the original text, including ligatures
// and combining characters. Highlighting must use the same normalization as storage.
export function searchMatchOffsets(text, query) {
  const needle = String(query ?? "").normalize("NFKC").toLowerCase().trim();
  if (!needle) return [];
  const normalized = text.normalize("NFKC").toLowerCase();
  const offsets = [];
  for (const { segment, index } of new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(text)) {
    const value = segment.normalize("NFKC").toLowerCase();
    for (let i = 0; i < value.length; i++) offsets.push([index, index + segment.length]);
  }
  const matches = [];
  for (let position = normalized.indexOf(needle); position >= 0; position = normalized.indexOf(needle, position + needle.length)) {
    matches.push([offsets[position][0], offsets[position + needle.length - 1][1]]);
  }
  return matches;
}

export function highlightSearchMatch(element, query) {
  const nodes = [];
  let text = "";
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.parentElement.closest("button, [aria-hidden='true']")) continue;
    nodes.push({ node, start: text.length, end: text.length + node.length });
    text += node.textContent;
  }
  let firstIndex = 0;
  let lastIndex = 0;
  const ranges = searchMatchOffsets(text, query).map(([start, end]) => {
    while (nodes[firstIndex].end <= start) firstIndex++;
    lastIndex = Math.max(firstIndex, lastIndex);
    while (nodes[lastIndex].end < end) lastIndex++;
    const first = nodes[firstIndex];
    const last = nodes[lastIndex];
    const range = document.createRange();
    range.setStart(first.node, start - first.start);
    range.setEnd(last.node, end - last.start);
    return range;
  });
  if (globalThis.CSS?.highlights && globalThis.Highlight) CSS.highlights.set("rem-thread-match", new globalThis.Highlight(...ranges));
  return ranges;
}
