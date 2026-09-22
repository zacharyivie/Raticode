import { useCallback, useEffect, useState } from "react";
import { apiUrl } from "../lib/api.js";

// Use the same compact controls and theme tokens as the surrounding settings.
const button = "rounded border border-line px-3 py-1.5 text-xs hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 disabled:opacity-50";
const input = "w-full rounded border border-line bg-white px-2 py-1.5 text-xs focus-visible:outline-indigo-500";

export default function PairedDevices() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [invite, setInvite] = useState(null);
  const [name, setName] = useState("Raticode Desktop");
  const [relay, setRelay] = useState("https://ntfy.sh");
  const [uri, setUri] = useState("");
  const [preview, setPreview] = useState(null);
  const [revoke, setRevoke] = useState(null);
  const [unpair, setUnpair] = useState(null);
  const [now, setNow] = useState(Date.now());
  const [result, setResult] = useState(null);
  const [syncError, setSyncError] = useState("");
  const [showPairing, setShowPairing] = useState(false);
  useEffect(() => {
    const update = event => setSyncError(event.detail);
    window.addEventListener("gofer:device-sync-status", update);
    return () => window.removeEventListener("gofer:device-sync-status", update);
  }, []);

  const refresh = useCallback(async (signal) => {
    const response = await fetch(apiUrl("/devices"), { signal, cache: "no-store" });
    if (!response.ok) throw new Error("Device settings could not be loaded.");
    setStatus(await response.json());
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const update = () => refresh(controller.signal).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    update();
    const timer = setInterval(() => { setNow(Date.now()); update(); }, 2000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [refresh]);
  useEffect(() => {
    if (invite && (invite.expires_at * 1000 <= now)) setInvite(null);
  }, [invite, now]);

  async function action(body) {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(apiUrl("/devices"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body), cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Device action failed.");
      if (body.action === "invite") setInvite(result);
      if (body.action === "preview") setPreview(result);
      if (body.action === "pair") { setUri(""); setPreview(null); }
      if (body.action === "cancel_invitation") setInvite(null);
      if (body.action === "revoke") setRevoke(null);
      if (body.action === "unpair") setUnpair(null);
      if (["send", "work_status", "fleet_status", "offer_file"].includes(body.action)) setResult(result);
      await refresh();
      return result;
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  const peers = status?.peers || [];
  const activePeers = peers.filter(peer => peer.state !== "revoked");
  const grants = status?.grants || [];
  return <section className="space-y-5 text-xs leading-relaxed" aria-label="Paired devices">
    <div className="flex items-start justify-between gap-3">
      <div><h3 className="text-sm font-semibold">Paired devices</h3>
        <p className="mt-1 text-muted">Chat with Rem from your phone.</p></div>
      {status?.enabled && <button type="button" className={button} aria-expanded={showPairing}
        onClick={() => setShowPairing(!showPairing)}>{showPairing ? "Close setup" : "Pair a device"}</button>}
    </div>
    {(error || status?.error) && <p role="alert" className="text-red-700">{error || status.error}</p>}
    {status && !status.experimental_network && <div role="status" className="space-y-2 rounded border border-line p-3">
      <p className="font-medium">Phone connections are disabled</p>
      <p className="text-muted">Restart desktop with device networking enabled.</p>
      <details><summary className="cursor-pointer">Startup instructions</summary>
        <p className="mt-2 text-muted">Run from the frontend folder:</p>
        <pre className="whitespace-pre-wrap break-all select-text">RATICODE_DEVICE_EXPERIMENTAL_NETWORK=1 npm run electron:dev</pre>
      </details>
    </div>}
    {status?.enabled && <div className="flex flex-wrap gap-x-5 gap-y-1 border-b border-line pb-3 text-muted" role="status">
      <span>{status.lan_available ? "Local network available" : "Local network unavailable"}</span>
      <span>{status.rem_ready ? "Rem ready" : "Rem unavailable"}</span>
    </div>}
    {status?.enabled && !status.rem_ready && <p role="alert">Restart the desktop backend to restore Rem messaging.</p>}
    {syncError && <p role="alert" className="text-red-700">Thread sync needs attention: {syncError}</p>}
    {!status?.enabled ? <button type="button" className={button} disabled={busy || !status}
      onClick={() => action({ action: "enable" })}>Set up device pairing</button> : <>
      <ul className="divide-y divide-line" aria-label="Device trust registry">
        {activePeers.map((peer, index) => {
          const sharing = (status.workspace_peers || []).includes(peer.device_id);
          const peerGrants = grants.filter(grant => grant.device_id === peer.device_id);
          const canCreate = peerGrants.some(grant => grant.allow_thread_create);
          return <li className="space-y-3 py-3 first:pt-0" key={peer.device_id}>
            <div className="flex items-baseline justify-between gap-3">
              <h4 className="font-semibold">{peer.role === "controller" ? "Phone" : "Desktop"}{activePeers.length > 1 ? ` ${index + 1}` : ""}</h4>
              <span className="text-muted">{peer.state === "active" ? "Paired" : peer.state === "confirmed" ? "Waiting for phone" : peer.state === "pending" ? "Confirm identity" : peer.state}</span>
            </div>
            {peer.state === "pending" && <div className="space-y-2">
              <p>Compare this fingerprint with the one on your device.</p>
              {peer.role === "controller" && <p className="text-muted">Pairing syncs your desktop threads and projects with your phone. Desktop approvals still apply.</p>}
              <p className="break-all select-text font-mono text-[11px]">{peer.fingerprint}</p>
              <button type="button" className={button} disabled={busy} onClick={() => action({ action: "confirm", device_id: peer.device_id, fingerprint: peer.fingerprint })}>Identity matches, confirm pairing</button>
            </div>}
            {peer.state === "confirmed" && <p className="text-muted">Confirmed, waiting for acknowledgment. Keep the phone open to finish pairing.</p>}
            {peer.state === "active" && peer.role === "controller" && <>
              <label className="flex items-start gap-3">
                <input type="checkbox" className="mt-0.5 accent-indigo-600" checked={sharing} disabled={busy}
                  onChange={event => action({ action: "share_workspace", device_id: peer.device_id, enabled: event.target.checked })} />
                <span><span className="font-medium">Sync desktop threads with this phone</span>
                  <span className="mt-1 block text-muted">Sync current and future threads and history. Use your desktop projects and start new conversations. Desktop approvals still apply.</span>
                </span>
              </label>
              <p role="status" className="text-muted">{sharing
                ? `${peerGrants.length} ${peerGrants.length === 1 ? "thread" : "threads"} shared. ${canCreate ? "New conversations allowed." : "No project available for new conversations yet."}`
                : "Thread sync is off. Turn it on to sync your desktop conversations and projects."}</p>
              {sharing && !canCreate && <p className="text-muted">Keep desktop open to finish syncing your projects and conversations.</p>}
            </>}
            {peer.state === "active" && peer.role === "desktop" && <PeerWork peer={peer} busy={busy} action={action} grants={peerGrants} />}
            <div className="space-y-3 text-muted">
              {peer.state !== "pending" && <p className="break-all select-text font-mono text-[11px]">{peer.fingerprint}</p>}
              <p>{peer.last_seen ? `Last authenticated ${new Date(peer.last_seen * 1000).toLocaleString()}.` : "No completed connection yet."} Pairing does not indicate a live connection.</p>
              <div className="flex flex-wrap gap-2">
                {peer.role === "desktop" && ["active", "outbound_pending"].includes(peer.state) && <button type="button" className={button} disabled={busy} onClick={() => action({ action: "reconnect", device_id: peer.device_id })}>Reconnect</button>}
                <button type="button" className={button} disabled={busy} onClick={() => { setUnpair(peer.device_id); setRevoke(null); }}>Unpair</button>
                <button type="button" className={button} disabled={busy} onClick={() => { setRevoke(peer.device_id); setUnpair(null); }}>Revoke</button>
              </div>
              {unpair === peer.device_id && <div role="group" aria-label="Confirm unpairing" className="space-y-2">
                <p>Remove pairing and shared access? You can pair again with a new QR code.</p>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className={button} disabled={busy} onClick={() => action({ action: "unpair", device_id: peer.device_id })}>Unpair device</button>
                  <button type="button" className={button} onClick={() => setUnpair(null)}>Keep paired</button>
                </div>
              </div>}
              {revoke === peer.device_id && <div role="group" aria-label="Confirm revocation" className="space-y-2">
                <p>Block this device from reconnecting or pairing again until you remove it from the revoked list?</p><div className="flex flex-wrap gap-2">
                  <button type="button" className={button} disabled={busy} onClick={() => action({ action: "revoke", device_id: peer.device_id })}>Revoke device</button>
                  <button type="button" className={button} onClick={() => setRevoke(null)}>Keep device</button>
                </div>
              </div>}
            </div>
          </li>;
        })}
      </ul>
      {!activePeers.length && <p className="text-muted">No devices paired. Choose Pair a device to connect your phone.</p>}
      {showPairing && <section aria-label="Pair a device" className="space-y-3 border-t border-line pt-4">
        <h4 className="font-semibold">Connect a phone</h4>
        <p className="text-muted">Create a QR code, scan it on your phone, then confirm its identity here.</p>
        <label className="block space-y-1"><span>Desktop name</span><input className={input} value={name} maxLength={80} onChange={e => setName(e.target.value)} /></label>
        <details><summary className="cursor-pointer text-muted">Relay settings</summary>
          <label className="mt-2 block">Relay origin<input className={input} value={relay} onChange={e => setRelay(e.target.value)} /></label>
          <p className="mt-1 text-muted">Encrypted fallback when the local network is unavailable.</p>
        </details>
        <button type="button" className={button} disabled={busy || !status.experimental_network || Boolean(status.error)} onClick={() => action({ action: "invite", name, relay })}>Create QR code</button>
        {invite && <div className="space-y-3">
          <img src={invite.qr} width="256" height="256" className="max-w-full" alt="One-time pairing invitation QR code" />
          <p className="text-muted">Expires {new Date(invite.expires_at * 1000).toLocaleTimeString()}. Keep this code private.</p>
          <details><summary className="cursor-pointer">Copy invitation link</summary><textarea aria-label="Pairing invitation" readOnly className={input} value={invite.uri} rows={3} /></details>
          <button type="button" className={button} disabled={busy} onClick={() => action({ action: "cancel_invitation" })}>Cancel invitation</button>
        </div>}
        <details><summary className="cursor-pointer text-muted">Pair another desktop</summary>
          <div className="mt-3 space-y-2">
            <label className="block">Desktop invitation<textarea className={input} rows={3} value={uri} onChange={e => { setUri(e.target.value); setPreview(null); }} placeholder="Paste the other desktop's invitation" /></label>
            <button type="button" className={button} disabled={busy || !uri} onClick={() => action({ action: "preview", uri })}>Review identity</button>
            {preview && <div className="space-y-2"><p>Compare this identity with {preview.name}.</p><p className="break-all select-text">{preview.fingerprint}</p><button type="button" className={button} disabled={busy} onClick={() => action({ action: "pair", uri, fingerprint: preview.fingerprint })}>Identity matches, request pairing</button></div>}
            {status.outbound === "awaiting_peer_confirmation" && <div><p>Waiting for confirmation on the other desktop.</p><button type="button" className={button} onClick={() => action({ action: "cancel_pairing" })}>Cancel pairing</button></div>}
            {status.outbound === "failed" && <p role="status">Pairing did not finish. Check expiry and confirmation on the other desktop.</p>}
          </div>
        </details>
      </section>}
      {peers.some(peer => peer.state === "revoked") && <details className="border-t border-line pt-3">
        <summary className="cursor-pointer font-medium">Revoked devices ({peers.filter(peer => peer.state === "revoked").length})</summary>
        <div className="mt-3 space-y-3">
          <p className="text-muted">Removing a device allows fresh QR pairing. Its old connection and shared access stay disabled.</p>
          <ul className="space-y-3" aria-label="Revoked devices">
            {peers.filter(peer => peer.state === "revoked").map(peer => <li key={peer.device_id} className="space-y-2">
              <p className="font-medium">{peer.role === "controller" ? "Phone" : "Desktop"}</p>
              <p className="break-all select-text font-mono text-[11px] text-muted">{peer.fingerprint}</p>
              <button type="button" className={button} disabled={busy} onClick={() => action({ action: "remove_revoked", device_id: peer.device_id })}>Remove from revoked list</button>
            </li>)}
          </ul>
        </div>
      </details>}
      <details className="border-t border-line pt-3">
        <summary className="cursor-pointer font-medium">Connection and troubleshooting</summary>
        <div className="mt-3 space-y-3 text-muted">
          {status.lan_available && status.lan_host ? <p>Local address <span className="select-text font-mono text-ink">{status.lan_host}:{status.port}</span>. On your phone, open Desktop and choose Try local network.</p>
            : <p>Direct LAN is unavailable. Check the desktop network address.</p>}
          {status.relay_error && <p role="status">{status.relay_error === "relay_rate_limited" ? "The relay is rate limited. Local network connections are independent. This warning clears when the affected relay operation succeeds." : "Relay unavailable. Check desktop internet access."}</p>}
          <p>{status.dispatch_error ? "Rem could not read its message queue. Check desktop storage and restart the backend." : "Project permissions and approvals stay on desktop."}</p>
          <details><summary className="cursor-pointer">Desktop identity and security</summary>
            <p className="mt-2 break-all select-text">{status.fingerprint}</p>
            <p className="mt-2">{status.notice || "Independent security review is pending."}</p>
          </details>
          <button type="button" className={button} disabled={busy} onClick={() => action({ action: "fleet_status" })}>Refresh fleet status</button>
        </div>
      </details>
      {result && <div aria-label="Fleet result" role="status" className="space-y-2 border-t border-line pt-3">
        {result.state && <p>Request {result.state}</p>}
        <button type="button" className={button} onClick={() => setResult(null)}>Dismiss result</button>
        {result.type === "file.offer" && <p>File offered: {result.payload?.name}. Waiting for the paired device.</p>}
        {result.devices?.map(device => <div key={device.device_id}><p className="break-all">{device.role} {device.device_id}: {device.reachability}</p><p>{device.running_jobs == null ? "Running work unknown" : `${device.running_jobs.length} reported running jobs`}</p></div>)}
        {result.events?.map(event => <p key={event.id} className="whitespace-pre-wrap break-words">{event.payload?.text || event.payload?.message || event.payload?.state || event.type}</p>)}
      </div>}
    </>}
  </section>;
}

function PeerWork({ peer, busy, action, grants }) {
  const [thread, setThread] = useState(() => crypto.randomUUID());
  const [title, setTitle] = useState("Mobile conversation");
  const [history, setHistory] = useState(null);
  const [projectId, setProjectId] = useState(() => crypto.randomUUID());
  const [project, setProject] = useState("");
  const [provider, setProvider] = useState("codex");
  const [model, setModel] = useState("cli-default");
  const [permission, setPermission] = useState("read-only");
  const [fleetExecute, setFleetExecute] = useState(false);
  const [allowCreate, setAllowCreate] = useState(true);
  const [text, setText] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const [error, setError] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("18766");
  async function chooseProject() {
    try {
      const path = await window.goferDesktop.workspace.selectPath({ directoryOnly: true, currentPath: project });
      if (path) setProject(path);
      setError("");
    } catch (cause) { setError(cause.message); }
  }
  async function offerFile(threadId, projectPath) {
    try {
      const path = await window.goferDesktop.workspace.selectPath({ directoryOnly: false, currentPath: projectPath });
      if (path) await action({ action: "offer_file", device_id: peer.device_id, thread_id: threadId, path });
      setError("");
    } catch (cause) { setError(cause.message); }
  }
  async function grantConversation() {
    const result = await action({ action: "authorize_thread", device_id: peer.device_id, thread_id: thread,
      grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(project),
      context: { title: title.trim(), project_path: project, project_id: projectId, provider, model, permission_mode: permission, fleet_execute: fleetExecute, allow_thread_create: allowCreate } });
    if (result && peer.role === "controller") setThread(crypto.randomUUID());
  }
  return <details className="border-t border-line pt-3">
    <summary className="cursor-pointer font-medium">Desktop work</summary>
    <div className="mt-3 space-y-2">
      <p className="text-muted">Grant a project to create conversations from this device.</p>
      <label className="block">Conversation name<input className={input} value={title} maxLength={160} onChange={(event) => setTitle(event.target.value)} /></label>
      <details><summary className="cursor-pointer text-muted">Advanced thread identifiers</summary>
      <label className="block">Thread ID<input className={input} value={thread} onChange={(event) => setThread(event.target.value)} /></label>
      <label className="block">Project ID<input className={input} value={projectId} onChange={(event) => setProjectId(event.target.value)} /></label>
      </details>
      <fieldset className="space-y-2 border-t border-line pt-2"><legend>Project and permissions</legend>
        <p className="break-all">{project || "Choose a local project"}</p>
        <button type="button" className={button} disabled={busy || !window.goferDesktop?.workspace?.selectPath} onClick={chooseProject}>Choose allowed project</button>
        <label className="block">Provider<select className={input} value={provider} onChange={(event) => { setProvider(event.target.value); setPermission(event.target.value === "codex" ? "read-only" : "plan"); }}><option value="codex">Codex</option><option value="claude_code">Claude Code</option></select></label>
        <label className="block">Model<input className={input} value={model} onChange={(event) => setModel(event.target.value)} /></label>
        <label className="block">Local permission<select className={input} value={permission} onChange={(event) => setPermission(event.target.value)}>{(provider === "codex" ? ["read-only", "workspace-write"] : ["plan", "default"]).map((mode) => <option key={mode} value={mode}>{mode}</option>)}</select></label>
        {peer.role === "controller" && <label className="flex items-start gap-2"><input type="checkbox" checked={allowCreate} onChange={(event) => setAllowCreate(event.target.checked)} disabled={busy} />Allow new phone conversations in this project</label>}
        <details><summary className="cursor-pointer text-muted">Delegation to other desktops</summary>
        <label className="mt-2 flex items-start gap-2"><input type="checkbox" checked={fleetExecute} onChange={(event) => setFleetExecute(event.target.checked)} disabled={busy} aria-describedby={`fleet-grant-${peer.device_id}`} />Allow this thread to delegate work to paired desktops</label>
        <p id={`fleet-grant-${peer.device_id}`} className="text-muted">Each desktop requires its own project grant. Revoke thread access to remove delegation permission.</p></details>
        <button type="button" className={button} disabled={busy || !project || !thread || !model || !title.trim()} onClick={grantConversation}>{peer.role === "controller" ? "Create conversation for phone" : "Grant this thread access"}</button>
        {!project && <p className="text-muted">Choose a project to enable conversation creation.</p>}
        <details><summary className="cursor-pointer text-muted">Shared conversations ({grants.length})</summary><div className="mt-2 space-y-3">
        {grants.filter((grant) => grant.device_id === peer.device_id).map((grant) => <div key={grant.thread_id} className="break-all"><p>{grant.title || "Remote Rem"} · {grant.provider} / {grant.model} · {grant.permission_mode}</p><button type="button" className={button} disabled={busy} onClick={async () => { const result = await action({ action: "thread_history", device_id: peer.device_id, thread_id: grant.thread_id }); if (result) setHistory({ title: grant.title || "Remote Rem", messages: result.messages }); }}>View conversation</button><button type="button" className={button} disabled={busy || !window.goferDesktop?.workspace?.selectPath} onClick={() => offerFile(grant.thread_id, grant.project_path)}>Choose and offer a project file</button><p>Fleet delegation: {grant.fleet_execute === true ? "allowed" : "disabled"}</p><button type="button" className={button} disabled={busy} onClick={() => action({ action: "revoke_thread", device_id: peer.device_id, thread_id: grant.thread_id })}>Revoke thread access</button></div>)}
        </div></details>
      </fieldset>
      {history && <section aria-label="Phone conversation" className="space-y-2 rounded border border-line p-3"><h4 className="font-medium">{history.title}</h4>{history.messages.length ? history.messages.map((message, index) => <p key={index} className="whitespace-pre-wrap break-words"><strong>{message.role === "assistant" ? "Rem" : "You"}</strong><br />{message.content || message.body}</p>) : <p>No messages yet. Open this conversation on your phone and send a message.</p>}<button type="button" className={button} onClick={() => setHistory(null)}>Close conversation</button></section>}
      {peer.role === "desktop" && <fieldset className="space-y-2 border-t border-line pt-2"><legend>Send work to this desktop</legend>
        <p className="text-muted">Use the thread and project IDs granted on that desktop. A queued request has not necessarily run. Retry with the same request ID.</p>
        <label className="block">Request ID<input className={input} value={requestId} onChange={(event) => setRequestId(event.target.value)} /></label>
        <label className="block">Message<textarea className={input} value={text} maxLength={16000} rows={3} onChange={(event) => setText(event.target.value)} /></label>
        <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={busy || !text.trim()} onClick={() => action({ action: "send", kind: "job.submit", device_id: peer.device_id, thread_id: thread, project_id: projectId, request_id: requestId, text })}>Queue work</button><button type="button" className={button} disabled={busy} onClick={() => action({ action: "work_status", device_id: peer.device_id, request_id: requestId })}>Check result</button><button type="button" className={button} disabled={busy} onClick={() => { setRequestId(crypto.randomUUID()); setText(""); }}>New request</button></div>
        <details><summary>LAN address for reverse connections</summary><label className="block">Private IP address<input className={input} value={host} onChange={(event) => setHost(event.target.value)} /></label><label className="block">Port<input className={input} inputMode="numeric" value={port} onChange={(event) => setPort(event.target.value)} /></label><button type="button" className={button} disabled={busy || !host} onClick={() => action({ action: "set_endpoint", device_id: peer.device_id, endpoint: { host, port: Number(port) } })}>Save peer address</button><p className="text-muted">The saved identity pin still authenticates the connection.</p></details>
      </fieldset>}
      {error && <p role="alert" className="text-red-700">{error}</p>}
    </div>
  </details>;
}
