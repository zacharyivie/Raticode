import { useId } from "react";
import { formatUsageTime } from "../lib/usage.js";
import { verifiedCauses, verifiedHeadline, verifiedStats, verifiedTrend } from "../lib/verifiedWork.js";

/** Settings › Usage › Verified work: how often Rem's tickets passed review on their own. */
export default function UsageVerifiedWork({ numbers }) {
  const id = useId();
  if (!numbers || typeof numbers !== "object") return null;
  const headline = verifiedHeadline(numbers);
  const stats = headline ? verifiedStats(numbers) : [];
  const causes = verifiedCauses(numbers);
  const trend = verifiedTrend(numbers);
  return <section className="usage-verified" aria-labelledby={`${id}-title`}>
    <h3 id={`${id}-title`}>Verified work</h3>
    {!headline ? <p className="usage-empty usage-verified-empty">No Rem ticket was accepted or blocked in this period. Choose a longer period to see earlier work.</p> : <>
      <div className="usage-total usage-verified-headline">
        <span>First-pass acceptance</span>
        <strong>{headline.value}{headline.percent !== null ? <small>{headline.percent}%</small> : null}</strong>
        <p>{headline.label}.</p>
      </div>
      <dl className="usage-verified-stats">
        {stats.map(stat => <div key={stat.key}><dt>{stat.label}</dt><dd><strong>{stat.value}</strong><span>{stat.note}</span></dd></div>)}
      </dl>
      {causes.length ? <div className="usage-verified-causes"><h4>Why tickets were blocked</h4><ul>
        {causes.map(cause => <li key={cause.key}><span>{cause.label}</span><strong>{cause.tickets}</strong></li>)}
      </ul></div> : null}
      {trend.length ? <figure className="usage-verified-trend">
        <figcaption>Per {numbers.trend?.bucket || "day"}: <span className="usage-verified-key" data-part="first">first time</span> <span className="usage-verified-key" data-part="repaired">after repairs</span> <span className="usage-verified-key" data-part="blocked">blocked</span></figcaption>
        <ol>{trend.map(bar => <li key={bar.key} title={bar.text} aria-label={bar.text}>
          <span data-part="blocked" style={{ height: `${bar.blocked}%` }} />
          <span data-part="repaired" style={{ height: `${bar.repaired}%` }} />
          <span data-part="first" style={{ height: `${bar.first}%` }} />
        </li>)}</ol>
      </figure> : null}
    </>}
    <p className="usage-note">Counts Rem&apos;s own Kanban tickets that reached Done or Blocked in this period, from their timelines, the runs still stored and the token ledger. These numbers are only shown to you; Rem and its workers never see them.{numbers.history_since ? ` History starts ${formatUsageTime(numbers.history_since)}.` : ""}</p>
  </section>;
}
