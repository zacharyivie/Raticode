import { useId } from "react";
import { ExternalLink } from "lucide-react";
import { costLabel, formatUsageNumber, formatUsd, usageDashboardUrl, usageNumber, usageProviderName } from "../lib/usage.js";

const BREAKDOWN = [["input", "Uncached input"], ["cache_read", "Cache reads"], ["cache_write", "Cache writes"], ["output", "Output"]];

function calls(count, singular, plural) {
  return `${formatUsageNumber(count)} ${count === 1 ? singular : plural}`;
}

/** Settings › Usage › Tokens in Raticode: what the period's tokens would cost at API list prices. */
export default function UsageApiCost({ cost }) {
  const id = useId();
  if (!cost?.total?.calls) return null;
  const { total, providers = [] } = cost;
  const unsplit = total.unsplit_calls || 0;
  const unpriced = total.unpriced_calls || 0;
  const leftOut = (total.no_token_calls || 0) + (total.estimated_calls || 0) + (total.partial_calls || 0);
  return <section className="usage-cost" aria-labelledby={`${id}-title`}>
    <h3 id={`${id}-title`}>Cost at API prices <span className="usage-estimate-badge">Estimate</span></h3>
    <div className="usage-cost-cards">
      <div className="usage-total usage-cost-card">
        <span>Estimated API cost</span>
        <strong>{costLabel(total)}</strong>
        <p>What this period&apos;s reported tokens would cost at public API list prices. Not a bill.</p>
      </div>
    </div>
    <div className="usage-table-scroll"><table className="usage-token-table usage-cost-table"><caption>Estimated API cost by provider</caption>
      <thead><tr><th scope="col">Provider</th>{BREAKDOWN.map(([key, label]) => <th key={key} scope="col">{label}</th>)}<th scope="col">API cost</th></tr></thead>
      <tbody>{providers.map((provider, index) => <tr key={`${provider.provider}:${provider.profile || ""}:${index}`}>
        <th scope="row">{usageProviderName(provider.provider)}{provider.profile ? <small>{provider.profile}</small> : null}
          {provider.unpriced_models?.length ? <small>No list price: {provider.unpriced_models.join(", ")}</small> : null}
          {provider.unsplit_calls ? <small>{calls(provider.unsplit_calls, "call", "calls")} without a cache breakdown</small> : null}
        </th>
        {BREAKDOWN.map(([key]) => <td key={key}>{provider.breakdown ? formatUsd(provider.breakdown[key]) : "Unknown"}</td>)}
        <td>{costLabel(provider)}</td>
      </tr>)}</tbody>
      <tfoot><tr><th scope="row">Total</th>{BREAKDOWN.map(([key]) => <td key={key}>{total.breakdown ? formatUsd(total.breakdown[key]) : "Unknown"}</td>)}<td>{costLabel(total)}</td></tr></tfoot>
    </table></div>
    {unsplit ? <p className="usage-note">{calls(unsplit, "call has", "calls have")} no cache breakdown, so all their input is priced as uncached input. That overstates a call that used the cache. Claude Code turns stopped before this release saved only part of their input, so for those it understates instead.</p> : null}
    {unpriced ? <p className="usage-note">{calls(unpriced, "call used a model", "calls used models")} with no list price, such as a provider&apos;s default model when Raticode didn&apos;t record which one ran. Their cost is unknown, not $0.</p> : null}
    {leftOut ? <p className="usage-note">Calls without token data, estimated calls and calls that reported only part of their tokens are left out or counted in part, so a total marked &ldquo;at least&rdquo; is a floor.</p> : null}
    <p className="usage-note">Cache reads, cache writes and uncached input are priced separately when the provider reported them. Long-context surcharges, cache storage and tool fees aren&apos;t included. Usage outside Raticode isn&apos;t counted. Prices checked {cost.prices_checked || "—"}.</p>
    <PriceList prices={cost.prices || []} />
  </section>;
}

function perMillion(value) {
  return usageNumber(value) === null ? "—" : `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 3 })}`;
}

function PriceList({ prices }) {
  if (!prices.length) return null;
  return <details className="usage-cost-details">
    <summary>Price list ({prices.length} models, US$ per million tokens)</summary>
    <div className="usage-table-scroll"><table className="usage-token-table usage-price-table"><caption>API list prices used for the estimate</caption>
      <thead><tr><th scope="col">Model</th><th scope="col">Input</th><th scope="col">Cache read</th><th scope="col" title="Anthropic: 5 minutes / 1 hour. OpenAI: 30 minutes / 12 hours.">Cache write short / long</th><th scope="col">Output</th><th scope="col">Source</th></tr></thead>
      <tbody>{prices.map(price => {
        const url = usageDashboardUrl(price.source);
        return <tr key={`${price.vendor}:${price.model}`}>
          <th scope="row">{price.model}{price.note ? <small>{price.note}</small> : null}</th>
          <td>{perMillion(price.input)}</td>
          <td>{price.cache_read === null ? "No discount" : perMillion(price.cache_read)}</td>
          <td>{price.cache_write === null && price.cache_write_long === null ? "As input" : `${perMillion(price.cache_write ?? price.input)} / ${perMillion(price.cache_write_long ?? price.cache_write ?? price.input)}`}</td>
          <td>{perMillion(price.output)}</td>
          <td>{url ? <a href={url} target="_blank" rel="noreferrer">{new URL(url).hostname}<ExternalLink aria-hidden="true" size={11} /></a> : "—"}<small>Checked {price.checked}</small></td>
        </tr>;
      })}</tbody>
    </table></div>
  </details>;
}
