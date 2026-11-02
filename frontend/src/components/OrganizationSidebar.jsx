import { useEffect, useState } from "react";
import { Building2, Plus, RefreshCw } from "lucide-react";
import { startPolling } from "../lib/refresh.js";
import "./OrganizationSidebar.css";
import { organizationRequest } from "../lib/organizations.js";

export default function OrganizationSidebar({ active, selectedId, onSelect }) {
  const [listing, setListing] = useState([]);
  const [showArchived, setShowArchived] = useState(false);
  const items = listing.filter(item => !item.archived);
  const archived = listing.filter(item => item.archived);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;
    setLoading(true);
    const load = async () => {
      try {
        const availability = await organizationRequest("", "availability");
        const data = availability.enabled ? await organizationRequest() : [];
        if (!cancelled) { setEnabled(availability.enabled); setListing(data); setError(""); }
      }
      catch (failure) { if (!cancelled) setError(failure.message); }
      finally { if (!cancelled) setLoading(false); }
    };
    const stop = startPolling(load, { interval: 4000, immediate: true });
    const changed = () => void load();
    window.addEventListener("gofer:organizations-changed", changed);
    return () => { cancelled = true; stop(); window.removeEventListener("gofer:organizations-changed", changed); };
  }, [active, refresh]);
  return <section id="sidebar-panel-organizations" role="tabpanel" aria-labelledby="sidebar-tab-organizations" hidden={!active} className={`org-sidebar min-h-0 min-w-0 flex-1 flex-col text-ink ${active ? "flex" : "hidden"}`}>
    <div className="org-sidebar-heading ui-panel-header"><strong className="ui-section-title">Organizations <small className="ui-badge">Preview</small></strong><div><button type="button" aria-label="Refresh organizations" title="Refresh organizations" disabled={loading} onClick={() => setRefresh(v => v + 1)}><RefreshCw size={14} /></button><button type="button" aria-label="New organization" title="New organization" disabled={!enabled || loading} onClick={() => onSelect("new")}><Plus size={15} /></button></div></div>
    {error && <p role="alert" className="org-sidebar-error">{error}</p>}
    {loading && !listing.length ? <p role="status" className="org-sidebar-hint">Loading organizations...</p> : !enabled ? <div className="org-sidebar-empty"><Building2 size={24} aria-hidden="true" /><p>Organizations is turned off. Existing organization data is kept.</p><p>Turn it on in Settings &gt; Developer &gt; Feature Preview.</p></div> : !listing.length ? <div className="org-sidebar-empty"><Building2 size={24} aria-hidden="true" /><p>Create a company of Rem employees or import an Agent Companies package.</p><button type="button" onClick={() => onSelect("new")}><Plus size={14} />Create organization</button></div> : <div className="org-sidebar-list">{[...items, ...(showArchived ? archived : [])].map(item => <button type="button" key={item.id} aria-current={selectedId === item.id ? "true" : undefined} title={item.name} aria-description={item.pauseReason || undefined} onClick={() => onSelect(item.id)}><Building2 size={17} aria-hidden="true" /><span><strong>{item.name}</strong><small><i data-state={item.archived ? "archived" : item.state} aria-hidden="true" /><span>{item.archived ? "Archived" : item.state}</span><span>{item.openTasks ? `${item.openTasks} open` : `Revision ${item.revision}`}</span></small>{!item.archived && item.state === "paused" && item.pauseReason && <small className="org-sidebar-reason">{item.pauseReason}</small>}</span></button>)}{archived.length > 0 && <button type="button" className="org-sidebar-toggle" aria-expanded={showArchived} onClick={() => setShowArchived(v => !v)}>{showArchived ? "Hide archived" : `Show archived (${archived.length})`}</button>}</div>}
  </section>;
}
