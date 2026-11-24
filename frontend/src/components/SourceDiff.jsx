// A unified diff, added lines green and removed lines red. The test sets' Show source
// change view and a trigger's Review changes share it.
export function diffLineTone(line) {
  if (line.startsWith("+") && !line.startsWith("+++")) return "text-emerald-700 dark:text-emerald-300";
  if (line.startsWith("-") && !line.startsWith("---")) return "text-red-700 dark:text-red-300";
  return "text-muted";
}

export function SourceDiff({ className = "", diff, emptyText = "The workflow source is the same.", label }) {
  return (
    <pre
      aria-label={label}
      className={`workflow-scrollbar overflow-auto bg-slate-50 px-3 py-2 font-mono text-[11px] leading-4 ${className}`}
    >
      {(diff || emptyText).split("\n").map((line, index) => (
        <span key={index} className={`block ${diffLineTone(line)}`}>{line || " "}</span>
      ))}
    </pre>
  );
}
