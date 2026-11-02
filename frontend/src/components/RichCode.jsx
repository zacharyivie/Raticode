// Loaded on demand by MarkdownContent: highlighted code and ```rem-chart charts.
// Both render from data as React elements; nothing here builds HTML or runs code.
import { tokenize } from "../lib/highlight.js";
import { chartScale, chartSummary, formatChartValue } from "../lib/remChart.js";
import "./RichCode.css";

export function HighlightedCode({ code, language }) {
  return <code className={`language-${language}`}>
    {tokenize(code, language).map((token, index) => token.type === "text"
      ? token.text
      : <span key={index} className={`rc-${token.type}`}>{token.text}</span>)}
  </code>;
}

const WIDTH = 560;
const HEIGHT = 240;
const PAD = { top: 16, right: 12, bottom: 40, left: 48 };
// Series are told apart by pattern and marker shape as well as color.
const SERIES = [
  { color: "var(--rc-series-1)", dash: "", marker: "circle" },
  { color: "var(--rc-series-2)", dash: "6 4", marker: "square" },
  { color: "var(--rc-series-3)", dash: "2 3", marker: "triangle" },
  { color: "var(--rc-series-4)", dash: "10 3 2 3", marker: "diamond" },
];

export function RemChart({ chart }) {
  const { low, high, ticks } = chartScale(chart);
  const plotWidth = WIDTH - PAD.left - PAD.right;
  const plotHeight = HEIGHT - PAD.top - PAD.bottom;
  const y = value => PAD.top + plotHeight - ((value - low) / (high - low)) * plotHeight;
  const band = plotWidth / chart.labels.length;
  const x = index => PAD.left + band * index + band / 2;
  const every = Math.ceil(chart.labels.length / 10);
  const summary = chartSummary(chart);
  const barWidth = Math.max(2, (band * 0.72) / chart.series.length);
  return <figure className="rc-chart">
    {chart.title ? <figcaption className="rc-chart-title">{chart.title}</figcaption> : null}
    <div className="rc-chart-scroll">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={summary}>
        {ticks.map(tick => <g key={tick}>
          <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(tick)} y2={y(tick)} className="rc-grid" />
          <text x={PAD.left - 6} y={y(tick)} className="rc-axis" textAnchor="end" dominantBaseline="middle">{formatChartValue(tick)}</text>
        </g>)}
        {chart.labels.map((label, index) => index % every === 0
          ? <text key={index} x={x(index)} y={HEIGHT - PAD.bottom + 16} className="rc-axis" textAnchor="middle">{label.length > 12 ? `${label.slice(0, 11)}…` : label}</text>
          : null)}
        {chart.series.map((series, seriesIndex) => {
          const style = SERIES[seriesIndex % SERIES.length];
          if (chart.type === "bar") {
            return <g key={series.name} fill={style.color}>
              {series.values.map((value, index) => value === null ? null : <rect key={index}
                x={x(index) - (barWidth * chart.series.length) / 2 + barWidth * seriesIndex}
                y={Math.min(y(value), y(0))} width={barWidth - 1} height={Math.max(1, Math.abs(y(value) - y(0)))}
                className={`rc-bar rc-pattern-${seriesIndex}`}><title>{`${series.name}, ${chart.labels[index]}: ${formatChartValue(value)}`}</title></rect>)}
            </g>;
          }
          const points = series.values.map((value, index) => value === null ? null : [x(index), y(value)]);
          const path = points.reduce((line, point, index) => point ? `${line}${line && points[index - 1] ? "L" : "M"}${point[0].toFixed(1)},${point[1].toFixed(1)}` : line, "");
          return <g key={series.name} stroke={style.color} fill={style.color}>
            <path d={path} fill="none" strokeWidth="2" strokeDasharray={style.dash} />
            {points.map((point, index) => point ? <Marker key={index} shape={style.marker} x={point[0]} y={point[1]}>
              <title>{`${series.name}, ${chart.labels[index]}: ${formatChartValue(series.values[index])}`}</title>
            </Marker> : null)}
          </g>;
        })}
        <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(0)} y2={y(0)} className="rc-zero" />
      </svg>
    </div>
    {chart.series.length > 1 ? <ul className="rc-legend" aria-label="Series">
      {chart.series.map((series, index) => <li key={series.name}><span aria-hidden="true" className={`rc-swatch rc-pattern-${index}`} style={{ background: SERIES[index % SERIES.length].color }} />{series.name}</li>)}
    </ul> : null}
    <details className="rc-data">
      <summary>Data table</summary>
      <div className="rc-table-scroll" role="region" aria-label="Chart data" tabIndex={0}>
        <table>
          <thead><tr><th scope="col">Label</th>{chart.series.map(series => <th key={series.name} scope="col">{series.name}</th>)}</tr></thead>
          <tbody>{chart.labels.map((label, index) => <tr key={index}>
            <th scope="row">{label}</th>
            {chart.series.map(series => <td key={series.name}>{formatChartValue(series.values[index])}</td>)}
          </tr>)}</tbody>
        </table>
      </div>
    </details>
  </figure>;
}

function Marker({ shape, x, y, children }) {
  if (shape === "square") return <rect x={x - 3.5} y={y - 3.5} width="7" height="7">{children}</rect>;
  if (shape === "triangle") return <path d={`M${x},${y - 4.5}L${x + 4.5},${y + 3.5}L${x - 4.5},${y + 3.5}Z`}>{children}</path>;
  if (shape === "diamond") return <path d={`M${x},${y - 5}L${x + 5},${y}L${x},${y + 5}L${x - 5},${y}Z`}>{children}</path>;
  return <circle cx={x} cy={y} r="3.5">{children}</circle>;
}
