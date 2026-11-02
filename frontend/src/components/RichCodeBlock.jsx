// A fenced code block with a language: a ```rem-chart chart, highlighted code, or
// the plain block when neither applies. Loaded on demand by MarkdownContent.
import { HighlightedCode, RemChart } from "./RichCode.jsx";
import { canHighlight, codeLanguage } from "../lib/highlight.js";
import { parseRemChart } from "../lib/remChart.js";

export default function RichCodeBlock({ code, fallback, Frame, inverse, language }) {
  // Inverse text sits on a colored bubble, where token colors wouldn't stay readable.
  if (inverse) return fallback;
  if (language === "rem-chart") {
    const chart = parseRemChart(code);
    return chart ? <RemChart chart={chart} /> : fallback;
  }
  const name = codeLanguage(`language-${language}`);
  if (!canHighlight(name)) return fallback;
  return <Frame copyText={code} inverse={inverse}><HighlightedCode code={code} language={name} /></Frame>;
}
