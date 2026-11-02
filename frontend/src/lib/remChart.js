// A ```rem-chart block: a small bar or line chart Rem can include in a reply.
// The JSON is data only. Anything invalid falls back to showing the code block.
// The phone reads the same format (RemChart.kt).

export const CHART_LIMITS = { points: 50, series: 4, label: 40, title: 120 };

export function parseRemChart(source) {
  let raw;
  try { raw = JSON.parse(String(source ?? "")); } catch { return null; }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const type = raw.type === "line" ? "line" : raw.type === "bar" ? "bar" : null;
  const labels = Array.isArray(raw.labels) ? raw.labels.map(label => String(label ?? "").slice(0, CHART_LIMITS.label)) : null;
  if (!type || !labels?.length || labels.length > CHART_LIMITS.points) return null;
  const series = (Array.isArray(raw.series) ? raw.series : []).slice(0, CHART_LIMITS.series).map((item, index) => ({
    name: String(item?.name ?? `Series ${index + 1}`).slice(0, CHART_LIMITS.label),
    values: Array.isArray(item?.values) ? item.values.slice(0, labels.length).map(value => (typeof value === "number" && Number.isFinite(value) ? value : null)) : [],
  })).filter(item => item.values.some(value => value !== null));
  if (!series.length) return null;
  return { type, title: String(raw.title ?? "").slice(0, CHART_LIMITS.title), labels, series };
}

// The value axis: always includes zero, rounded to a readable step.
export function chartScale(chart) {
  const values = chart.series.flatMap(item => item.values).filter(value => value !== null);
  let low = Math.min(0, ...values);
  let high = Math.max(0, ...values);
  if (low === high) high = low + 1;
  const rough = (high - low) / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map(factor => factor * magnitude).find(candidate => candidate >= rough) || rough;
  low = Math.floor(low / step) * step;
  high = Math.ceil(high / step) * step;
  const ticks = [];
  for (let value = low; value <= high + step / 2; value += step) ticks.push(Number(value.toPrecision(12)));
  return { low, high, ticks };
}

export function chartSummary(chart) {
  const names = chart.series.map(item => item.name).join(", ");
  return `${chart.type === "line" ? "Line" : "Bar"} chart${chart.title ? `: ${chart.title}` : ""}. ${chart.labels.length} points for ${names}. The data table follows.`;
}

export function formatChartValue(value) {
  if (value === null || value === undefined) return "—";
  return Math.abs(value) >= 1000 ? value.toLocaleString(undefined, { maximumFractionDigits: 1 }) : String(Number(value.toFixed(2)));
}
