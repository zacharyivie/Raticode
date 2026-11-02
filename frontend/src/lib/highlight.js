// A small, dependency-free code tokenizer for chat code blocks: comments, strings,
// numbers and keywords. It never runs code and never builds HTML; the renderer turns
// tokens into spans. The phone uses the same rules (CodeHighlight.kt).

const C_LIKE = "break case catch class const continue default do else enum export extends false finally for function if import in instanceof interface let new null return static super switch this throw true try typeof var void while yield async await of";
const KEYWORDS = {
  js: C_LIKE,
  ts: `${C_LIKE} type implements private public protected readonly namespace declare as`,
  py: "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield self",
  kt: "as break class continue do else false for fun if in interface is null object package return super this throw true try typealias val var when while data sealed internal private public override companion import",
  java: "abstract boolean break byte case catch char class continue default do double else enum extends final finally float for if implements import instanceof int interface long new null package private protected public return short static super switch this throw true false try void while",
  go: "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false",
  rust: "as break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while async await dyn",
  sh: "if then else elif fi for while do done case esac in function return export local echo cd exit set unset",
  sql: "select from where and or not insert into values update set delete create table drop alter join left right inner outer on group by order limit as distinct null is in like having union",
  css: "",
  json: "true false null",
  yaml: "true false null yes no",
};
const ALIASES = {
  javascript: "js", jsx: "js", mjs: "js", cjs: "js", typescript: "ts", tsx: "ts", python: "py", kotlin: "kt", kts: "kt",
  golang: "go", rs: "rust", bash: "sh", shell: "sh", zsh: "sh", fish: "sh", console: "sh", yml: "yaml", html: "html", xml: "html", toml: "yaml", ini: "yaml",
};
const HASH_COMMENTS = new Set(["py", "sh", "yaml"]);
const MAX_HIGHLIGHT = 20000;

export function codeLanguage(className = "") {
  const name = /language-([\w+-]+)/.exec(String(className))?.[1]?.toLowerCase() || "";
  return ALIASES[name] || name;
}

export function canHighlight(language) {
  return language in KEYWORDS || language === "html";
}

// [{ type: "comment"|"string"|"number"|"keyword"|"tag"|"text", text }]
export function tokenize(code, language) {
  const text = String(code ?? "");
  if (!canHighlight(language) || text.length > MAX_HIGHLIGHT) return [{ type: "text", text }];
  if (language === "html") return tokenizeHtml(text);
  const keywords = new Set((KEYWORDS[language] || "").split(" ").filter(Boolean));
  const hash = HASH_COMMENTS.has(language);
  const slash = !["py", "sh", "yaml", "json", "sql"].includes(language);
  const dash = language === "sql";
  const tokens = [];
  const push = (type, value) => {
    const last = tokens.at(-1);
    if (last && last.type === type && type === "text") last.text += value;
    else tokens.push({ type, text: value });
  };
  let at = 0;
  while (at < text.length) {
    const rest = text.slice(at);
    let match = null;
    if (slash && rest.startsWith("//")) match = ["comment", /^\/\/[^\n]*/.exec(rest)[0]];
    else if (slash && rest.startsWith("/*")) match = ["comment", (/^\/\*[\s\S]*?\*\//.exec(rest) || [rest])[0]];
    else if (hash && rest[0] === "#") match = ["comment", /^#[^\n]*/.exec(rest)[0]];
    else if (dash && rest.startsWith("--")) match = ["comment", /^--[^\n]*/.exec(rest)[0]];
    else if (language === "py" && /^("""|''')/.test(rest)) {
      const quote = rest.slice(0, 3);
      const end = rest.indexOf(quote, 3);
      match = ["string", end < 0 ? rest : rest.slice(0, end + 3)];
    } else if (/^["'`]/.test(rest)) match = ["string", stringAt(rest)];
    else if (/^\d/.test(rest) || (/^\.\d/.test(rest))) match = ["number", /^(?:0x[\da-f]+|\d[\d_]*\.?\d*(?:e[+-]?\d+)?|\.\d+)/i.exec(rest)[0]];
    else if (/^[A-Za-z_$]/.test(rest)) {
      const word = /^[A-Za-z_$][\w$]*/.exec(rest)[0];
      match = [keywords.has(language === "sql" ? word.toLowerCase() : word) ? "keyword" : "text", word];
    }
    if (match && match[1]) {
      push(match[0], match[1]);
      at += match[1].length;
    } else {
      push("text", text[at]);
      at += 1;
    }
  }
  return tokens;
}

function stringAt(rest) {
  const quote = rest[0];
  let index = 1;
  while (index < rest.length) {
    if (rest[index] === "\\") index += 2;
    else if (rest[index] === quote) return rest.slice(0, index + 1);
    else if (rest[index] === "\n" && quote !== "`") return rest.slice(0, index);
    else index += 1;
  }
  return rest;
}

function tokenizeHtml(text) {
  const tokens = [];
  const pattern = /(<!--[\s\S]*?-->)|(<\/?[A-Za-z][^>]*>)/g;
  let at = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > at) tokens.push({ type: "text", text: text.slice(at, match.index) });
    tokens.push({ type: match[1] ? "comment" : "tag", text: match[0] });
    at = match.index + match[0].length;
  }
  if (at < text.length) tokens.push({ type: "text", text: text.slice(at) });
  return tokens;
}
