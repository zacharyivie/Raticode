import { useEffect, useState } from "react";
import { Building2, Plus, RefreshCw } from "lucide-react";
import { startPolling } from "../lib/refresh.js";
import "./OrganizationSidebar.css";
import { organizationRequest } from "../lib/organizations.js";

export default function OrganizationSidebar({ active, selectedId, onSelect }) {
  const [listing, setListing] = useState([]);
  const items = listing;
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
    <div className="org-sidebar-heading"><strong>Organizations <small>Experimental</small></strong><div><button type="button" aria-label="Refresh organizations" title="Refresh organizations" disabled={loading} onClick={() => setRefresh(v => v + 1)}><RefreshCw size={14} /></button><button type="button" aria-label="New organization" title="New organization" disabled={!enabled || loading} onClick={() => onSelect("new")}><Plus size={15} /></button></div></div>
    {error && <p role="alert" className="org-sidebar-error">{error}</p>}
    {loading && !items.length ? <p role="status" className="org-sidebar-hint">Loading organizations...</p> : !enabled ? <div className="org-sidebar-empty"><Building2 size={24} aria-hidden="true" /><p>Organizations is experimental and disabled for this release. Existing organization data is kept.</p><p>To try it, quit Raticode and launch it with the environment variable <code className="break-all">RATICODE_EXPERIMENTAL_ORGANIZATIONS=1</code>. Employees, remote jobs, and workflow execution require your review before use.</p></div> : !items.length ? <div className="org-sidebar-empty"><Building2 size={24} aria-hidden="true" /><p>Create a company of Rem employees or import an Agent Companies package.</p><button type="button" onClick={() => onSelect("new")}><Plus size={14} />Create organization</button></div> : <div className="org-sidebar-list">{items.map(item => <button type="button" key={item.id} aria-current={selectedId === item.id ? "true" : undefined} title={item.name} onClick={() => onSelect(item.id)}><Building2 size={17} aria-hidden="true" /><span><strong>{item.name}</strong><small><i data-state={item.state} aria-hidden="true" /><span>{item.state}</span><span>Revision {item.revision}</span></small></span></button>)}</div>}
  </section>;
}
