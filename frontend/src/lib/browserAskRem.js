// Builds the attachment text for "Ask Rem about this" on a web page:
// the right-clicked element and selection first, then the page text.
export function askRemPageText({ selection = "", text = "", element = null } = {}) {
  const sections = [];
  if (element?.tag) {
    const details = [
      ["Element", `<${element.tag}>`],
      ["Location", element.path],
      ["Role", element.role],
      ["Label", element.label],
      ["Alt text", element.alt],
      ["Title", element.title],
      ["Link", element.href],
      ["Source", element.src],
    ].filter(([, value]) => value).map(([name, value]) => `${name}: ${value}`);
    if (element.text) details.push(`Text:\n${element.text}`);
    if (element.html) details.push(`HTML:\n${element.html}`);
    sections.push(`The user right-clicked this element and is asking about it:\n${details.join("\n")}`);
  }
  if (selection) sections.push(`Selected text:\n${selection}`);
  sections.push(sections.length ? `Page text:\n${text}` : text);
  return sections.join("\n\n");
}
