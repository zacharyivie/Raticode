// What Rem did on its own: shown in Settings > Rem and on the Kanban sidebar.
export default function SelfWorkReport({ report, title = "Report", limit = 40 }) {
  const items = [
    ...report.work_done.map(item => ({ key: `w-${item.at}-${item.title}`, at: item.at, title: item.title, detail: item.summary, tone: item.state })),
    ...report.tickets_added.map(item => ({ key: `a-${item.at}-${item.title}`, at: item.at, title: item.title, detail: item.proposed ? "Proposed for your approval" : item.organization_name ? `Added to ${item.organization_name}` : "Added to Rem's board", tone: "added" })),
    ...report.duplicates_merged.map(item => ({ key: `d-${item.at}-${item.title}`, at: item.at, title: item.title, detail: `Merged into “${item.kept_title}”`, tone: "merged" })),
  ].sort((a, b) => String(b.at).localeCompare(String(a.at))).slice(0, limit);
  // With nothing else to report, the summary is already the latest note.
  const notes = report.notes.filter(note => note.text !== report.summary);
  return <div className="kb-card" data-testid="self-work-report">
    <h3>{title}</h3>
    <p className="kb-side-note" data-testid="kanban-report-summary">{report.summary}</p>
    {items.length ? <ul className="kb-report">{items.map(item => <li key={item.key} data-tone={item.tone}><strong>{item.title}</strong><span>{item.detail}</span></li>)}</ul> : null}
    {notes.length ? <ul className="kb-notes">{notes.map(note => <li key={note.at + note.text}>{note.text}</li>)}</ul> : null}
  </div>;
}
