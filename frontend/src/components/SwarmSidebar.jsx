import { useEffect, useState } from "react";
import { Plus, RefreshCw, Users } from "lucide-react";
import { startPolling } from "../lib/refresh.js";
import { swarmRequest } from "../lib/swarms.js";

export default function SwarmSidebar({ rootPath, active, selectedId, onSelect }) {
  const [swarms, setSwarms] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!active || !rootPath) return undefined;
    let cancelled = false;
    async function load() {
      try {
        const result = await swarmRequest(rootPath);
        if (!cancelled) { setSwarms(result.swarms || []); setError(""); }
      } catch (failure) { if (!cancelled) setError(failure.message); }
      finally { if (!cancelled) setLoading(false); }
    }
    setLoading(true);
    const stop = startPolling(load, { interval: 4000, immediate: true });
    const update = () => void load();
    window.addEventListener("gofer:swarms-changed", update);
    return () => { cancelled = true; stop(); window.removeEventListener("gofer:swarms-changed", update); };
  }, [active, rootPath, refresh]);
  return <section id="sidebar-panel-swarms" role="tabpanel" aria-labelledby="sidebar-tab-swarms" hidden={!active} className={`min-h-0 min-w-0 flex-1 flex-col ${active ? "flex" : "hidden"}`}>
    <div className="flex h-8 shrink-0 items-center justify-between px-2">
      <span className="text-xs font-semibold text-ink">Swarms</span>
      <div className="flex gap-1"><button type="button" aria-label="Refresh swarms" className="grid h-7 w-7 place-items-center rounded text-muted hover:bg-slate-100" onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={13} /></button><button type="button" aria-label="New swarm" className="grid h-7 w-7 place-items-center rounded text-muted hover:bg-slate-100" onClick={() => onSelect("new")}><Plus size={15} /></button></div>
    </div>
    <div className="workflow-scrollbar min-h-0 overflow-y-auto">
      {error ? <p role="alert" className="px-2 py-3 text-xs text-red-600 dark:text-red-300">{error}</p> : null}
      {loading && !swarms.length ? <p role="status" className="px-2 py-3 text-xs text-muted">Loading swarms...</p> : !swarms.length ? <div className="space-y-3 px-2 py-4 text-xs leading-5 text-muted"><p>No swarms in this project.</p><button type="button" className="font-semibold text-ink underline underline-offset-4" onClick={() => onSelect("new")}>Create a swarm</button></div> : swarms.map((swarm) => <button key={swarm.id} type="button" aria-current={swarm.id === selectedId ? "true" : undefined} className={`flex w-full items-start gap-2 rounded px-2 py-2 text-left text-xs hover:bg-slate-100 ${swarm.id === selectedId ? "bg-slate-100 text-ink" : "text-muted"}`} onClick={() => onSelect(swarm.id)}><Users size={15} className="mt-0.5 shrink-0" /><span className="min-w-0 flex-1"><span className="block truncate font-semibold text-ink">{swarm.name}</span><span className="block truncate">{swarm.agents?.length || 0} agents · {swarm.run?.state || "Ready"}</span></span></button>)}

    </div>
  </section>;
}
