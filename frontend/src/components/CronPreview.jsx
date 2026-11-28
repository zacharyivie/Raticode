import { useEffect, useState } from "react";
import { triggersApi } from "../lib/rattishTriggers.js";
import { dateTime } from "../lib/timeText.js";

const EMPTY = { summary: "", nextRuns: [], error: "" };

// A cron expression in words, its next three times, or the parser's message, before Save.
// `describe` turns the words ("" for an unusual expression) into the line shown; return ""
// to show the Next line alone.
export function CronPreview({
  expression,
  timezone,
  describe = summary => summary,
  className = "text-xs leading-5 text-muted",
  errorClassName = "text-xs leading-5 text-amber-700 dark:text-amber-300",
}) {
  const [result, setResult] = useState(EMPTY);
  useEffect(() => {
    if (!expression.trim()) {
      setResult(EMPTY);
      return undefined;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      triggersApi.previewCron(expression, timezone).then(
        payload => !cancelled && setResult({ summary: payload.summary || "", nextRuns: payload.nextRuns || [], error: "" }),
        error => !cancelled && setResult({ ...EMPTY, error: error.message }),
      );
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [expression, timezone]);
  if (result.error) return <p className={errorClassName} data-testid="cron-preview-error">{result.error}</p>;
  if (!result.nextRuns.length) return null;
  const words = describe(result.summary);
  return (
    <div data-testid="cron-preview">
      {words ? <p className={className}>{words}</p> : null}
      <p className={className}>Next: {result.nextRuns.map(at => dateTime(at, { weekday: true })).join(", ")}</p>
    </div>
  );
}
