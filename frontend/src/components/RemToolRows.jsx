import "./RemResources.css";

// Shared list primitives for the tools menu, Settings, swarms and organizations.
export function ToolSection({ title, summary, action, children }) {
  return <section className="rem-tools__section" aria-label={title}>
    <header className="rem-tools__section-header">
      <h3>{title}</h3>
      {summary ? <span className="rem-tools__section-summary">{summary}</span> : null}
      {action ? <span className="rem-tools__section-action">{action}</span> : null}
    </header>
    <ul className="rem-tools__list">{children}</ul>
  </section>;
}

export function ToolRow({ icon: Icon, title, detail, badge, checked, disabled = false, actions = null, onChange, label }) {
  return <li className={`rem-tools__row${checked ? " is-on" : ""}${disabled ? " is-disabled" : ""}`}>
    <span className="rem-tools__icon" aria-hidden="true">{Icon ? <Icon size={15} strokeWidth={1.75} /> : null}</span>
    <span className="rem-tools__text">
      <span className="rem-tools__title"><span className="rem-tools__name" title={title}>{title}</span>{badge ? <span className="rem-tools__badge">{badge}</span> : null}</span>
      {detail ? <span className="rem-tools__detail" title={detail}>{detail}</span> : null}
    </span>
    {actions ? <span className="rem-tools__actions">{actions}</span> : null}
    <input aria-label={label || title} className="rem-switch" type="checkbox" role="switch" checked={Boolean(checked)} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
  </li>;
}

export function IconAction({ label, icon: Icon, onClick, danger = false }) {
  return <button aria-label={label} title={label} className={`rem-tools__icon-button${danger ? " is-danger" : ""}`} type="button" onClick={onClick}><Icon size={14} strokeWidth={1.75} /></button>;
}
