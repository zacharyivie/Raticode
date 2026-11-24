import { useId, useMemo, useState } from "react";
import { formatUsageNumber } from "../lib/usage.js";
import { bucketLabel, compactTokens, loadUsageSplit, saveUsageSplit, seriesStyleVars, sharePercent, tokenChartModel, USAGE_SPLITS } from "../lib/usageHistory.js";

const BUCKET_WORDS = { hour: "hour", day: "day", week: "week", month: "month", year: "year" };

/** Reported tokens per time bucket, one stacked segment per provider or per account. */
export default function TokenUsageChart({ series }) {
  const [split, setSplit] = useState(() => loadUsageSplit());
  const model = useMemo(() => tokenChartModel(series, split), [series, split]);
  const [active, setActive] = useState(null);
  const [hovered, setHovered] = useState(null);
  const id = useId();
  const styles = Object.fromEntries(model.legend.map(entry => [entry.key, entry.style]));
  if (!model.bars.length) return null;
  const word = BUCKET_WORDS[model.kind] || "period";
  const who = model.split === "account" ? "account" : "provider";
  const bar = active === null ? null : model.bars[active];
  function choose(next) {
    setSplit(saveUsageSplit(next));
    setHovered(null);
  }
  function move(event) {
    const last = model.bars.length - 1;
    const current = active ?? -1;
    const next = { ArrowRight: current + 1, ArrowLeft: current - 1, Home: 0, End: last }[event.key];
    if (next === undefined) {
      if (event.key === "Escape") setActive(null);
      return;
    }
    event.preventDefault();
    setActive(Math.max(0, Math.min(last, next < 0 ? last : next)));
  }
  const align = active === null ? "center" : active < model.bars.length / 4 ? "start" : active > (3 * model.bars.length) / 4 ? "end" : "center";
  return <figure className="usage-chart" aria-labelledby={`${id}-title`}>
    <div className="usage-chart-head">
      <div id={`${id}-title`} className="usage-chart-title">Reported tokens per {word}<span>{model.empty ? `No ${who} reported tokens in this period.` : `Each bar is one ${word}. Its pieces show each ${who}'s share.`}</span></div>
      <SplitSwitch split={split} onChange={choose} />
    </div>
    <div className="usage-chart-frame">
      <div className="usage-chart-axis" aria-hidden="true">{[...model.ticks].reverse().map(tick => <span key={tick}>{compactTokens(tick)}</span>)}</div>
      <div className="usage-chart-plot">
        <div className="usage-chart-grid" aria-hidden="true">{model.ticks.slice(1).map(tick => <span key={tick} style={{ bottom: `${100 * tick / (model.max || 1)}%` }} />)}</div>
        <div
          className="usage-chart-bars" role="group" tabIndex={0}
          aria-label={`Tokens per ${word} by ${who}, ${model.bars.length} bars. Use the left and right arrow keys to read each ${word}.`}
          aria-describedby={`${id}-detail`}
          onKeyDown={move} onFocus={() => setActive(current => current ?? model.bars.length - 1)} onBlur={() => { setActive(null); setHovered(null); }}
          onMouseLeave={() => { setActive(null); setHovered(null); }}
        >
          {model.bars.map((item, index) => <div
            key={item.key} className="usage-chart-column" data-active={active === index || undefined}
            onMouseEnter={() => setActive(index)}
          >
            <div className="usage-chart-stack" style={{ height: `${model.max ? 100 * item.total / model.max : 0}%` }}>
              {item.segments.map(segment => <span
                key={segment.key} className="usage-chart-segment" data-pattern={styles[segment.key]?.pattern}
                data-hovered={(active === index && hovered === segment.key) || undefined}
                style={{ flexGrow: segment.share, ...seriesStyleVars(styles[segment.key]) }}
                onMouseEnter={() => setHovered(segment.key)}
              />)}
            </div>
          </div>)}
        </div>
        {bar ? <div className="usage-chart-tooltip" data-align={align} style={{ left: `${100 * (active + 0.5) / model.bars.length}%` }} aria-hidden="true">
          <BarDetail bar={bar} kind={model.kind} styles={styles} hovered={hovered} />
        </div> : null}
      </div>
    </div>
    <div className="usage-chart-xaxis" aria-hidden="true">
      {model.bars.map((item, index) => <span key={item.key}>{index % model.labelEvery === 0 ? bucketLabel(item, model.kind) : ""}</span>)}
    </div>
    <p id={`${id}-detail`} className="usage-visually-hidden" aria-live="polite">{bar ? barSentence(bar, model.kind) : ""}</p>
    <ul className="usage-chart-legend" aria-label={model.split === "account" ? "Accounts" : "Providers"}>
      {model.legend.map(entry => <li key={entry.key}>
        <span className="usage-chart-swatch" data-pattern={entry.style.pattern} style={seriesStyleVars(entry.style)} aria-hidden="true" />
        <strong>{entry.label}</strong>
        <span>{entry.total ? `${formatUsageNumber(entry.total)} tokens · ${sharePercent(entry.total, model.grand)}` : "Tokens not reported"}</span>
      </li>)}
    </ul>
    <details className="usage-chart-table">
      <summary>Show as a table</summary>
      <div className="usage-table-scroll"><table className="usage-bucket-table">
        <caption>Reported tokens per {word} by {who}</caption>
        <thead><tr><th scope="col">{word[0].toUpperCase() + word.slice(1)}</th>{model.legend.map(entry => <th scope="col" key={entry.key}>{entry.label}</th>)}<th scope="col">Total</th></tr></thead>
        <tbody>{model.bars.map(item => <tr key={item.key}>
          <th scope="row">{bucketLabel(item, model.kind, true)}</th>
          {model.legend.map(entry => { const tokens = item.segments.find(segment => segment.key === entry.key)?.tokens || 0; return <td key={entry.key}>{tokens ? formatUsageNumber(tokens) : "–"}</td>; })}
          <td>{formatUsageNumber(item.total)}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
  </figure>;
}

/** By provider / By account: a two-option radio group, arrow keys move between them. */
function SplitSwitch({ split, onChange }) {
  return <div className="usage-range-options usage-chart-split" role="radiogroup" aria-label="Split the chart">
    {USAGE_SPLITS.map((option, index) => <button
      key={option.key} type="button" role="radio" aria-checked={split === option.key}
      tabIndex={split === option.key ? 0 : -1} onClick={() => onChange(option.key)}
      onKeyDown={event => {
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const next = USAGE_SPLITS[(index + step + USAGE_SPLITS.length) % USAGE_SPLITS.length];
        onChange(next.key);
        event.currentTarget.parentElement.querySelectorAll("[role=radio]")[USAGE_SPLITS.indexOf(next)]?.focus();
      }}
    >{option.label}</button>)}
  </div>;
}

function BarDetail({ bar, kind, styles, hovered }) {
  return <>
    <strong>{bucketLabel(bar, kind, true)}</strong>
    <span className="usage-chart-tooltip-total">{bar.total ? `${formatUsageNumber(bar.total)} tokens` : "No reported tokens"}</span>
    {bar.segments.length ? <ul>{bar.segments.map(segment => <li key={segment.key} data-hovered={hovered === segment.key || undefined}>
      <span className="usage-chart-swatch" data-pattern={styles[segment.key]?.pattern} style={seriesStyleVars(styles[segment.key])} />
      <span>{segment.label}</span>
      <span>{formatUsageNumber(segment.tokens)}</span>
      <span>{sharePercent(segment.tokens, bar.total)}</span>
    </li>)}</ul> : null}
    {bar.unknownCalls ? <span className="usage-chart-tooltip-note">{bar.unknownCalls} {bar.unknownCalls === 1 ? "call" : "calls"} without token data</span> : null}
  </>;
}

function barSentence(bar, kind) {
  const parts = bar.segments.map(segment => `${segment.label} ${formatUsageNumber(segment.tokens)} (${sharePercent(segment.tokens, bar.total)})`);
  const unknown = bar.unknownCalls ? `, ${bar.unknownCalls} ${bar.unknownCalls === 1 ? "call" : "calls"} without token data` : "";
  return `${bucketLabel(bar, kind, true)}: ${bar.total ? `${formatUsageNumber(bar.total)} tokens` : "no reported tokens"}${parts.length ? `. ${parts.join(", ")}` : ""}${unknown}.`;
}
