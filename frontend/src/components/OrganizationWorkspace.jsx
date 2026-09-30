import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Building2, History, Plus, Download, Pause, Play, Users, ListTodo, Activity, Settings, Folder, GitBranch, Check, X, ChevronRight, Upload, Clock, ShieldCheck } from "lucide-react";
import { organizationRequest, teamMembers, projectPeople, employeeTree, eligibleManagers, historyByDate, configurationChanges, newEmployee, initials, plural, employeeStatus, STATUS_LABELS, TASK_STATUSES, needsApproval, isOpenTask, taskGroups, actorLabel, changeLabel, activityEntries, usageCost, relativeDay, filterWork, appendActivity } from "../lib/organizations.js";
import { startPolling } from "../lib/refresh.js";
import { ProviderModelEffortFields, useProviderCapabilities } from "./ProviderModelEffortFields.jsx";
import { providerPermissionDefault, providerPermissionOptions } from "../lib/providerPermissions.js";
import RemResources, { remResourceError } from "./RemResources.jsx";
import OrganizationOperations, { TaskRunControls } from "./OrganizationOperations.jsx";
import OrganizationChart from "./OrganizationChart.jsx";
import "./OrganizationWorkspace.css";

const SECTIONS = [["people", "Employees", Users], ["chart", "Org chart", GitBranch], ["owned-projects", "Projects", Folder], ["work", "Work", ListTodo], ["operations", "Operations", Settings], ["history", "Configuration history", History], ["activity", "Activity", Activity]];
const STATES = TASK_STATUSES;
const NEW_COLLECTION = Symbol("new collection");
function Field({ label, children }) { return <label className="org-field"><span>{label}</span>{children}</label>; }
function TextField({ label, value, onChange, multiline = false, ...props }) {
  const Input = multiline ? "textarea" : "input";
  return <Field label={label}><Input value={value ?? ""} onChange={event => onChange(event.target.value)} {...props} /></Field>;
}
// Drafts stay strings throughout editing; conversion happens only on submit.
function NumberField({ label, value, onChange, ...props }) { return <TextField label={label} inputMode="decimal" value={value} onChange={onChange} {...props} />; }
function Status({ state, children }) { return <span className="org-status" data-state={state}><i aria-hidden="true" />{children || STATUS_LABELS[state] || state.charAt(0).toUpperCase() + state.slice(1)}</span>; }
function Empty({ icon: Icon = Users, title, children, action }) {
  return <div className="org-empty"><Icon size={28} aria-hidden="true" /><h3>{title}</h3><p>{children}</p>{action}</div>;
}
function EditorFrame({ children, onBack, label }) {
  const ref = useRef(null);
  useEffect(() => { ref.current?.focus(); ref.current?.scrollIntoView({ block: "start" }); }, []);
  return <div className="org-editor"><button ref={ref} className="org-button org-back" type="button" onClick={onBack}><ArrowLeft size={14} />{label}</button>{children}</div>;
}

export default function OrganizationWorkspace({ rootPath, organizationId, projectOptions = [], defaults = {}, memorySettings = {}, onSelect, onClose }) {
  const [organization, setOrganization] = useState(null);
  const [section, setSection] = useState("people");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState(organizationId === "new" ? "organization" : null);
  const [employeeId, setEmployeeId] = useState(null);
  const [collectionId, setCollectionId] = useState(null);
  const [history, setHistory] = useState([]);
  const [events, setEvents] = useState([]);
  const [taskId, setTaskId] = useState(null);
  const [restore, setRestore] = useState(null);
  const [restoreReason, setRestoreReason] = useState("");
  const scroll = useRef(null);
  const readVersion = useRef("");
  const activityCursor = useRef(0);
  const historyRevision = useRef(null);
  const [filters, setFilters] = useState({ query: "", assignee: "", project: "", attention: false });
  const [workPage, setWorkPage] = useState(0);
  useEffect(() => { readVersion.current = ""; activityCursor.current = 0; historyRevision.current = null; setEvents([]); setOrganization(null); }, [organizationId]);
  const revision = organization?.revision;
  useEffect(() => {
    if (organizationId === "new") return undefined;
    let cancelled = false;
    const stop = startPolling(async () => {
      try {
        const data = await organizationRequest("", "read", organizationId, { sinceVersion: readVersion.current });
        if (!cancelled && !data.notModified) { setOrganization(data); readVersion.current = data.version; }
        if (section === "history" && historyRevision.current !== revision) { const revisions = await organizationRequest(organizationId === "new" ? rootPath : "", "history", organizationId); if (!cancelled) { setHistory(revisions); historyRevision.current = revision; } }
        if (section === "activity") {
          const all = []; let cursor = activityCursor.current, page;
          do { page = await organizationRequest(organizationId === "new" ? rootPath : "", "events", organizationId, { after: cursor }); all.push(...page); cursor = page.at(-1)?.sequence || cursor; } while (!cancelled && page.length === 500);
          if (!cancelled) { activityCursor.current = cursor; setEvents(previous => appendActivity(previous, all)); }
        }
      } catch (failure) { if (!cancelled) setError(failure.message); }
    }, { interval: 3000, immediate: true });
    return () => { cancelled = true; stop(); };
  }, [rootPath, organizationId, section, revision]);
  async function act(action, params = {}) {
    setBusy(true); setError(""); setNotice("");
    try {
      const data = await organizationRequest(organizationId === "new" ? rootPath : "", action, organizationId === "new" ? undefined : organizationId, params);
      if (["create", "import", "import_github"].includes(action)) onSelect(data.id);
      else if (["configure", "restore", "control"].includes(action)) setOrganization(data);
      else if (!["export", "import_preview", "import_github_preview"].includes(action)) setOrganization(await organizationRequest(organizationId === "new" ? rootPath : "", "read", organizationId));
      const messages = { configure: "Changes saved. Check the operating status for affected work.", restore: "Configuration restored. Employees are paused.", task_create: "Task created.", task_update: "Task saved.", comment: "Comment saved.", wake: "Assignment added to the work queue.", approve: "Plan approved." };
      if (messages[action]) setNotice(messages[action]);
      window.dispatchEvent(new CustomEvent("gofer:organizations-changed"));
      return data;
    } catch (failure) { setError(failure.message); return null; }
    finally { setBusy(false); }
  }
  const config = organization?.config;
  const tasks = organization?.runtime.tasks || [];
  const employee = config?.employees.find(e => e.id === employeeId) || config?.employees[0];
  const selectedTask = tasks.find(t => t.id === taskId);
  const filteredWork = filterWork(tasks, filters);
  const pageCount = Math.max(1, Math.ceil(filteredWork.length / 50));
  const currentPage = Math.min(workPage, pageCount - 1);
  const visibleWork = filteredWork.slice(currentPage * 50, (currentPage + 1) * 50);
  const filter = values => { setFilters(f => ({ ...f, ...values })); setWorkPage(0); };
  const running = organization?.runtime.state === "running";
  const openCount = tasks.filter(isOpenTask).length;
  const approvals = tasks.filter(t => isOpenTask(t) && needsApproval(t)).length;
  function navigate(id) { setCollectionId(null); setSection(id); setEditor(null); setRestore(null); scroll.current?.scrollTo(0, 0); }
  function openCollection(kind, id = null) { navigate(kind); setCollectionId(id); }
  function edit(id) { setEditor(id); setNotice(""); }
  async function saveConfig(next, reason, expectedRevision) {
    const result = await act(organization ? "configure" : "create", { config: next, reason, expectedRevision });
    if (result) { setEditor(null); scroll.current?.scrollTo(0, 0); }
    return result;
  }
  async function download() {
    const data = await act("export");
    if (!data) return;
    const bytes = Uint8Array.from(atob(data.zip), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
    const a = document.createElement("a"); a.href = url; a.download = `${config.slug}.zip`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section className="org-workspace" aria-label="Organization workspace">
    <header className="org-toolbar">
      <button className="org-button org-icon-button" type="button" aria-label="Back to editor" title="Back to editor" onClick={onClose}><ArrowLeft size={17} /></button>
      <div className="org-toolbar-title"><Building2 size={18} aria-hidden="true" /><h2>{config?.name || (organizationId === "new" ? "New organization" : "Organization")}</h2></div>
      {organization && <div className="org-toolbar-actions"><Status state={organization.runtime.state} />
        <button className={`org-button ${running ? "" : "org-primary"}`} type="button" disabled={busy} onClick={() => act("control", { state: running ? "paused" : "running" })}>{running ? <Pause size={14} /> : <Play size={14} />}{running ? "Pause" : "Start work"}</button>
        <button className="org-button org-export" type="button" disabled={busy} onClick={download}><Download size={14} />Export template</button>
        <button className="org-button org-icon-button" type="button" aria-label="Organization settings" title="Organization settings" aria-pressed={editor === "organization"} disabled={busy} onClick={() => edit("organization")}><Settings size={17} /></button>
      </div>}
    </header>
    {organization && <nav className="org-tabs" aria-label="Organization sections">{SECTIONS.map(([id, label, Icon]) => <button type="button" key={id} aria-label={label} aria-current={!editor && (section === id || id === "chart" && ["teams", "projects"].includes(section)) ? "page" : undefined} onClick={() => navigate(id)}><Icon size={15} aria-hidden="true" />{label}{id === "work" && approvals > 0 && <span className="org-count" aria-label={`${approvals} awaiting approval`}>{approvals}</span>}</button>)}</nav>}
    {error && <div className="org-banner org-error" role="alert"><span>{error}</span><button className="org-button org-icon-button" type="button" aria-label="Dismiss error" onClick={() => setError("")}><X size={15} /></button></div>}
    {notice && <div className="org-banner org-notice" role="status"><Check size={16} aria-hidden="true" /><span>{notice}</span><button className="org-button org-icon-button" type="button" aria-label="Dismiss notification" onClick={() => setNotice("")}><X size={15} /></button></div>}
    {organization?.runtime.pauseReason && !notice && <div className="org-banner org-pause" role="status"><Pause size={14} aria-hidden="true" /><span>{organization.runtime.pauseReason}</span></div>}
    <div ref={scroll} className="org-scroll"><div className="org-content">
      {editor === "organization" ? <EditorFrame onBack={() => organization ? setEditor(null) : onClose()} label={organization ? "Back to organization" : "Back to editor"}>
        <OrganizationEditor key={organizationId} rootPath={rootPath} config={config} tasks={tasks} memorySettings={memorySettings} revision={revision} busy={busy} onSave={saveConfig} onCancel={() => organization ? setEditor(null) : onClose()} />
        {!organization && <PackageImport busy={busy} onAction={act} />}
      </EditorFrame> : !organization ? <p role="status" className="org-loading">Loading organization...</p> : <>
        <div className="org-heading"><p>{config.description || "Define the company purpose in Settings."}</p><div className="org-meta"><small><GitBranch size={13} />Revision {revision}</small><small><Folder size={13} />{plural(config.projectRoots.length, "project")}</small></div></div>
        {section === "people" && (editor === "new-employee" || editor === "employee" ? <EditorFrame key={editor} label="Back to employees" onBack={() => setEditor(null)}><EmployeeEditor employee={editor === "employee" ? employee : null} config={config} tasks={tasks} revision={revision} defaults={defaults} busy={busy} onSave={async (...args) => { const result = await saveConfig(...args); if (result && editor === "new-employee") setEmployeeId(result.config.employees.at(-1)?.id); return result; }} onCancel={() => setEditor(null)} /></EditorFrame> : <>
          <div className="org-overview"><span><Users size={15} />{plural(config.employees.length, "employee")}</span><button type="button" onClick={() => navigate("work")}><ListTodo size={15} />{plural(openCount, "open task")}<ChevronRight size={13} /></button><button type="button" onClick={() => openCollection("teams")}><GitBranch size={15} />{plural(config.teams.length, "team")}<ChevronRight size={13} /></button><button type="button" onClick={() => openCollection("projects")}><Folder size={15} />{plural(config.projects.length, "initiative")}<ChevronRight size={13} /></button></div>
          <div className="org-people"><section aria-label="Reporting tree" className="org-tree"><div className="org-section-heading"><h3>Reporting lines</h3><button className="org-button" type="button" onClick={() => edit("new-employee")}><Plus size={14} />Employee</button></div>
            <div className="org-owner"><Building2 size={19} /><span><strong>Organization owner</strong><small>You and Rem</small></span></div>
            <div className="org-roster">{employeeTree(config.employees).map(e => <button type="button" key={e.id} className="org-person" style={{ "--org-depth": Math.min(e.depth, 4) }} aria-pressed={e.id === employee?.id} onClick={() => setEmployeeId(e.id)}><span className="org-initial">{initials(e.name)}</span><span className="org-person-name"><strong>{e.name}</strong><small>{e.title || e.role || "Employee"}</small></span><Status state={employeeStatus(e, tasks)} /></button>)}</div>
            {!config.employees.length && <p className="org-note">Your employees and reporting lines will appear here.</p>}
          </section>
          <section className="org-inspector" aria-label="Employee details">{employee ? <>
            <div className="org-section-heading org-profile"><span className="org-initial org-avatar-large">{initials(employee.name)}</span><div><h3>{employee.name}</h3><p>{employee.title || employee.role || "Rem employee"}</p></div><button className="org-button" type="button" onClick={() => edit("employee")}>Edit employee</button></div>
            <dl className="org-facts"><div><dt>Reports to</dt><dd>{config.employees.find(e => e.id === employee.reportsTo)?.name || "Organization owner"}</dd></div><div><dt>Provider and model</dt><dd>{employee.provider} / {employee.model === "cli-default" ? "Provider default" : employee.model}</dd></div><div><dt>Working directory</dt><dd>{config.projectRoots[0] || "Assign a project in Projects"}</dd></div><div><dt>Heartbeat</dt><dd>{employee.heartbeatSeconds ? `Every ${employee.heartbeatSeconds.toLocaleString()} seconds while running` : "On assignment"}</dd></div></dl>
            <div className="org-capabilities"><h4>Capabilities</h4><div className="org-tags">{["Workflows", "Swarms", "Fleet", "Organization tasks", ...(employee.resources.shell ? ["Shell"] : []), ...(employee.resources.web ? ["Web"] : []), plural(employee.resources.skills.length + employee.skills.length, "skill"), plural(employee.resources.mcpServers.length, "MCP server")].map(label => <span key={label}>{label}</span>)}</div></div>
            <h4>Instructions</h4><p className="org-prose">{employee.instructions || "Add purpose-built instructions for this employee."}</p>
            <details className="org-memory"><summary>Working memory</summary><p className="org-prose">{organization.runtime.employeeMemory?.[employee.id] || employee.memory || "Task conversations retain findings between turns."}</p></details>
            <WakeForm key={employee.id} busy={busy} employee={employee} onWake={message => act("wake", { employeeId: employee.id, message })} />
            <p className="org-note">Configuration changes go through you or Rem. Assignments wait in the queue while the organization is paused.</p>
          </> : <Empty title="Build your team" action={<button className="org-button org-primary" type="button" onClick={() => edit("new-employee")}><Plus size={15} />Add your first employee</button>}>Give each Rem employee a role, a provider, and instructions for their work.</Empty>}</section>
          </div>
        </>)}
        {section === "chart" && <OrganizationChart config={config} onEmployee={id => { navigate("people"); setEmployeeId(id); }} onCollection={openCollection} />}
        {section === "owned-projects" && <OwnedProjects config={config} revision={revision} projectOptions={projectOptions} busy={busy} onSave={saveConfig} />}
        {["teams", "projects"].includes(section) && <CollectionView key={section} kind={section === "teams" ? "team" : "project"} config={config} tasks={tasks} onEmployee={id => { navigate("people"); setEmployeeId(id); setEditor("employee"); }} revision={revision} selectedId={collectionId} onSelect={setCollectionId} busy={busy} onSave={saveConfig} onChart={() => navigate("chart")} />}
        {section === "operations" && <OrganizationOperations organization={organization} busy={busy} onAction={act} onSave={saveConfig} />}
        {section === "work" && (editor === "task" ? <EditorFrame key={selectedTask?.id || "new"} label="Back to work queue" onBack={() => setEditor(null)}><TaskEditor task={selectedTask} config={config} busy={busy} onCancel={() => setEditor(null)} onSave={async params => { const result = await act(selectedTask ? "task_update" : "task_create", params); if (result) { setEditor(null); setTaskId(result.id || selectedTask?.id); } }} /></EditorFrame> : selectedTask ? <><button className="org-button org-back" type="button" onClick={() => setTaskId(null)}><ArrowLeft size={14} />Back to work queue</button><TaskConversation key={selectedTask.id} task={selectedTask} employees={config.employees} busy={busy} onEdit={() => edit("task")} onComment={body => act("comment", { taskId: selectedTask.id, body, idempotencyKey: crypto.randomUUID() })} /><TaskRunControls organization={organization} task={selectedTask} busy={busy} onAction={act} /></> : <>
          <div className="org-section-heading"><div><h3>Work queue</h3><p className="org-note">{plural(openCount, "open task")}{approvals ? `, ${approvals} awaiting approval` : ""}</p></div><button className="org-button org-primary" type="button" onClick={() => { setTaskId(null); edit("task"); }}><Plus size={14} />New task</button></div>
          <div className="org-work-filters"><TextField label="Search work" value={filters.query} onChange={query => filter({ query })} /><Field label="Filter assignee"><select value={filters.assignee} onChange={e => filter({ assignee: e.target.value })}><option value="">All employees</option>{config.employees.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></Field><Field label="Filter initiative"><select value={filters.project} onChange={e => filter({ project: e.target.value })}><option value="">All initiatives</option>{config.projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field><label className="org-check"><input type="checkbox" checked={filters.attention} onChange={e => filter({ attention: e.target.checked })} />Needs you</label></div>
          {tasks.length > 0 && !filteredWork.length && <p role="status">No tasks match these filters.</p>}
          {!tasks.length ? <Empty icon={ListTodo} title="Give your team a starting point">Create a task with an owner and a clear description of the expected result. Start work when the team is ready.</Empty> : <div className="org-work-groups">{taskGroups(visibleWork).map(group => <section className="org-work-group" key={group.id}><h4><Status state={group.id}>{group.label}</Status><span className="org-count">{group.tasks.length}</span></h4>{group.tasks.map(task => <article className="org-task" key={task.id}>
            <button className="org-task-open" type="button" onClick={() => setTaskId(task.id)}><strong>{task.title}</strong><small>{organization.diagnostics?.taskReasons?.[task.id]}</small><span className="org-task-meta"><span data-priority={task.priority}>{task.priority}</span>{task.project && <span>{config.projects.find(p => p.id === task.project)?.name || task.project}</span>}{task.recurring && <span><Clock size={12} />Recurring</span>}{task.claim && <span>Working</span>}</span></button>
            <span className="org-task-owner">{config.employees.find(e => e.id === task.assignee)?.name || "Unassigned"}</span><div className="org-row-actions">{needsApproval(task) && isOpenTask(task) && <button className="org-button" type="button" disabled={busy} onClick={() => act("approve", { taskId: task.id, expectedRevision: task.revision })}><ShieldCheck size={14} />Approve plan</button>}<button className="org-button" type="button" disabled={busy || !!task.claim} onClick={() => { setTaskId(task.id); edit("task"); }}>Edit</button></div>
          </article>)}</section>)}</div>}
          {pageCount > 1 && <div className="org-actions"><button className="org-button" disabled={currentPage === 0} onClick={() => setWorkPage(currentPage - 1)}>Previous tasks</button><span>Page {currentPage + 1} of {pageCount}</span><button className="org-button" disabled={currentPage + 1 >= pageCount} onClick={() => setWorkPage(currentPage + 1)}>Next tasks</button></div>}
          <p className="org-note">Starting work runs assigned tasks and enabled heartbeats. Employees working directly in the same project take turns.</p>
        </>)}
        {section === "history" && <>
          <div className="org-section-heading"><div><h3>Configuration history</h3><p className="org-note">Every change records who made it and why. Restore a healthy state without losing task history.</p></div><History size={24} className="org-muted" aria-hidden="true" /></div>
          {restore && <RestorePreview restore={restore} config={config} busy={busy} reason={restoreReason} onReason={setRestoreReason} onCancel={() => setRestore(null)} onRestore={async () => { if (await act("restore", { revision: restore.revision, expectedRevision: restore.expectedRevision, reason: restoreReason })) { setRestore(null); setRestoreReason(""); } }} />}
          <div className="org-history">{historyByDate(history).map(bucket => <section className="org-date" key={bucket.date}><h4>{relativeDay(bucket.revisions[0].at) && <strong>{relativeDay(bucket.revisions[0].at)}</strong>}<span>{bucket.date}</span><small>{plural(bucket.revisions.length, "revision")}</small></h4><div>{bucket.revisions.map(item => <article className="org-revision" key={item.revision} data-current={item.revision === revision}>
            <div className="org-section-heading"><strong>Revision {item.revision}{item.revision === revision && <span className="org-current">Current</span>}</strong><time dateTime={item.at}>{new Date(item.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time></div>
            <p>{item.reason}</p><small title={item.actor}>{actorLabel(item.actor, config.employees)}{item.restored_from ? ` · Restored revision ${item.restored_from}` : ""}</small>
            <details className="org-revision-changes"><summary>{plural(item.changes.length, "changed field")}</summary><Changes changes={item.changes} configs={[item.config, config]} /><small className="org-digest">Snapshot hash: {item.digest}</small></details>
            {item.revision !== revision && <button className="org-button" type="button" disabled={busy} onClick={() => { setRestore({ ...item, expectedRevision: revision, comparedConfig: structuredClone({ ...config, packageFiles: organization.packageFiles }) }); setRestoreReason(`Restore revision ${item.revision}: ${item.reason}`); }}>Preview restore<ChevronRight size={13} /></button>}
          </article>)}</div></section>)}</div>
          {history.length > 0 && history.length % 50 === 0 && <button className="org-button" disabled={busy} onClick={async () => { try { const older = await organizationRequest("", "history", organizationId, { offset: history.length }); setHistory(items => [...items, ...older]); } catch (failure) { setError(failure.message); } }}>Load older revisions</button>}
          <p className="org-note">Dates use your local timezone. Restoring creates a new revision and pauses employees. It does not undo changes to project files or completed work.</p>
        </>}
        {section === "activity" && <><UsageTable usage={organization.runtime.usage || {}} employees={config.employees} /><div className="org-section-heading"><h3>Activity</h3><span className="org-muted">Newest first</span></div>{!events.length ? <Empty icon={Activity} title="No activity yet">Assignments, employee updates, and configuration changes will appear here.</Empty> : <div className="org-activity">{activityEntries(events, tasks).map(entry => <details className="org-event" key={entry.key} data-tone={entry.tone}><summary><span className="org-initial">{initials(actorLabel(entry.actor, config.employees))}</span><span className="org-event-copy"><strong title={entry.actor}>{actorLabel(entry.actor, config.employees)}</strong><span>{entry.text}</span>{entry.detail && <small>{entry.detail}</small>}</span><time dateTime={entry.at}>{new Date(entry.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</time><ChevronRight size={14} className="org-disclosure" /></summary><div className="org-event-data"><h4>Event details</h4><pre>{JSON.stringify(entry.events.map(e => e.payload), null, 2)}</pre></div></details>)}</div>}</>}
      </>}
    </div></div>
  </section>;
}

function Changes({ changes, configs = [] }) {
  return <div className="org-changes">{changes.length ? changes.map(change => <details key={change.path}><summary title={change.path}>{changeLabel(change.path, ...configs)}</summary><div className="org-change-values"><div><span>Previous value</span><pre aria-label="Previous value">{JSON.stringify(change.before, null, 2) ?? "Not set"}</pre></div><div><span>New value</span><pre aria-label="New value">{JSON.stringify(change.after, null, 2) ?? "Not set"}</pre></div></div></details>) : <p>No configuration differences.</p>}</div>;
}
function RestorePreview({ restore, config, busy, reason, onReason, onCancel, onRestore }) {
  const ref = useRef(null);
  useEffect(() => { ref.current?.scrollIntoView({ block: "start" }); ref.current?.focus(); }, [restore.revision]);
  return <section ref={ref} tabIndex={-1} className="org-restore" aria-label="Restore preview"><div className="org-section-heading"><div><h3>Restore revision {restore.revision}</h3><p className="org-note">{new Date(restore.at).toLocaleString()} · {actorLabel(restore.actor, config.employees)}</p></div><History size={22} /></div><p>This will replace the current settings and pause employees. Review the changes below before restoring.</p><Changes configs={[restore.config, config]} changes={configurationChanges(restore.comparedConfig, { ...restore.config, packageFiles: restore.packageFiles })} /><TextField label="Restore reason" value={reason} onChange={onReason} /><div className="org-actions"><button className="org-button org-primary" type="button" disabled={busy || !reason.trim()} onClick={onRestore}>Restore and pause employees</button><button className="org-button" type="button" onClick={onCancel}>Cancel</button></div></section>;
}

function ConfigurationImpact({ before, after, tasks = [] }) {
  if (!before) return null;
  const changes = configurationChanges(before, after);
  if (!changes.length) return null;
  const global = changes.some(c => /^\/(projectRoots|remSecondBrain)(\/|$)/.test(c.path) || /^\/projects\/[^/]+(\/workspacePath)?$/.test(c.path));
  const affected = new Set(changes.map(c => /^\/employees\/([^/]+)(?:\/(resources|permissionMode|workspacePaths|paused|secretRefs)(?:\/|$)|$)/.exec(c.path)).filter(Boolean).map(match => match[1]));
  const stopped = tasks.filter(t => t.claim && (global || affected.has(t.assignee)));
  return <p className="org-banner org-pause">{global ? "This change pauses the organization. " : "The organization keeps its current operating state. "}{stopped.length ? `Stops ${stopped.length} active task${stopped.length === 1 ? "" : "s"}: ${stopped.map(t => t.title).join(", ")}. ` : "No active tasks need to stop. "}Provider and instruction changes apply on the next turn.</p>;
}

function OrganizationEditor({ rootPath, config, tasks, memorySettings, revision, busy, onSave, onCancel }) {
  const [draft, setDraft] = useState(() => config ? structuredClone(config) : { name: "", slug: "", description: "", instructions: "", goals: [], employees: [], projects: [], projectRoots: rootPath ? [rootPath] : [], teams: [], monthlyBudgetUsd: 0, monthlyTurnLimit: 0, maxConcurrency: 1, maxTaskTurns: 20, turnTimeoutSeconds: 1800, remSecondBrain: { enabled: memorySettings.secondBrainEnabled || false, root: memorySettings.secondBrainRoot || "", format: memorySettings.secondBrainFormat || "md" } });
  const [baseRevision] = useState(revision);
  const [goals, setGoals] = useState((config?.goals || []).join("\n"));

  const [reason, setReason] = useState(config ? "" : "Created organization");
  const [error, setError] = useState("");
  const patch = (key, value) => setDraft(d => ({ ...d, [key]: value }));
  return <form className="org-form" onSubmit={event => { event.preventDefault(); try { onSave({ ...draft, slug: draft.slug || draft.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""), goals: goals.split("\n").filter(Boolean), monthlyBudgetUsd: Number(draft.monthlyBudgetUsd || 0), monthlyTurnLimit: Number(draft.monthlyTurnLimit || 0), maxConcurrency: Number(draft.maxConcurrency), maxTaskTurns: Number(draft.maxTaskTurns), turnTimeoutSeconds: Number(draft.turnTimeoutSeconds) }, reason, baseRevision); } catch (failure) { setError(failure.message); } }}>
    <h3>{config ? "Organization settings" : "Build your organization"}</h3><p className="org-note">{config ? "Changes are recorded in configuration history. Display changes apply immediately. Permission changes stop affected runs; workspace changes pause the company." : "Define the purpose, then add your Rem employees. Your organization starts paused."}</p>
    <div className="org-form-grid"><TextField label="Organization name" value={draft.name} onChange={v => patch("name", v)} required /><TextField label="Package slug" value={draft.slug} onChange={v => patch("slug", v)} placeholder="e.g. product-studio" /></div>
    <TextField label="Purpose" value={draft.description} onChange={v => patch("description", v)} multiline placeholder="What should this organization accomplish?" />
    <div className="org-form-grid"><TextField label="Company instructions" value={draft.instructions} onChange={v => patch("instructions", v)} multiline /><TextField label="Goals, one per line" value={goals} onChange={setGoals} multiline /></div>
    {!config && rootPath && <label className="org-check"><input type="checkbox" checked={draft.projectRoots.includes(rootPath)} onChange={event => patch("projectRoots", event.target.checked ? [rootPath] : [])} />Assign the current project to this organization</label>}
    {!config && <p className="org-note">{draft.projectRoots.length ? `Initial project: ${rootPath}. Add more projects after creating the organization.` : "Assign Raticode projects after creating the organization."}</p>}
    <h4 className="org-form-divider">Operating limits</h4>
    <div className="org-form-grid org-form-thirds"><NumberField label="Maximum concurrent employees" value={draft.maxConcurrency} onChange={v => patch("maxConcurrency", v)} required /><NumberField label="Maximum turns per task" value={draft.maxTaskTurns} onChange={v => patch("maxTaskTurns", v)} required /><NumberField label="Turn timeout in seconds" value={draft.turnTimeoutSeconds} onChange={v => patch("turnTimeoutSeconds", v)} required /></div>
    <div className="org-form-grid"><NumberField label="Monthly turn limit, 0 is unlimited" value={draft.monthlyTurnLimit ?? "0"} onChange={v => patch("monthlyTurnLimit", v)} /><NumberField label="Monthly dollar budget, 0 disables" value={draft.monthlyBudgetUsd ?? "0"} onChange={v => patch("monthlyBudgetUsd", v)} /></div><p className="org-note">Budgets use reported spending. A budget pauses dispatch when cost is unavailable. An in-flight turn can exceed the remaining budget.</p>
    <p className="org-note">Manage teams and initiatives from Org chart. Assign Raticode projects in Projects.</p>
    <details className="org-form-disclosure"><summary>Second Brain</summary><label className="org-check"><input type="checkbox" checked={!!draft.remSecondBrain?.enabled} onChange={e => patch("remSecondBrain", { ...draft.remSecondBrain, enabled: e.target.checked })} />Use Second Brain for employee research and reports</label><TextField label="Knowledge folder" value={draft.remSecondBrain?.root || ""} onChange={root => patch("remSecondBrain", { ...draft.remSecondBrain, root })} /></details>
    <ConfigurationImpact before={config} after={draft} tasks={tasks} /><TextField label="Change reason" value={reason} onChange={setReason} required />{error && <p role="alert">{error}</p>}<div className="org-actions"><button className="org-button org-primary" type="submit" disabled={busy}>{config ? "Save settings" : "Create organization"}</button><button className="org-button" type="button" onClick={onCancel}>Cancel</button></div>
  </form>;
}

function EmployeeEditor({ employee, config, tasks, revision, defaults, busy, onSave, onCancel }) {
  const [draft, setDraft] = useState(() => employee ? structuredClone(employee) : newEmployee(defaults));
  const [base] = useState(() => ({ config: structuredClone(config), revision }));
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const { capabilities, loading, refresh } = useProviderCapabilities();
  const patch = values => setDraft(d => ({ ...d, ...values }));
  const save = event => {
    event.preventDefault(); const issue = remResourceError(draft.resources); if (issue) { setError(issue); return; }
    const record = { ...draft, heartbeatSeconds: Number(draft.heartbeatSeconds), monthlyTurnLimit: Number(draft.monthlyTurnLimit || 0), monthlyBudgetUsd: Number(draft.monthlyBudgetUsd || 0), model: draft.model || "cli-default" };
    record.secretRefs = (draft.secretRefsDraft ?? (draft.secretRefs || []).join(",")).split(",").map(v => v.trim()).filter(Boolean); delete record.secretRefsDraft;
    const employees = employee ? base.config.employees.map(e => e.id === draft.id ? record : e) : [...base.config.employees, record];
    onSave({ ...base.config, employees }, reason || `${employee ? "Updated" : "Added"} employee ${draft.name}`, base.revision);
  };
  return <form className="org-form" onSubmit={save}><h3>{employee ? `Edit ${employee.name}` : "New Rem employee"}</h3><p className="org-note">Give this employee a purpose and the tools to carry it out. Instructions apply on the next run. Permission changes stop affected work.</p><div className="org-form-grid"><TextField label="Employee name" value={draft.name} onChange={name => patch({ name })} required /><TextField label="Title" value={draft.title} onChange={title => patch({ title })} /><TextField label="Role and responsibilities" value={draft.role} onChange={role => patch({ role })} multiline /><Field label="Reports to"><select value={draft.reportsTo || ""} onChange={e => patch({ reportsTo: e.target.value || null })}><option value="">Organization owner</option>{eligibleManagers(config.employees, draft.id).map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></Field></div>
    <TextField label="Employee instructions" value={draft.instructions} onChange={instructions => patch({ instructions })} multiline rows={7} placeholder="Describe their expertise, working style, responsibilities and what a finished assignment looks like." /><details className="org-form-disclosure"><summary>Persistent memory</summary><TextField label="Persistent memory" value={draft.memory} onChange={memory => patch({ memory })} multiline /></details>
    <h4 className="org-form-divider">Provider and permissions</h4><ProviderModelEffortFields className="org-provider-fields" capabilities={capabilities} loading={loading} provider={draft.provider} model={draft.model} effort={draft.effort} onRefresh={refresh} onChange={values => patch({ ...values, ...(values.provider && values.provider !== draft.provider ? { permissionMode: providerPermissionDefault(values.provider) } : {}) })} />
    <Field label="Permission mode"><select value={draft.permissionMode} onChange={e => patch({ permissionMode: e.target.value })}>{providerPermissionOptions(draft.provider).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></Field>
    <h4 className="org-form-divider">Workspace grants</h4><label className="org-check"><input type="checkbox" checked={draft.workspacePaths == null} onChange={e => patch({ workspacePaths: e.target.checked ? null : [] })} />All organization projects</label>{draft.workspacePaths != null && config.projectRoots.map(root => <label key={root} className="org-check"><input type="checkbox" checked={draft.workspacePaths.includes(root)} onChange={e => patch({ workspacePaths: e.target.checked ? [...draft.workspacePaths, root] : draft.workspacePaths.filter(r => r !== root) })} />{root}</label>)}
    <TextField label="Secret reference IDs, comma separated" value={draft.secretRefsDraft ?? (draft.secretRefs || []).join(", ")} onChange={value => patch({ secretRefsDraft: value })} />
    <h4 className="org-form-divider">Schedule and limits</h4><NumberField label="Heartbeat interval in seconds, 0 disables" value={draft.heartbeatSeconds} onChange={heartbeatSeconds => patch({ heartbeatSeconds })} required /><label className="org-check"><input type="checkbox" checked={draft.paused} onChange={e => patch({ paused: e.target.checked })} />Pause this employee</label>
    <div className="org-form-grid"><NumberField label="Employee monthly turn limit, 0 is unlimited" value={draft.monthlyTurnLimit ?? "0"} onChange={monthlyTurnLimit => patch({ monthlyTurnLimit })} /><NumberField label="Employee monthly dollar budget, 0 disables" value={draft.monthlyBudgetUsd ?? "0"} onChange={monthlyBudgetUsd => patch({ monthlyBudgetUsd })} /></div>
    <details className="org-form-disclosure"><summary>Tools, skills and MCP servers</summary><RemResources value={draft.resources} onChange={resources => patch({ resources })} /></details>
    <ConfigurationImpact before={config} after={{ ...config, employees: employee ? config.employees.map(e => e.id === draft.id ? draft : e) : [...config.employees, draft] }} tasks={tasks} /><TextField label="Change reason" value={reason} onChange={setReason} placeholder={`${employee ? "Update" : "Add"} ${draft.name || "employee"}`} />{error && <p role="alert">{error}</p>}<div className="org-actions"><button className="org-button org-primary" type="submit" disabled={busy}>Save employee</button><button className="org-button" type="button" onClick={onCancel}>Cancel</button>{employee && <button className="org-button org-danger" type="button" disabled={busy} onClick={() => { if (window.confirm(`Remove ${employee.name}? Tasks and configuration history will remain.`)) onSave({ ...base.config, employees: base.config.employees.filter(e => e.id !== employee.id).map(e => e.reportsTo === employee.id ? { ...e, reportsTo: employee.reportsTo } : e) }, reason || `Removed employee ${employee.name}`, base.revision); }}>Remove employee</button>}</div>
  </form>;
}

function WakeForm({ employee, busy, onWake }) {
  const [message, setMessage] = useState("");
  return <form className="org-assignment" onSubmit={async e => { e.preventDefault(); if (await onWake(message)) setMessage(""); }}><TextField label={`Assignment for ${employee.name}`} value={message} onChange={setMessage} placeholder="What should they work on next?" required multiline /><button className="org-button org-primary" type="submit" disabled={busy || !message.trim()}><Plus size={14} />Assign work</button></form>;
}
function TaskConversation({ task, employees, busy, onComment, onEdit }) {
  const [body, setBody] = useState("");
  return <section className="org-conversation"><div className="org-section-heading"><h3>{task.title}</h3><button className="org-button" type="button" disabled={busy || !!task.claim} onClick={onEdit}>Edit task</button></div><p className="org-note">Task ID: {task.id}</p><div className="org-conversation-meta"><Status state={task.status} /><span>{employees.find(e => e.id === task.assignee)?.name || "Unassigned"}</span><span className="org-priority" data-priority={task.priority}>{task.priority} priority</span></div><p className="org-prose">{task.description || "No description provided."}</p>{task.evidence && <div className="org-evidence"><h4><Check size={15} />Evidence</h4><p className="org-prose">{task.evidence}</p></div>}<h4>Conversation</h4>{!task.comments.length && <p className="org-note">Add context or leave instructions for the next turn.</p>}{task.comments.map((comment, i) => <article key={i}><small><strong title={comment.actor}>{actorLabel(comment.actor, employees)}</strong><time dateTime={comment.at}>{new Date(comment.at).toLocaleString()}</time></small><p className="org-prose">{comment.body}</p></article>)}<form onSubmit={async e => { e.preventDefault(); if (await onComment(body)) setBody(""); }}><TextField label="Comment" value={body} onChange={setBody} multiline required placeholder="Add context, feedback, or evidence..." /><button className="org-button org-primary" type="submit" disabled={busy || !body.trim()}>Save comment</button></form></section>;
}
function TaskEditor({ task, config, busy, onSave, onCancel }) {
  const [dependencies, setDependencies] = useState((task?.dependsOn || []).join(", "));
  const [workflowPath, setWorkflowPath] = useState(task?.workflowContract?.path || "");
  const [executionTarget, setExecutionTarget] = useState(task?.execution?.targetId || "");
  const [workflowInputs, setWorkflowInputs] = useState(JSON.stringify(task?.workflowContract?.inputs || {}, null, 2));
  const [workflowGrants, setWorkflowGrants] = useState((task?.workflowContract?.allowedResources || []).join(", "));
  const [workflowChecks, setWorkflowChecks] = useState(JSON.stringify(task?.workflowContract?.completionChecks || [], null, 2));
  const [workflowTurns, setWorkflowTurns] = useState(String(task?.workflowContract?.turnLimit || 1));
  const [contractError, setContractError] = useState("");
  const [draft, setDraft] = useState(() => ({ title: task?.title || "", description: task?.description || "", assignee: task?.assignee || "", project: task?.project || "", parentId: task?.parentId || null, goal: task?.goal || "", priority: task?.priority || "medium", status: task?.status || "todo", evidence: task?.evidence || "", recurring: task?.recurring || false, intervalSeconds: task?.intervalSeconds || 0, approvalRequired: task?.approvalRequired || false, reviewRequired: task?.reviewRequired || false, reviewer: task?.reviewer || "", workspacePath: task?.workspacePath || "", goalId: task?.goalId || "", dueAt: task?.dueAt || "" }));
  const [baseRevision] = useState(task?.revision);
  const patch = values => setDraft(d => ({ ...d, ...values }));
  return <form className="org-form" onSubmit={e => { e.preventDefault(); let contract = null; try { contract = workflowPath ? { path: workflowPath, inputs: JSON.parse(workflowInputs), allowedResources: workflowGrants.split(",").map(s => s.trim()).filter(Boolean), completionChecks: JSON.parse(workflowChecks), turnLimit: Number(workflowTurns) } : null; setContractError(""); } catch { setContractError("Workflow inputs and completion checks must be valid JSON."); return; } const values = { ...draft, execution: executionTarget ? { targetId: executionTarget } : task?.execution?.kind === "workflow" && contract ? { kind: "workflow" } : null, assignee: draft.assignee || null, project: draft.project || null, intervalSeconds: Number(draft.intervalSeconds), dueAt: draft.dueAt || null, reviewer: draft.reviewer || null, workspacePath: draft.workspacePath || null, goalId: draft.goalId || null, dependsOn: dependencies.split(",").map(v => v.trim()).filter(Boolean), workflowContract: contract }; onSave(task ? { taskId: task.id, expectedRevision: baseRevision, changes: values } : values); }}><h3>{task ? "Edit task" : "New task"}</h3><TextField label="Task title" value={draft.title} onChange={title => patch({ title })} required /><TextField label="Description and acceptance criteria" value={draft.description} onChange={description => patch({ description })} multiline /><div className="org-form-grid"><Field label="Assignee"><select value={draft.assignee} onChange={e => patch({ assignee: e.target.value })}><option value="">Unassigned</option>{config.employees.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></Field><Field label="Initiative"><select value={draft.project} onChange={e => patch({ project: e.target.value })}><option value="">Company-wide</option>{config.projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field><Field label="Status"><select value={draft.status} onChange={e => patch({ status: e.target.value })}>{STATES.map(s => <option key={s} value={s}>{STATUS_LABELS[s]}</option>)}</select></Field><Field label="Priority"><select value={draft.priority} onChange={e => patch({ priority: e.target.value })}>{["critical", "high", "medium", "low"].map(s => <option key={s}>{s}</option>)}</select></Field></div><Field label="Execution workspace"><select value={draft.workspacePath} onChange={e => patch({ workspacePath: e.target.value })}><option value="">Initiative or company default</option>{config.projectRoots.map(root => <option key={root}>{root}</option>)}</select></Field><Field label="Linked goal"><select value={draft.goalId} onChange={e => patch({ goalId: e.target.value })}><option value="">No structured goal</option>{(config.goalRecords || []).map(g => <option key={g.id} value={g.id}>{g.title}</option>)}</select></Field><TextField label="Due at, ISO timestamp with timezone, optional" value={draft.dueAt} onChange={dueAt => patch({ dueAt })} /><TextField label="Dependency task IDs, comma separated" value={dependencies} onChange={setDependencies} /><Field label="Execution destination"><select value={executionTarget} onChange={e => setExecutionTarget(e.target.value)}><option value="">Employee turn or authorized workflow</option>{(config.executionTargets || []).filter(t => t.employees.includes(draft.assignee)).map(t => <option key={t.id} value={t.id}>{t.id} · {t.kind}</option>)}</select></Field><TextField label="Workflow contract path, optional workflow.rattish" value={workflowPath} onChange={setWorkflowPath} />{workflowPath && <><TextField label="Workflow inputs, JSON object" multiline value={workflowInputs} onChange={setWorkflowInputs} /><TextField label="Allowed workflow resources, comma separated" value={workflowGrants} onChange={setWorkflowGrants} /><p className="org-note">Save and validate to see the required node and workspace grants. Enter the grants you authorize, then save before launching.</p><TextField label="Completion checks, JSON array" multiline value={workflowChecks} onChange={setWorkflowChecks} /><TextField label="Workflow provider turn reservation" value={workflowTurns} onChange={setWorkflowTurns} />{contractError && <p role="alert">{contractError}</p>}</>}<label className="org-check"><input type="checkbox" checked={draft.reviewRequired} onChange={e => patch({ reviewRequired: e.target.checked })} />Require independent completion review</label><Field label="Independent reviewer"><select value={draft.reviewer} onChange={e => patch({ reviewer: e.target.value })}><option value="">User or Rem only</option>{config.employees.filter(e => e.id !== draft.assignee).map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></Field><TextField label="Goal" value={draft.goal} onChange={goal => patch({ goal })} /><TextField label="Evidence" value={draft.evidence} onChange={evidence => patch({ evidence })} multiline /><label className="org-check"><input type="checkbox" checked={draft.approvalRequired} onChange={e => patch({ approvalRequired: e.target.checked })} />Require approval before work starts</label><label className="org-check"><input type="checkbox" checked={draft.recurring} onChange={e => patch({ recurring: e.target.checked })} />Recurring task</label>{draft.recurring && <NumberField label="Repeat interval in seconds, 0 is manual" value={draft.intervalSeconds} onChange={intervalSeconds => patch({ intervalSeconds })} required />}<div className="org-actions"><button className="org-button org-primary" type="submit" disabled={busy}>Save task</button><button className="org-button" type="button" onClick={onCancel}>Cancel</button></div></form>;
}
function GithubPackageImport({ busy, onAction }) {
  const [repository, setRepository] = useState("");
  const [commit, setCommit] = useState("");
  const [directory, setDirectory] = useState("");
  const [preview, setPreview] = useState(null);
  const edit = setter => value => { setter(value); setPreview(null); };
  return <details className="org-form-disclosure"><summary>Import from a pinned GitHub revision</summary><form className="org-form" onSubmit={async e => { e.preventDefault(); setPreview(await onAction("import_github_preview", { repository, commit, directory })); }}><TextField label="GitHub repository, owner/name" value={repository} onChange={edit(setRepository)} required /><TextField label="Immutable commit, 40 hexadecimal characters" value={commit} onChange={edit(setCommit)} required pattern="[0-9a-fA-F]{40}" /><TextField label="Package folder inside repository, optional" value={directory} onChange={edit(setDirectory)} /><button className="org-button" disabled={busy}>Preview GitHub package</button></form>{preview && <div className="org-import-preview"><h4>{preview.config.name}</h4><p>{preview.config.employees.length} employees, {preview.tasks.length} starter tasks</p>{preview.warnings.map(w => <p key={w}>{w}</p>)}<button className="org-button org-primary" disabled={busy} onClick={() => onAction("import_github", { repository, commit, directory })}>Import pinned template</button></div>}<p className="org-note">Public archives only, at most 16 MiB and 1,000 files. Review imported tools and providers before starting.</p></details>;
}
function PackageImport({ busy, onAction }) {
  const [zip, setZip] = useState("");
  const [filename, setFilename] = useState("");
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  useEffect(() => () => { generation.current += 1; }, []);
  async function choose(event) {
    const request = ++generation.current;
    setPreview(null); setZip(""); setError(""); setFilename("");
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 16 * 1024 * 1024) { setError("Choose a ZIP smaller than 16 MiB."); return; }
    setLoading(true);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      if (request !== generation.current) return;
      let binary = ""; for (const byte of bytes) binary += String.fromCharCode(byte);
      const value = btoa(binary);
      const result = await onAction("import_preview", { zip: value });
      if (request === generation.current) { setZip(value); setPreview(result); setFilename(file.name); }
    } catch (failure) { if (request === generation.current) setError(failure.message); }
    finally { if (request === generation.current) setLoading(false); }
  }
  return <section className="org-import"><GithubPackageImport busy={busy} onAction={onAction} /><div className="org-section-heading"><Upload size={22} /><div><h3>Bring an existing company</h3><p className="org-note">Import an Agent Companies ZIP with COMPANY.md, TEAM.md or AGENTS.md at its root, up to 16 MiB.</p></div></div><input type="file" accept=".zip" aria-label="Organization package ZIP" disabled={busy || loading} onChange={choose} />{loading && <p className="org-note" role="status">Reading package...</p>}{error && <p className="org-error" role="alert">{error}</p>}{preview && <div className="org-import-preview"><small>{filename}</small><h4>{preview.config.name}</h4><p>{preview.config.description}</p><p className="org-note">{plural(preview.config.employees.length, "employee")}, {plural(preview.config.projects.length, "initiative")}, {plural(preview.tasks.length, "starter task")}</p>{preview.warnings.length > 0 && <ul className="org-import-warnings">{preview.warnings.map(w => <li key={w}>{w}</li>)}</ul>}<button className="org-button org-primary" type="button" disabled={busy} onClick={() => onAction("import", { zip })}>Import paused organization</button><p className="org-note">Employees and recurring tasks remain paused for review.</p></div>}</section>;
}

function CollectionView({ kind, config, tasks, onEmployee, revision, selectedId, onSelect, busy, onSave, onChart }) {
  const [query, setQuery] = useState("");
  const collection = `${kind}s`;
  const title = kind === "project" ? "Initiatives" : "Teams";
  const noun = kind === "project" ? "initiative" : "team";
  const Icon = kind === "project" ? Folder : Users;
  const entries = config[collection];
  const entry = entries.find(item => item.id === selectedId);
  if (entry || selectedId === NEW_COLLECTION) return <EditorFrame key={String(selectedId)} label={`Back to ${title.toLowerCase()}`} onBack={() => onSelect(null)}>
    <CollectionDetail kind={kind} entry={entry} tasks={tasks} onEmployee={onEmployee} config={config} revision={revision} busy={busy} onCancel={() => onSelect(null)} onSave={async (...args) => { const result = await onSave(...args); if (result) onSelect(null); return result; }} />
  </EditorFrame>;
  const matches = entries.filter(item => (item.name || item.id).toLowerCase().includes(query.trim().toLowerCase()));
  return <section aria-label={title}>
    <button className="org-button org-back" type="button" onClick={onChart}><ArrowLeft size={14} />Back to org chart</button>
    <div className="org-section-heading"><div><h3>{title}</h3><p className="org-note">Select a {noun} to view and edit its properties.</p></div><button className="org-button org-primary" type="button" onClick={() => onSelect(NEW_COLLECTION)}><Plus size={14} />Add {noun}</button></div>
    {entries.length > 0 && <TextField label={`Find ${title.toLowerCase()}`} value={query} onChange={setQuery} type="search" />}
    <ul className="org-directory">{matches.map(item => <li key={item.id}><button type="button" onClick={() => onSelect(item.id)}><Icon size={17} aria-hidden="true" /><span>{item.name || item.id}</span><ChevronRight size={16} aria-hidden="true" /></button></li>)}</ul>
    {!entries.length && <Empty icon={Icon} title={`No ${title.toLowerCase()} yet`}>Use Add {noun} to create one and choose who leads it.</Empty>}
    {entries.length > 0 && !matches.length && <p className="org-note">No {title.toLowerCase()} match your search.</p>}
  </section>;
}

function CollectionDetail({ kind, entry, config, tasks, onEmployee, revision, busy, onSave, onCancel }) {
  const reference = kind === "project" ? "owner" : "manager";
  const label = kind === "project" ? "Initiative" : "Team";
  const [base] = useState(() => ({ config: structuredClone(config), revision }));
  const [draft, setDraft] = useState(() => entry ? structuredClone(entry) : { id: crypto.randomUUID(), name: "", [reference]: null });
  const [reason, setReason] = useState("");
  const patch = values => setDraft(value => ({ ...value, ...values }));
  const save = (remove = false) => {
    const collection = `${kind}s`;
    const entries = base.config[collection];
    const record = kind === "project" ? { ...draft, monthlyBudgetUsd: Number(draft.monthlyBudgetUsd || 0), monthlyTurnLimit: Number(draft.monthlyTurnLimit || 0) } : draft;
    const next = remove ? entries.filter(item => item.id !== entry.id) : entry ? entries.map(item => item.id === entry.id ? record : item) : [...entries, record];
    return onSave({ ...base.config, [collection]: next }, reason || `${remove ? "Removed" : entry ? "Updated" : "Added"} ${label.toLowerCase()} ${draft.name}`, base.revision);
  };
  const people = kind === "team"
    ? teamMembers(config.employees, draft).map(employee => ({ ...employee, assignment: employee.id === draft.manager ? "Team manager" : "Team member" }))
    : projectPeople(config.employees, draft, tasks);
  function openEmployee(id) {
    if (JSON.stringify(draft) !== JSON.stringify(entry) && !window.confirm("Discard unsaved changes and open employee settings?")) return;
    onEmployee(id);
  }
  return <form className="org-form" onSubmit={event => { event.preventDefault(); save(); }}>
    <h3>{entry ? entry.name || entry.id : `New ${label.toLowerCase()}`}</h3><p className="org-note">Saving records a configuration revision. Workspace changes pause the organization.</p>
    <TextField label={`${label} name`} value={draft.name} onChange={name => patch({ name })} required />
    <TextField label={`${label} ID`} value={draft.id} onChange={id => patch({ id })} required readOnly={!!entry} pattern={"[a-zA-Z0-9][a-zA-Z0-9_\\-]{0,99}"} />
    <Field label={kind === "project" ? "Initiative owner" : "Team manager"}><select value={draft[reference] || ""} onChange={event => patch({ [reference]: event.target.value || null })}><option value="">Organization owner</option>{config.employees.map(employee => <option key={employee.id} value={employee.id}>{employee.name}</option>)}</select></Field>
    <section className="org-collection-people" aria-label={kind === "team" ? "Team members" : "Initiative assignments"}>
      <h4>{kind === "team" ? "Team members" : "Assigned employees"}<span className="org-count">{people.length}</span></h4>
      <p className="org-note">{kind === "team" ? "Includes the team manager and everyone in their reporting chain." : "Includes the initiative owner and employees assigned to this initiative's tasks."}</p>
      <ul className="org-directory">{people.map(person => <li key={person.id}><button type="button" onClick={() => openEmployee(person.id)} aria-label={`Edit employee: ${person.name}`}><span className="org-initial">{initials(person.name)}</span><span>{person.name}<small>{person.assignment}</small></span><ChevronRight size={16} aria-hidden="true" /></button></li>)}</ul>
      {!people.length && <p className="org-note">{kind === "team" ? "Choose a team manager to show members." : "No employees assigned. Choose an initiative owner or assign employees to initiative tasks in Work."}</p>}
    </section>
    <TextField label={`${label} description`} value={draft.description || ""} onChange={description => patch({ description })} multiline />
    {kind === "project" && <><div className="org-form-grid"><NumberField label="Initiative monthly dollar budget, 0 disables" value={draft.monthlyBudgetUsd ?? "0"} onChange={monthlyBudgetUsd => patch({ monthlyBudgetUsd })} /><NumberField label="Initiative monthly turn limit, 0 is unlimited" value={draft.monthlyTurnLimit ?? "0"} onChange={monthlyTurnLimit => patch({ monthlyTurnLimit })} /></div><TextField label="Project or worktree folder" value={draft.workspacePath || ""} onChange={workspacePath => patch({ workspacePath })} list="initiative-projects" placeholder="Default project" /><datalist id="initiative-projects">{config.projectRoots.map(path => <option key={path} value={path} />)}</datalist><p className="org-note">Choose an owned project or one of its worktrees. Blank uses the organization&apos;s default project.</p></>}
    <TextField label="Change reason" value={reason} onChange={setReason} />
    <div className="org-actions"><button className="org-button org-primary" type="submit" disabled={busy}>Save {label.toLowerCase()}</button><button className="org-button" type="button" onClick={onCancel}>Cancel</button>{entry && <button className="org-button org-danger" type="button" disabled={busy} onClick={() => { if (window.confirm(`Remove ${entry.name || entry.id}? Configuration history will remain.`)) save(true); }}>Remove {label.toLowerCase()}</button>}</div>
  </form>;
}
function UsageTable({ usage, employees }) {
  const month = new Date().toISOString().slice(0, 7);
  const accounts = usage[month] || {};
  const total = accounts.company || {};
  const label = new Date(`${month}-01T00:00:00Z`).toLocaleDateString([], { month: "long", year: "numeric", timeZone: "UTC" });
  return <section className="org-usage"><div className="org-section-heading"><h3>Usage</h3><span className="org-muted">{label} · UTC</span></div><dl className="org-usage-totals"><div><dt>Turns</dt><dd>{(total.turns || 0).toLocaleString()}</dd></div><div><dt>Reported tokens</dt><dd>{total.tokens?.toLocaleString() ?? "Unknown"}</dd></div><div><dt>Reported cost</dt><dd>{usageCost(total)}</dd></div><div><dt>Turns without cost</dt><dd>{(total.unknownCostTurns || 0).toLocaleString()}</dd></div></dl>{employees.length > 0 && <div className="org-table-wrap" tabIndex={0} role="region" aria-label="Employee usage"><table><thead><tr><th scope="col">Employee</th><th scope="col">Turns</th><th scope="col">Tokens</th><th scope="col">Reported cost</th><th scope="col">Turns without cost</th></tr></thead><tbody>{employees.map(e => { const a = accounts[`employee:${e.id}`] || {}; return <tr key={e.id}><th scope="row">{e.name}</th><td>{(a.turns || 0).toLocaleString()}</td><td>{a.tokens?.toLocaleString() ?? "Unknown"}</td><td>{usageCost(a)}</td><td>{a.unknownCostTurns || 0}</td></tr>; })}</tbody></table></div>}<p className="org-note">Missing provider costs and token counts remain unknown. Configuration restores preserve usage.</p></section>;
}


function OwnedProjects({ config, revision, projectOptions, busy, onSave }) {
  const [selected, setSelected] = useState(null);
  const [base, setBase] = useState({ config, revision });
  const choose = value => { setBase({ config, revision }); setSelected(value); };
  const [path, setPath] = useState("");
  const [query, setQuery] = useState("");
  const roots = selected === null ? config.projectRoots : base.config.projectRoots;
  const name = value => value.split(/[\\/]/).filter(Boolean).at(-1) || value;
  const save = async (next, reason) => {
    const result = await onSave({ ...base.config, projectRoots: next }, reason, base.revision);
    if (result) { setSelected(null); setPath(""); }
  };
  if (selected !== null) return <EditorFrame label="Back to projects" onBack={() => setSelected(null)}>
    <form className="org-form" onSubmit={event => { event.preventDefault(); void save([...roots, path.trim()], `Assigned project ${path.trim()}`); }}>
      <h3>{selected || "Assign a Raticode project"}</h3>
      <p className="org-note">The organization owns this project and all of its Git worktrees. Each project can belong to one organization.</p>
      <TextField label="Project folder" value={selected || path} readOnly={!!selected} onChange={setPath} list="organization-project-options" required />
      <datalist id="organization-project-options">{projectOptions.map(option => { const value = typeof option === "string" ? option : option.root || option.path; return value ? <option key={value} value={value} /> : null; })}</datalist>
      {selected ? <><p className="org-note">{roots[0] === selected ? "Default project for tasks without an initiative workspace." : "Initiatives can use this project or any of its worktrees."}</p><div className="org-actions">
        {roots[0] !== selected && <button type="button" className="org-button" disabled={busy} onClick={() => save([selected, ...roots.filter(root => root !== selected)], `Made ${selected} the default project`)}>Use as default</button>}
        <button type="button" className="org-button org-danger" disabled={busy} onClick={() => save(roots.filter(root => root !== selected), `Removed project ownership for ${selected}`)}>Remove from organization</button>
      </div><p className="org-note">Removing ownership keeps project files. Reassign initiative workspaces that use this project before removing it.</p></> : <button type="submit" className="org-button org-primary" disabled={busy || !path.trim()}>Assign project and pause</button>}
    </form>
  </EditorFrame>;
  return <section aria-label="Raticode projects"><div className="org-section-heading"><div><h3>Projects</h3><p className="org-note">Raticode projects owned by {config.name}, including their worktrees.</p></div><button type="button" className="org-button org-primary" onClick={() => choose("")}><Plus size={14} />Assign project</button></div>
    {!!roots.length && <TextField label="Find projects" value={query} onChange={setQuery} type="search" />}
    <ul className="org-directory">{roots.filter(root => root.toLowerCase().includes(query.toLowerCase())).map(root => <li key={root}><button type="button" onClick={() => choose(root)} title={root}><Folder size={17} /><span>{name(root)}<small>{root}{root === roots[0] ? " · Default project" : ""}</small></span><ChevronRight size={16} /></button></li>)}</ul>
    {!!roots.length && !roots.some(root => root.toLowerCase().includes(query.toLowerCase())) && <p className="org-note">No projects match your search.</p>}
    {!roots.length && <Empty icon={Folder} title="No projects assigned">Assign a project before starting employees. Organizations are available from every project.</Empty>}
  </section>;
}
