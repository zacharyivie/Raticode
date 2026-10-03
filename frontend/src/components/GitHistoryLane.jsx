import { CircleDot, Cloud, GitBranch, Tag } from "lucide-react";
import { commitLabels, GRAPH_COLORS, GRAPH_PATTERNS, GRAPH_LANE_SPACING, GRAPH_LANE_INSET } from "../lib/gitHistoryGraph.js";

export default function GitHistoryLane({ row, width }) {
  const x = lane => GRAPH_LANE_INSET + lane * GRAPH_LANE_SPACING;
  const pattern = color => GRAPH_PATTERNS[GRAPH_COLORS.indexOf(color)];
  return <span aria-hidden="true" className="scm-history-lane" style={{ width }}>
    <svg width={width} height="18" viewBox={`0 0 ${width} 18`}>
      {row.segments.filter(segment => segment.full || segment.top).map((segment, index) => <path key={index} d={`M ${x(segment.from)} 0 V 18`} fill="none" stroke={segment.color} strokeWidth="2.5" strokeDasharray={pattern(segment.color)} />)}
    </svg>
    <span className="scm-history-lower"><svg width={width} height="100%" viewBox={`0 0 ${width} 18`} preserveAspectRatio="none">
      {row.segments.filter(segment => !segment.top).map((segment, index) => <path key={index} d={segment.full
        ? `M ${x(segment.from)} 0 L ${x(segment.to)} 18`
        : `M ${x(segment.from)} 0 C ${x(segment.from)} 10 ${x(segment.to)} 8 ${x(segment.to)} 18`}
      fill="none" stroke={segment.color} strokeWidth="2.5" strokeDasharray={pattern(segment.color)} vectorEffect="non-scaling-stroke" strokeLinecap="round" />)}
    </svg></span>
    <span className={`scm-history-dot ${row.commit.isHead ? "scm-history-head" : ""}`} style={{ left: x(row.lane), borderColor: row.color, backgroundColor: row.commit.isHead ? "var(--scm-graph-background)" : row.color }} />
  </span>;
}

export function GitHistoryRefs({ commit }) {
  const labels = commitLabels(commit);
  const detached = commit.isHead && !labels.some(ref => ref.current);
  if (!labels.length && !detached) return null;
  return <span className="flex flex-wrap gap-1 py-0.5">
    {detached ? <span className="scm-history-ref scm-history-ref-local" title="Current detached HEAD"><CircleDot aria-hidden="true" size={10} /> HEAD</span> : null}
    {labels.map(ref => {
      const Icon = ref.type === "remote" ? Cloud : ref.type === "tag" ? Tag : ref.current ? CircleDot : GitBranch;
      const kind = ref.type === "remote" ? "Remote branch" : ref.type === "tag" ? "Tag" : "Local branch";
      const tracking = ref.upstreamGone ? `Upstream ${ref.upstream} is missing` : ref.upstream ? `${ref.ahead || 0} ahead, ${ref.behind || 0} behind ${ref.upstream}` : "";
      return <span key={ref.id} className="contents">
        <span className={`scm-history-ref scm-history-ref-${ref.type}`} title={`${kind}: ${ref.name}${ref.current ? " (current)" : ""}`}><Icon aria-hidden="true" size={10} className="shrink-0" /> {ref.name}</span>
        {ref.upstream ? <span className="scm-history-ref scm-history-ref-local scm-history-tracking" title={tracking} aria-label={tracking}>{ref.upstreamGone ? "upstream missing" : `${ref.ahead || 0} ahead / ${ref.behind || 0} behind`}</span> : null}
      </span>;
    })}
  </span>;
}
