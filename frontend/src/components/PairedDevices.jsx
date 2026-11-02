import { useCallback, useEffect, useState } from "react";
import { apiUrl } from "../lib/api.js";
import { pollWithoutOverlap } from "../lib/statusPolling.js";

// Use the same compact controls and theme tokens as the surrounding settings.
const button = "rounded border border-line px-3 py-1.5 text-xs hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 disabled:opacity-50";
const primaryButton = "rounded border border-brand bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 disabled:opacity-50";
const input = "w-full rounded border border-line bg-white px-2 py-1.5 text-xs focus-visible:outline-indigo-500";
const card = "rounded-lg border border-line p-4";
const tones = {
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  info: "bg-info-soft text-info",
  muted: "bg-surface-muted text-muted",
};

function Pill({ tone, children }) {
  return <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>
    <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />{children}
  </span>;
}

// Matches the 8-character groups the phone shows, so codes are easy to compare.
function SecurityCode({ value, className = "" }) {
  const groups = String(value || "").match(/.{1,8}/g) || [];
  return <p className={`grid w-fit select-text grid-cols-4 gap-x-3 gap-y-1 font-mono text-[11px] tracking-wide ${className}`} aria-label={`Security code ${value}`}>
    {groups.map((group, index) => <span key={index}>{group}</span>)}
  </p>;
}

function Step({ number, title, children }) {
  return <li className="flex gap-3">
    <span aria-hidden="true" className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-surface-muted text-[11px] font-semibold">{number}</span>
    <div className="min-w-0 flex-1 space-y-2 pt-0.5"><h5 className="font-semibold">{title}</h5>{children}</div>
  </li>;
}

function Toggle({ checked, disabled, saving = false, title, description, hint, onChange }) {
  return <label aria-busy={saving} className={`flex items-start gap-4 py-3 ${disabled ? "cursor-default" : "cursor-pointer"}`}>
    <span className="min-w-0 flex-1"><span className="block font-medium">{title}</span>
      <span className="mt-0.5 block text-muted">{description}</span>
      {saving ? <span role="status" className="mt-1 block text-[11px] font-medium text-info">Saving…</span>
        : hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </span>
    <input type="checkbox" role="switch" className="peer sr-only" checked={checked} disabled={disabled} onChange={onChange} />
    <span aria-hidden="true" className={`relative mt-0.5 h-[18px] w-8 shrink-0 rounded-full border transition-[background-color,border-color] duration-150 peer-focus-visible:ring-2 peer-focus-visible:ring-brand/40 peer-disabled:opacity-50 ${checked ? "border-brand bg-brand" : "border-line bg-slate-100"}`}>
      <span className={`absolute left-0.5 top-0.5 h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-150 ease-out motion-reduce:transition-none ${checked ? "translate-x-3.5" : "translate-x-0"}`} />
    </span>
  </label>;
}

const deviceName = peer => peer.role === "controller" ? "Phone" : "Desktop";

function peerState(peer) {
  if (peer.state === "active") return ["success", "Paired"];
  if (peer.state === "confirmed") return ["info", "Finishing pairing"];
  if (peer.state === "pending") return ["warning", "Needs your confirmation"];
  if (peer.state === "outbound_pending") return ["info", "Waiting for desktop"];
  return ["muted", peer.state];
}

function countdown(seconds) {
  const left = Math.max(0, Math.round(seconds));
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

export default function PairedDevices() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [invite, setInvite] = useState(null);
  const [name, setName] = useState("Raticode Desktop");
  const [uri, setUri] = useState("");
  const [preview, setPreview] = useState(null);
  const [revoke, setRevoke] = useState(null);
  const [unpair, setUnpair] = useState(null);
  const [now, setNow] = useState(Date.now());
  const [result, setResult] = useState(null);
  const [syncError, setSyncError] = useState("");
  const [showPairing, setShowPairing] = useState(false);
  const [pairingMode, setPairingMode] = useState("phone");
  const [copied, setCopied] = useState(false);
  // The action and device of the change being saved, so its switch can say so.
  const [saving, setSaving] = useState("");
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
    const stop = pollWithoutOverlap(async signal => { setNow(Date.now()); await refresh(signal); },
      { signal: controller.signal, onError: failure => setError(failure.message) });
    return () => { controller.abort(); stop(); };
  }, [refresh]);
  // Tick every second while a QR code is showing so its countdown stays accurate.
  useEffect(() => {
    if (!invite) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [invite]);
  useEffect(() => {
    if (invite && (invite.expires_at * 1000 <= now)) setInvite(null);
  }, [invite, now]);

  async function action(body) {
    setBusy(true);
    setSaving(`${body.action}:${body.device_id || ""}`);
    setError("");
    try {
      const response = await fetch(apiUrl("/devices"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body), cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Device action failed.");
      if (body.action === "invite") { setInvite(result); setCopied(false); }
      if (body.action === "preview") setPreview(result);
      if (body.action === "pair") { setUri(""); setPreview(null); }
      if (body.action === "cancel_invitation") setInvite(null);
      if (body.action === "revoke") setRevoke(null);
      if (body.action === "unpair") setUnpair(null);
      if (["send", "work_status", "fleet_status", "offer_file"].includes(body.action)) setResult(result);
      await refresh();
      return result;
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); setSaving(""); }
  }

  async function copyInvitation() {
    try { await navigator.clipboard.writeText(invite.uri); setCopied(true); }
    catch { setError("The link could not be copied. Select it below and copy it instead."); }
  }

  const peers = status?.peers || [];
  const activePeers = peers.filter(peer => peer.state !== "revoked");
  const revokedPeers = peers.filter(peer => peer.state === "revoked");
  const grants = status?.grants || [];
  const fleet = status?.fleet || [];
  const indirect = fleet.filter(device => device.state === "indirect");
  // Pairing is a Feature Preview. While it is off, nothing here is shown.
  if (!status?.experimental_network) return null;
  return <section className="space-y-4 pt-4 text-xs leading-5" aria-label="Paired devices">
    <div className="flex items-start justify-between gap-3">
      <div><h3 className="text-sm font-semibold">Paired devices</h3>
        <p className="mt-0.5 text-muted">Chat with Rem from your phone and share work between your computers on the same network.</p></div>
      {status.enabled && <button type="button" className={`${showPairing ? button : primaryButton} shrink-0 whitespace-nowrap`} aria-expanded={showPairing}
        onClick={() => setShowPairing(!showPairing)}>{showPairing ? "Close setup" : "Pair a device"}</button>}
    </div>
    {status.enabled && <div className="flex flex-wrap gap-2" role="status">
      <Pill tone={status.lan_available ? "success" : "warning"}>{status.lan_available ? "Local network available" : "Local network unavailable"}</Pill>
      <Pill tone={status.rem_ready ? "success" : "warning"}>{status.rem_ready ? "Rem ready" : "Rem unavailable"}</Pill>
    </div>}
    {(error || status.error) && <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-danger">{error || status.error}</p>}
    {status.enabled && !status.rem_ready && <p role="alert" className="rounded-md bg-warning-soft px-3 py-2 text-warning">Restart the desktop backend to restore Rem messaging.</p>}
    {syncError && <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-danger">Thread sync needs attention: {syncError}</p>}
    {!status.enabled ? <div className={`${card} space-y-3`}>
      <h4 className="font-semibold">Use Rem from your phone</h4>
      <ol className="list-decimal space-y-1 pl-4 text-muted">
        <li>Turn on pairing for this computer.</li>
        <li>Scan a QR code with the Raticode app on your phone.</li>
        <li>Check that the security codes match on both devices.</li>
      </ol>
      <button type="button" className={primaryButton} disabled={busy} onClick={() => action({ action: "enable" })}>{saving === "enable:" ? "Setting up…" : "Set up device pairing"}</button>
    </div> : <>
      {showPairing && <section aria-label="Pair a device" className={`${card} space-y-4`}>
        <div className="space-y-2">
          <h4 className="text-sm font-semibold">What are you pairing?</h4>
          <div className="inline-flex rounded-md border border-line p-0.5" role="group" aria-label="Device to pair">
            {[["phone", "Phone"], ["desktop", "Another computer"]].map(([value, label]) => <button key={value} type="button" aria-pressed={pairingMode === value}
              className={`rounded px-3 py-1 text-xs font-medium ${pairingMode === value ? "bg-brand text-white" : "text-muted hover:bg-slate-100"}`}
              onClick={() => setPairingMode(value)}>{label}</button>)}
          </div>
        </div>
        <label className="block space-y-1"><span className="font-medium">Name this computer</span>
          <span className="block text-muted">Shown on the phone or computer you pair with.</span>
          <input className={input} value={name} maxLength={80} onChange={e => setName(e.target.value)} /></label>
        {!status.lan_available && <p role="status" className="rounded-md bg-warning-soft px-3 py-2 text-warning">This computer has no local network address. Connect it to your Wi-Fi network or set RATICODE_DEVICE_HOST, then restart Raticode.</p>}
        {pairingMode === "phone" ? <>
          <p className="text-muted">Your phone connects only over the same Wi-Fi network as this computer. There is no internet relay.</p>
          <ol className="space-y-4">
            <Step number={1} title="Scan the QR code with your phone">
              {!invite ? <>
                <p className="text-muted">In the Raticode app, choose Scan QR code.</p>
                <button type="button" className={primaryButton} disabled={busy || !status.lan_available || Boolean(status.error)} onClick={() => action({ action: "invite", name })}>Create QR code</button>
              </> : <div className="flex flex-wrap items-start gap-4">
                {/* Always black on white so phone cameras can read it in dark mode. */}
                <div className="pairing-qr shrink-0 rounded-lg p-2"><img src={invite.qr} width="240" height="240" className="block max-w-full" alt="One-time pairing invitation QR code" /></div>
                <div className="min-w-[12rem] flex-1 space-y-3">
                  <p><span className="font-medium">Expires in {countdown(invite.expires_at - now / 1000)}</span>
                    <span className="block text-muted">Works once. Keep this code private.</span></p>
                  <div className="flex flex-wrap gap-2">
                    <button type="button" className={button} disabled={busy} onClick={() => action({ action: "invite", name })}>New QR code</button>
                    <button type="button" className={button} disabled={busy} onClick={() => action({ action: "cancel_invitation" })}>Cancel invitation</button>
                  </div>
                  <details><summary className="cursor-pointer text-muted">Can&apos;t scan? Use the link</summary>
                    <div className="mt-2 space-y-2">
                      <textarea aria-label="Pairing invitation" readOnly className={input} value={invite.uri} rows={3} />
                      <button type="button" className={button} onClick={copyInvitation}>{copied ? "Copied" : "Copy link"}</button>
                    </div>
                  </details>
                </div>
              </div>}
            </Step>
            <Step number={2} title="Confirm the security code">
              <p className="text-muted">After you scan, your phone appears under Your devices. Check that its code matches the one on your phone, then confirm.</p>
            </Step>
          </ol>
        </> : <DesktopPairing status={status} invite={invite} now={now} busy={busy} name={name} copied={copied} uri={uri} preview={preview}
          action={action} copyInvitation={copyInvitation} setUri={value => { setUri(value); setPreview(null); }} />}
      </section>}
      <div className="space-y-2">
        <h4 className="font-semibold">Your devices</h4>
        {!activePeers.length && <p className={`${card} text-muted`}>No devices paired. Choose Pair a device to connect your phone or another computer.</p>}
        <ul className="space-y-3" aria-label="Device trust registry">
          {activePeers.map((peer, index) => {
            const sharing = (status.workspace_peers || []).includes(peer.device_id);
            const peerGrants = grants.filter(grant => grant.device_id === peer.device_id);
            const canCreate = peerGrants.some(grant => grant.allow_thread_create);
            const desktopControl = (status.desktop_control_peers || []).includes(peer.device_id);
            const remoteControl = desktopControl || (status.remote_control_peers || []).includes(peer.device_id);
            const evidence = fleet.find(device => device.device_id === peer.device_id) || {};
            const [tone, label] = peerState(peer);
            const needsSync = sharing ? "" : "Turn on thread sync first.";
            return <li className={`${card} space-y-3 ${peer.state === "pending" ? "border-warning/60" : ""}`} key={peer.device_id}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><h5 className="text-sm font-semibold">{peer.name || `${deviceName(peer)}${activePeers.length > 1 ? ` ${index + 1}` : ""}`}</h5>
                  <p className="text-[11px] text-muted">{peer.last_seen ? `Last connected ${new Date(peer.last_seen * 1000).toLocaleString()}` : "Not connected yet"}</p></div>
                <Pill tone={tone}>{label}</Pill>
              </div>
              {peer.state === "pending" && <div className="space-y-3 rounded-md bg-warning-soft p-3">
                <p className="font-medium">Does this code match the one on your {peer.role === "controller" ? "phone" : "other desktop"}?</p>
                <SecurityCode value={peer.fingerprint} className="text-ink" />
                {peer.role === "controller" && <p className="text-muted">Pairing syncs your desktop threads and projects with your phone. Desktop approvals still apply.</p>}
                <button type="button" className={primaryButton} disabled={busy} onClick={() => action({ action: "confirm", device_id: peer.device_id, fingerprint: peer.fingerprint })}>Identity matches, confirm pairing</button>
              </div>}
              {peer.state === "confirmed" && <p className="text-muted">Confirmed, waiting for acknowledgment. Keep the phone open to finish pairing.</p>}
              {peer.state === "active" && peer.role === "controller" && <div className="border-t border-line">
                <div className="divide-y divide-line">
                  <Toggle checked={sharing} disabled={busy} saving={saving === `share_workspace:${peer.device_id}`} title="Sync desktop threads with this phone"
                    description="Sync current and future threads and history, use your desktop projects and start new conversations. Desktop approvals still apply."
                    hint={sharing
                      ? `${peerGrants.length} ${peerGrants.length === 1 ? "thread" : "threads"} shared. ${canCreate ? "New conversations allowed." : "No project available for new conversations yet. Raticode finishes syncing your projects while it runs, including in the background."}`
                      : "Thread sync is off. Turn it on to sync your desktop conversations and projects."}
                    onChange={event => action({ action: "share_workspace", device_id: peer.device_id, enabled: event.target.checked })} />
                  <Toggle checked={remoteControl} disabled={busy || !sharing || desktopControl} saving={saving === `remote_control:${peer.device_id}`} title="Allow approvals and swarm controls"
                    description="Answer workflow and swarm approvals, message swarms, and pause, resume or stop them."
                    hint={needsSync || (desktopControl ? "Included with Use Raticode from this phone." : "")}
                    onChange={event => action({ action: "remote_control", device_id: peer.device_id, enabled: event.target.checked })} />
                  <Toggle checked={desktopControl} disabled={busy || !sharing} saving={saving === `desktop_control:${peer.device_id}`} title="Use Raticode from this phone"
                    description="Also talk to Rem and manage its tasks, run organizations, start swarms, view, run and stop workflows, check usage and change display settings. Work still runs on this computer under its approval policies. Files, terminals, the browser, credentials and agent permissions stay here."
                    hint={needsSync}
                    onChange={event => action({ action: "desktop_control", device_id: peer.device_id, enabled: event.target.checked })} />
                </div>
              </div>}
              {peer.state === "active" && peer.role === "desktop" && <div className="border-t border-line">
                <Toggle checked={(status.thread_host_peers || []).includes(peer.device_id)} disabled={busy} saving={saving === `host_threads:${peer.device_id}`}
                  title="Run threads from this desktop"
                  description={`Let ${peer.name || "this desktop"} start Rem threads that run on this computer, in the projects open here and with this computer's provider sign-ins. It chooses the provider, model and permissions, and Stop works from there. Off by default.`}
                  hint="On that desktop, choose this computer in Run on, next to the permissions menu, when you start a thread. Turning this off ends the threads it started here."
                  onChange={event => action({ action: "host_threads", device_id: peer.device_id, enabled: event.target.checked })} />
              </div>}
              {peer.state === "active" && peer.role === "desktop" && <p role="status" className="text-muted">
                {evidence.reachability === "reachable" ? "Answering now." : "Not answering right now, or not checked yet."}{" "}
                {evidence.endpoint_known ? "Address saved; it updates on its own." : "Address not known yet. It is learned the next time that desktop connects here."}</p>}
              {peer.state === "active" && peer.role === "desktop" && <PeerWork peer={peer} busy={busy} action={action} grants={peerGrants} shared={(evidence.shared_threads || []).filter(item => item.project_id)} />}
              <div className="space-y-3 border-t border-line pt-3">
                <div className="flex flex-wrap items-center gap-2">
                  {peer.role === "desktop" && ["active", "outbound_pending"].includes(peer.state) && <button type="button" className={button} disabled={busy} onClick={() => action({ action: "reconnect", device_id: peer.device_id })}>Reconnect</button>}
                  <button type="button" className={button} disabled={busy} onClick={() => { setUnpair(peer.device_id); setRevoke(null); }}>Unpair</button>
                  <button type="button" className={`${button} text-danger`} disabled={busy} onClick={() => { setRevoke(peer.device_id); setUnpair(null); }}>Revoke</button>
                </div>
                {unpair === peer.device_id && <div role="group" aria-label="Confirm unpairing" className="space-y-2 rounded-md bg-surface-muted p-3">
                  <p>Remove pairing and shared access? You can pair again with a new QR code.</p>
                  <div className="flex flex-wrap gap-2">
                    <button type="button" className={button} disabled={busy} onClick={() => action({ action: "unpair", device_id: peer.device_id })}>Unpair device</button>
                    <button type="button" className={button} onClick={() => setUnpair(null)}>Keep paired</button>
                  </div>
                </div>}
                {revoke === peer.device_id && <div role="group" aria-label="Confirm revocation" className="space-y-2 rounded-md bg-danger-soft p-3">
                  <p>Block this device from reconnecting or pairing again until you remove it from the revoked list?</p>
                  <div className="flex flex-wrap gap-2">
                    <button type="button" className={`${button} text-danger`} disabled={busy} onClick={() => action({ action: "revoke", device_id: peer.device_id })}>Revoke device</button>
                    <button type="button" className={button} onClick={() => setRevoke(null)}>Keep device</button>
                  </div>
                </div>}
                {peer.state !== "pending" && <details className="text-muted"><summary className="cursor-pointer">Security code</summary>
                  <SecurityCode value={peer.fingerprint} className="mt-2" />
                  <p className="mt-2">Paired means this device is trusted, not that it is connected right now.</p>
                </details>}
              </div>
            </li>;
          })}
        </ul>
      </div>
      {indirect.length > 0 && <section aria-label="Through other desktops" className="space-y-2 border-t border-line pt-3">
        <h4 className="font-semibold">Through other desktops</h4>
        <p className="text-muted">Paired with another desktop in your fleet, not with this one. Your phone sees them too. Pair directly to send them work from here.</p>
        <ul className="space-y-1">{indirect.map(device => <li key={device.device_id}>
          {device.name || deviceName(device)} <span className="text-muted">via {device.via_name || "a paired desktop"} · {device.reachability === "reachable" ? "answering" : "status unknown"}</span>
        </li>)}</ul>
      </section>}
      {revokedPeers.length > 0 &&<details className="border-t border-line pt-3">
        <summary className="cursor-pointer font-medium">Revoked devices ({revokedPeers.length})</summary>
        <div className="mt-3 space-y-3">
          <p className="text-muted">Removing a device allows fresh QR pairing. Its old connection and shared access stay disabled.</p>
          <ul className="space-y-3" aria-label="Revoked devices">
            {revokedPeers.map(peer => <li key={peer.device_id} className={`${card} space-y-2`}>
              <p className="font-medium">{deviceName(peer)}</p>
              <SecurityCode value={peer.fingerprint} className="text-muted" />
              <button type="button" className={button} disabled={busy} onClick={() => action({ action: "remove_revoked", device_id: peer.device_id })}>Remove from revoked list</button>
            </li>)}
          </ul>
        </div>
      </details>}
      <TailscaleAccess tailnet={status.tailnet} port={status.port} busy={busy} saving={saving} action={action} />
      <details className="border-t border-line pt-3">
        <summary className="cursor-pointer font-medium">Connection and troubleshooting</summary>
        <div className="mt-3 space-y-3 text-muted">
          {status.lan_available && status.lan_host ? <>
            <p>Local address <span className="select-text font-mono text-ink">{status.lan_host}:{status.port}</span></p>
            <p>If your phone cannot connect:</p>
            <ul className="list-disc space-y-1 pl-4">
              <li>Check that both devices use the same Wi-Fi network.</li>
              <li>Enter the address above in the phone&apos;s Desktop settings.</li>
              <li>Make sure the firewall allows port {status.port}.</li>
            </ul>
          </> : <p>Direct LAN is unavailable. Check the desktop network address.</p>}
          <p>{status.dispatch_error ? "Rem could not read its message queue. Check desktop storage and restart the backend." : "Project permissions and approvals stay on desktop."}</p>
          <details><summary className="cursor-pointer">Desktop identity and security</summary>
            <SecurityCode value={status.fingerprint} className="mt-2" />
            <p className="mt-2">{status.notice || "Independent security review is pending."}</p>
          </details>
          <button type="button" className={button} disabled={busy} onClick={() => action({ action: "fleet_status" })}>Refresh fleet status</button>
        </div>
      </details>
      {result && <div aria-label="Fleet result" role="status" className="space-y-2 border-t border-line pt-3">
        {result.state && <p>Request {result.state}</p>}
        <button type="button" className={button} onClick={() => setResult(null)}>Dismiss result</button>
        {result.type === "file.offer" && <p>File offered: {result.payload?.name}. Waiting for the paired device.</p>}
        {result.devices?.map(device => <div key={device.device_id}><p className="break-all">{device.name || device.role}{device.via ? ` (via ${device.via_name || "a paired desktop"})` : ""}: {device.reachability}</p><p>{device.running_jobs == null ? "Running work unknown" : `${device.running_jobs.length} reported running jobs`}</p></div>)}
        {result.events?.map(event => <p key={event.id} className="whitespace-pre-wrap break-words">{event.payload?.text || event.payload?.message || event.payload?.state || event.type}</p>)}
      </div>}
    </>}
  </section>;
}

// What the backend found when it last checked this computer's Tailscale.
const TAILNET_STATES = {
  off: ["muted", "Off", ""],
  checking: ["info", "Checking", "Checking Tailscale on this computer…"],
  listening: ["success", "Listening", ""],
  not_installed: ["warning", "Not found", "Raticode can't find Tailscale on this computer. Install it and sign in (steps 2 and 3), then choose Check again."],
  needs_login: ["warning", "Sign in needed", "Tailscale is installed but not signed in. Sign in with your own account (step 2), then choose Check again."],
  needs_approval: ["warning", "Needs approval", "This computer is waiting for approval in your Tailscale admin console. Approve it there, then choose Check again."],
  stopped: ["warning", "Tailscale off", "Tailscale is turned off or not running on this computer. Turn it on, then choose Check again."],
  starting: ["info", "Starting", "Tailscale is starting. Raticode checks again on its own."],
  not_detected: ["warning", "No address", "Tailscale is running, but Raticode found no Tailscale address on this computer."],
  unavailable: ["warning", "Unknown", "Raticode couldn't read Tailscale's status on this computer."],
  invalid_address: ["warning", "Wrong address", "RATICODE_TAILSCALE_HOST must be a Tailscale address (100.64.0.0/10 or fd7a:115c:a1e0::/48)."],
  error: ["danger", "Problem", ""],
};

function tailnetPolicy(host, port) {
  return JSON.stringify({ grants: [{ src: ["PHONE_TAILSCALE_ADDRESS"], dst: [host || "THIS_COMPUTER_TAILSCALE_ADDRESS"], ip: [`tcp:${port || "PORT"}`] }] }, null, 2);
}

// Optional reach away from home through the user's own Tailscale network. Raticode
// never signs in to Tailscale or edits its policy; it only listens on this
// computer's tailnet address after the user opts in.
function TailscaleAccess({ tailnet, port, busy, saving, action }) {
  const info = tailnet || { enabled: false, state: "off" };
  const [open, setOpen] = useState(Boolean(info.enabled));
  const [copied, setCopied] = useState(false);
  const [tone, label, guidance] = TAILNET_STATES[info.state] || TAILNET_STATES.unavailable;
  const checking = saving === "tailnet:" || saving === "tailnet_check:";
  const policy = tailnetPolicy(info.host, info.port || port);
  async function copyPolicy() {
    try { await navigator.clipboard.writeText(policy); setCopied(true); }
    catch { setCopied(false); }
  }
  return <section aria-label="Away from home with Tailscale" className="space-y-3 border-t border-line pt-3">
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0"><h4 className="font-semibold">Away from home with Tailscale</h4>
        <p className="mt-0.5 text-muted">Optional. Reach this computer from your phone on mobile data or other Wi-Fi through your own private Tailscale network. Nothing is opened to the internet and no router ports are forwarded.</p></div>
      {info.enabled ? <Pill tone={tone === "danger" ? "warning" : tone}>{label}</Pill>
        : <button type="button" className={`${button} shrink-0 whitespace-nowrap`} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Set up"}</button>}
    </div>
    {(open || info.enabled) && <div className="space-y-3">
      <div className="border-y border-line">
        <Toggle checked={Boolean(info.enabled)} disabled={busy} saving={saving === "tailnet:"} title="Also listen on this computer's Tailscale address"
          description="Off by default. The local Wi-Fi connection keeps working. Paired phones still need their security code and this computer's confirmation, and messages stay end-to-end encrypted inside the Tailscale tunnel."
          hint={info.enabled ? "" : "Turn on after Tailscale is installed and signed in here (steps 1 to 4)."}
          onChange={event => action({ action: "tailnet", enabled: event.target.checked })} />
      </div>
      {info.enabled && <div role="status" className="space-y-2">
        {info.state === "listening" ? <p>Listening on <span className="select-text font-mono text-ink">{info.host.includes(":") ? `[${info.host}]` : info.host}:{info.port}</span>
          {info.dns_name && <span className="text-muted"> ({info.dns_name})</span>}. Paired phones with Tailscale turned on in Raticode learn this address the next time they sync.</p>
          : info.state === "error" ? <p className="rounded-md bg-danger-soft px-3 py-2 text-danger">{info.error === "listen_failed" ? `Raticode couldn't listen on the Tailscale address. Another program may already use port ${port} there.` : "Checking Tailscale failed. Choose Check again."}</p>
            : <p className={tone === "warning" ? "rounded-md bg-warning-soft px-3 py-2 text-warning" : "text-muted"}>{checking && info.state !== "checking" ? "Checking Tailscale on this computer…" : guidance}</p>}
        <button type="button" className={button} disabled={busy} onClick={() => action({ action: "tailnet_check" })}>{saving === "tailnet_check:" ? "Checking…" : "Check again"}</button>
      </div>}
      <details open={!info.enabled || info.state !== "listening"}>
        <summary className="cursor-pointer font-medium">Setup steps</summary>
        <p className="mt-2 text-muted">You set these up in your own Tailscale account. Raticode doesn&apos;t sign in to Tailscale, needs no API key or auth key, and never changes your Tailscale settings, access rules, firewall or router.</p>
        <ol className="mt-3 space-y-4">
          <Step number={1} title="Use your own Tailscale account">
            <p className="text-muted">Sign up at tailscale.com with Google, Microsoft, GitHub, Apple or another sign-in you trust, and turn on two-step sign-in there. Anyone who controls that account can add devices to your network.</p>
          </Step>
          <Step number={2} title="Install Tailscale on this computer and sign in">
            <p className="text-muted">On Linux, install Tailscale, run <code className="font-mono text-ink">sudo tailscale up</code> and open the sign-in link it prints. On macOS or Windows, install the Tailscale app and sign in. Raticode reads only this computer&apos;s Tailscale address.</p>
          </Step>
          <Step number={3} title="Install Tailscale on your phone and sign in">
            <p className="text-muted">Use the same account. In Raticode Mobile, turn on its Tailscale option. That needs a Raticode Mobile version with Tailscale support. Pair at home first; pairing still happens on your Wi-Fi.</p>
          </Step>
          <Step number={4} title="Let your phone reach only Raticode's port">
            <p className="text-muted">A new Tailscale network lets every device reach every port on every other device. In the Tailscale admin console, open Access controls, remove the default rule that allows everything (<code className="font-mono">&quot;src&quot;: [&quot;*&quot;], &quot;dst&quot;: [&quot;*:*&quot;]</code>), and add only this grant. Replace PHONE_TAILSCALE_ADDRESS with the address the Tailscale app shows for your phone{info.host ? "" : ", and use the address Tailscale shows for this computer"}. Keep other rules you rely on.</p>
            <pre aria-label="Tailscale access rule" className="select-text overflow-x-auto rounded-md bg-surface-muted p-3 font-mono text-[11px] text-ink">{policy}</pre>
            <button type="button" className={button} onClick={copyPolicy}>{copied ? "Copied" : "Copy access rule"}</button>
          </Step>
          <Step number={5} title="Leave Tailscale's sharing features off here">
            <p className="text-muted">Don&apos;t turn on Funnel, Serve, subnet routes or exit node on this computer. Raticode needs none of them, and each one exposes more than this one port.</p>
          </Step>
        </ol>
      </details>
      <details>
        <summary className="cursor-pointer font-medium">Check that it works</summary>
        <ul className="mt-2 list-disc space-y-1 pl-4 text-muted">
          <li>Turn Wi-Fi off on your phone, keep Tailscale on, open Raticode and send Rem a message.</li>
          <li>With Tailscale off on the phone, Raticode Mobile should fail to connect away from home.</li>
          <li>From your phone, only port {info.port || port || "PORT"} on this computer should answer over Tailscale.</li>
          <li>Your router should have no port forwards to this computer.</li>
        </ul>
      </details>
    </div>}
  </section>;
}

const INVITATION_PREFIX = "raticode://pair?v=2&data=";

// Two computers pair with a link: one creates it, the other pastes it. Either can go first.
function DesktopPairing({ status, invite, now, busy, name, copied, uri, preview, action, copyInvitation, setUri }) {
  const pasted = uri.trim();
  const looksValid = pasted.startsWith(INVITATION_PREFIX);
  return <div className="space-y-4">
    <p className="text-muted">Both computers need Raticode open on the same network. One computer creates an invitation link, and you paste that link on the other. It doesn&apos;t matter which goes first. Once paired, either one can run work the other sends.</p>
    <section aria-label="Invite another computer" className="space-y-3 rounded-md border border-line p-3">
      <h5 className="font-semibold">A. Invite from this computer</h5>
      <ol className="space-y-4">
        <Step number={1} title="Create an invitation link">
          {!invite ? <button type="button" className={primaryButton} disabled={busy || !status.lan_available || Boolean(status.error)}
            onClick={() => action({ action: "invite", name })}>Create invitation link</button>
            : <div className="space-y-2">
              <textarea aria-label="Desktop invitation link" readOnly className={`${input} font-mono`} value={invite.uri} rows={3} onFocus={event => event.target.select()} />
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" className={primaryButton} onClick={copyInvitation}>{copied ? "Copied" : "Copy invitation link"}</button>
                <button type="button" className={button} disabled={busy} onClick={() => action({ action: "invite", name })}>New link</button>
                <button type="button" className={button} disabled={busy} onClick={() => action({ action: "cancel_invitation" })}>Cancel invitation</button>
              </div>
              <p className="text-muted">Expires in {countdown(invite.expires_at - now / 1000)}. Works once. Send it only to your own computer.</p>
            </div>}
        </Step>
        <Step number={2} title="Paste it on the other computer">
          <p className="text-muted">On the other computer, open Settings, Devices, then Pair a device. Choose Another computer and paste the link under B. Paste an invitation.</p>
        </Step>
        <Step number={3} title="Confirm the request here">
          <p className="text-muted">The other computer appears under Your devices. Confirm it when its code matches the code the other computer shows. This computer&apos;s code is:</p>
          <SecurityCode value={status.fingerprint} />
        </Step>
      </ol>
    </section>
    <section aria-label="Paste an invitation" className="space-y-3 rounded-md border border-line p-3">
      <h5 className="font-semibold">B. Paste an invitation</h5>
      <p className="text-muted">Use this when the other computer created the link (A. on that computer).</p>
      <label className="block space-y-1"><span className="font-medium">Invitation link</span>
        <textarea className={`${input} font-mono`} rows={3} value={uri} onChange={e => setUri(e.target.value)} placeholder={`${INVITATION_PREFIX}…`} /></label>
      {pasted && !looksValid && <p role="status" className="text-warning">This isn&apos;t an invitation link. It starts with {INVITATION_PREFIX.slice(0, 15)}. Copy it again with Copy invitation link on the other computer.</p>}
      <button type="button" className={button} disabled={busy || !looksValid} onClick={() => action({ action: "preview", uri: pasted })}>Review identity</button>
      {preview && <div className="space-y-2 rounded-md bg-surface-muted p-3"><p>Check that this code matches the one on {preview.name}, shown in its pairing setup.</p><SecurityCode value={preview.fingerprint} /><button type="button" className={primaryButton} disabled={busy} onClick={() => action({ action: "pair", uri: pasted, fingerprint: preview.fingerprint })}>Identity matches, request pairing</button></div>}
      {status.outbound === "awaiting_peer_confirmation" && <div className="space-y-2"><p>Waiting for you to confirm on the other computer, under Your devices.</p><button type="button" className={button} onClick={() => action({ action: "cancel_pairing" })}>Cancel pairing</button></div>}
      {status.outbound === "trust_saved" && <p role="status" className="text-success">Paired. The other computer is listed under Your devices.</p>}
      {status.outbound === "failed" && <p role="status">Pairing did not finish. The link may have expired or was already used. Create a new link on the other computer and try again.</p>}
    </section>
  </div>;
}

function PeerWork({ peer, busy, action, grants, shared = [] }) {
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
  const [target, setTarget] = useState("");
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
        <p className="text-muted">Choose a thread that desktop shared with this one. A queued request has not necessarily run. Retry with the same request ID.</p>
        {shared.length ? <label className="block">Shared thread<select className={input} value={target} onChange={(event) => setTarget(event.target.value)}>
          <option value="">Choose a shared thread</option>
          {shared.map(item => <option key={item.thread_id} value={item.thread_id}>{item.title} · {item.provider} / {item.model}</option>)}
        </select></label> : <p className="text-muted">That desktop has not shared a thread with this one yet. Grant one in its Desktop work settings.</p>}
        <label className="block">Request ID<input className={input} value={requestId} onChange={(event) => setRequestId(event.target.value)} /></label>
        <label className="block">Message<textarea className={input} value={text} maxLength={4000} rows={3} onChange={(event) => setText(event.target.value)} /></label>
        <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={busy || !text.trim()} onClick={() => { const chosen = shared.find(item => item.thread_id === target); action({ action: "send", kind: "job.submit", device_id: peer.device_id, thread_id: chosen ? chosen.thread_id : thread, project_id: chosen ? chosen.project_id : projectId, request_id: requestId, text }); }}>Queue work</button><button type="button" className={button} disabled={busy} onClick={() => action({ action: "work_status", device_id: peer.device_id, request_id: requestId })}>Check result</button><button type="button" className={button} disabled={busy} onClick={() => { setRequestId(crypto.randomUUID()); setText(""); }}>New request</button></div>
        <details><summary>Set this desktop&apos;s address by hand</summary><p className="text-muted">Usually not needed: paired desktops share their addresses when they connect.</p><label className="block">Private IP address<input className={input} value={host} onChange={(event) => setHost(event.target.value)} /></label><label className="block">Port<input className={input} inputMode="numeric" value={port} onChange={(event) => setPort(event.target.value)} /></label><button type="button" className={button} disabled={busy || !host} onClick={() => action({ action: "set_endpoint", device_id: peer.device_id, endpoint: { host, port: Number(port) } })}>Save peer address</button><p className="text-muted">The saved identity pin still authenticates the connection.</p></details>
      </fieldset>}
      {error && <p role="alert" className="text-red-700">{error}</p>}
    </div>
  </details>;
}
