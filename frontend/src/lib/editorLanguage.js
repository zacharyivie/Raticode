export function languageForPath(path) {
  const name = String(path ?? "").split(/[\\/]/).pop().toLowerCase();
  const extension = name.includes(".") ? name.slice(name.lastIndexOf(".")) : "";
  const languages = {
    ".bash": "shell", ".c": "c", ".cc": "cpp", ".cpp": "cpp", ".cs": "csharp",
    ".cjs": "javascript", ".cts": "typescript", ".css": "css", ".go": "go", ".h": "c", ".hpp": "cpp", ".html": "html",
    ".htm": "html", ".ini": "ini", ".java": "java", ".js": "javascript", ".json": "json",
    ".jsonc": "json", ".jsx": "javascript", ".markdown": "markdown", ".md": "markdown",
    ".mdown": "markdown", ".mjs": "javascript", ".mkd": "markdown", ".php": "php",
    ".mts": "typescript", ".ps1": "powershell", ".py": "python", ".rb": "ruby", ".rs": "rust",
    ".rattish": "rattish",
    ".rad": "rattish",
    ".scss": "scss", ".sh": "shell", ".sql": "sql", ".svg": "xml", ".toml": "ini",
    ".ts": "typescript", ".tsx": "typescript", ".txt": "plaintext", ".xml": "xml",
    ".yaml": "yaml", ".yml": "yaml", ".zsh": "shell",
  };
  if (name === "dockerfile") return "dockerfile";
  return languages[extension] ?? "plaintext";
}

