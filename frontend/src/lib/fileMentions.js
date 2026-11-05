// Full file paths in Rem's text become links. The backend finds them by checking the
// filesystem (paths may contain spaces), so this only marks the text it named. Code
// blocks and existing links keep their text; inline code that is exactly a path links.
const SKIPPED = new Set(["link", "linkReference", "definition", "code", "html", "image", "imageReference"]);
// A mention must start where a path can start, not partway through a longer path.
const STARTS_AFTER = /[\s([{<"'`*_]/;

export function remarkFileMentions(mentions = []) {
  const known = mentions
    .filter(item => typeof item?.text === "string" && item.text && typeof item.path === "string" && item.path)
    .sort((a, b) => b.text.length - a.text.length);
  return () => tree => {
    if (known.length) linkChildren(tree, known);
  };
}

// The destination opens the same file however it's spelled; each name is encoded so
// spaces, "#" and "?" survive the trip through the Markdown link.
export function fileMentionHref(path) {
  return String(path).split("/").map(encodeURIComponent).join("/");
}

function linkChildren(node, known) {
  if (!Array.isArray(node.children)) return;
  const children = [];
  for (const child of node.children) {
    if (child.type === "text") children.push(...splitText(child.value, known));
    else if (child.type === "inlineCode") {
      const mention = known.find(item => item.text === child.value.trim());
      children.push(mention ? linkNode(mention, [child]) : child);
    } else {
      if (!SKIPPED.has(child.type)) linkChildren(child, known);
      children.push(child);
    }
  }
  node.children = children;
}

function splitText(value, known) {
  const parts = [];
  let from = 0;
  let cursor = 0;
  while (cursor < value.length) {
    const found = nextMention(value, cursor, known);
    if (!found) break;
    if (found.index > from) parts.push({ type: "text", value: value.slice(from, found.index) });
    parts.push(linkNode(found.mention, [{ type: "text", value: found.mention.text }]));
    from = cursor = found.index + found.mention.text.length;
  }
  if (from < value.length || !parts.length) parts.push({ type: "text", value: value.slice(from) });
  return parts;
}

function nextMention(value, cursor, known) {
  let best = null;
  for (const mention of known) {
    let index = value.indexOf(mention.text, cursor);
    while (index >= 0 && index > 0 && !STARTS_AFTER.test(value[index - 1])) index = value.indexOf(mention.text, index + 1);
    // Longer mentions come first, so a tie keeps the longer one.
    if (index >= 0 && (!best || index < best.index)) best = { index, mention };
  }
  return best;
}

function linkNode(mention, children) {
  return { type: "link", url: fileMentionHref(mention.path), title: mention.path, children };
}
