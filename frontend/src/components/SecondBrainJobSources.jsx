import { useState } from "react";
import { brainRequest } from "../lib/secondBrainApi.js";
import { describeJobSources, jobSourceRulesError } from "../lib/secondBrainJobSources.js";
import { DraftInput } from "./SecondBrainParts.jsx";

const TYPES = [["note", "Markdown"], ["html", "HTML"], ["text", "Plain text"], ["pdf", "PDF"], ["capture", "Captured sources"], ["report", "Second Brain reports"]];
const splitTags = text => [...new Set(text.split(",").map(tag => tag.trim()).filter(Boolean))];

export default function JobSourceRules({ rules, onChange, job, collections, searches }) {
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const update = patch => onChange({ ...rules, ...patch });
  const location = rules.location;
  const locationValue = ["collection", "saved-search"].includes(location.kind) ? `${location.kind}:${location.id}` : location.kind;
  const previewKey = JSON.stringify({ sourceRules: rules, limits: job.limits, recipe: job.recipe });
  const currentPreview = preview?.key === previewKey ? preview : null;
  const error = jobSourceRulesError(rules, collections, searches);
  async function previewSources() {
    setPreviewing(true);
    try { setPreview({ key: previewKey, ...await brainRequest("job_sources", { job: { ...job, sourceRules: rules } }) }); }
    catch (error) { setPreview({ key: previewKey, error: error.message }); }
    finally { setPreviewing(false); }
  }
  return <fieldset className="sb-job-source-rules"><legend>Source rules</legend>
    <p className="sb-hint">Location, topic, time and additional filters apply together. Leave the topic blank to use all matching sources.</p>
    <div className="sb-form-grid">
      <label className="sb-field"><span>Source location</span><select value={locationValue} onChange={event => { const [kind, id = ""] = event.target.value.split(":"); update({ location: { ...location, kind, id } }); }}>
        <option value="all">All documents in this brain</option><option value="inbox">Inbox</option><option value="folder">A folder in this brain</option>
        {collections.map(item => <option key={item.id} value={`collection:${item.id}`}>Collection: {item.name}</option>)}
        {searches.map(item => <option key={item.id} value={`saved-search:${item.id}`}>Saved search: {item.name}</option>)}
        {["collection", "saved-search"].includes(location.kind) && !(location.kind === "collection" ? collections : searches).some(item => item.id === location.id) ? <option value={locationValue}>Unavailable selection</option> : null}
      </select></label>
      <label className="sb-field"><span>Time rule</span><select value={rules.time.mode} onChange={event => update({ time: { ...rules.time, mode: event.target.value } })}><option value="all">All dates</option><option value="since-success">Since the last successful run</option><option value="window">Within the last…</option></select></label>
      {location.kind === "folder" ? <div className="sb-field"><span>Folder within this brain</span><DraftInput label="Source folder" value={location.path} placeholder="e.g. Research/OpenAI" onCommit={path => update({ location: { ...location, path } })} /><label className="sb-check"><input type="checkbox" checked={location.recursive} onChange={event => update({ location: { ...location, recursive: event.target.checked } })} />Include subfolders</label></div> : null}
      <label className="sb-field"><span>Use source date</span><select value={rules.time.field} onChange={event => update({ time: { ...rules.time, field: event.target.value } })}><option value="activity">Added or changed</option><option value="added">Added to Second Brain</option><option value="modified">File modified</option><option value="published">Published</option><option value="captured">Captured</option></select><small>Also controls oldest/newest ordering. Sources without a known date do not match a time window.</small></label>
      {rules.time.mode === "window" ? <div className="sb-form-pair"><label className="sb-field"><span>Look back</span><DraftInput label="Look-back amount" inputMode="numeric" value={String(rules.time.count)} onCommit={text => { const count = Number(text); if (Number.isInteger(count) && count > 0) update({ time: { ...rules.time, count } }); }} /></label><label className="sb-field"><span>Time unit</span><select value={rules.time.unit} onChange={event => update({ time: { ...rules.time, unit: event.target.value } })}><option value="hours">Hours</option><option value="days">Days</option></select></label></div> : null}
      <label className="sb-field"><span>Topic or query</span><input value={rules.query} maxLength={2000} placeholder="e.g. OpenAI Dots" onChange={event => update({ query: event.target.value })} /><small>Matches titles and content. Quoted phrases and excluded words such as -archived are supported.</small></label>
      <label className="sb-field"><span>Topic matching</span><select value={rules.match} onChange={event => update({ match: event.target.value })}><option value="all">All words</option><option value="any">Any words</option><option value="phrase">Exact phrase</option></select></label>
      <label className="sb-field"><span>Source order</span><select value={rules.order} onChange={event => update({ order: event.target.value })}><option value="oldest">Oldest first</option><option value="newest">Newest first</option></select></label>
    </div>
    {rules.time.mode === "since-success" ? <p className="sb-hint">The first run considers all matching sources. Completed source versions are remembered. Limits, failures and cancellations leave unfinished sources for later runs; the checkpoint advances only after the backlog is complete.</p> : null}
    {job.recipe === "refresh-comparison" ? <p className="sb-hint">A comparison needs at least two matching collection sources. All dates includes unchanged sources for context.</p> : null}
    <details className="sb-job-source-filters"><summary>Additional source filters</summary><div className="sb-form-grid">
      <label className="sb-field"><span>Include tags</span><DraftInput label="Include source tags" value={rules.tags.join(", ")} placeholder="research, OpenAI" onCommit={text => update({ tags: splitTags(text) })} /></label>
      <label className="sb-field"><span>Tag matching</span><select value={rules.requireAllTags ? "all" : "any"} onChange={event => update({ requireAllTags: event.target.value === "all" })}><option value="any">Any included tag</option><option value="all">All included tags</option></select></label>
      <label className="sb-field"><span>Exclude tags</span><DraftInput label="Exclude source tags" value={rules.excludeTags.join(", ")} placeholder="archived, superseded" onCommit={text => update({ excludeTags: splitTags(text) })} /></label>
      <label className="sb-field"><span>Project</span><DraftInput label="Source project" value={rules.project} onCommit={project => update({ project })} /></label>
    </div>
      <fieldset className="sb-job-source-types"><legend>Source types <small>Any type when none are selected</small></legend>{TYPES.map(([id, label]) => <label key={id} className="sb-check"><input type="checkbox" checked={rules.types.includes(id)} onChange={event => update({ types: event.target.checked ? [...rules.types, id] : rules.types.filter(type => type !== id) })} />{label}</label>)}</fieldset>
      <label className="sb-check"><input type="checkbox" checked={rules.unreadOnly} onChange={event => update({ unreadOnly: event.target.checked })} />Unread sources only</label>
      <label className="sb-check"><input type="checkbox" checked={rules.starredOnly} onChange={event => update({ starredOnly: event.target.checked })} />Starred sources only</label>
      <label className="sb-check"><input type="checkbox" checked={rules.onlyUnprocessed} onChange={event => update({ onlyUnprocessed: event.target.checked })} />Only source versions not previously processed by this job</label>
      <label className="sb-check"><input type="checkbox" checked={rules.includeOutputs} onChange={event => update({ includeOutputs: event.target.checked })} />Include previous Second Brain outputs</label>
      <p className="sb-hint">Previous outputs are reports and summaries saved by Second Brain. Other documents remain eligible, including AI-written files.</p>
    </details>
    <p className="sb-job-source-description">{describeJobSources(rules, collections, searches)}</p>
    {error ? <p role="alert" className="sb-hint">{error}</p> : null}
    <button type="button" className="sb-button" disabled={previewing || Boolean(error)} onClick={previewSources}>{previewing ? "Checking sources…" : "Preview matching sources"}</button>
    {currentPreview ? <div className="sb-job-source-preview" aria-live="polite">{currentPreview.error ? <p role="alert">{currentPreview.error}</p> : <><p>{currentPreview.eligibleCount} eligible sources. The next run can process {currentPreview.selectedCount}; {currentPreview.remainingCount} remain beyond its limits.</p><details><summary>Sources for the next run</summary><ul className="sb-source-list">{currentPreview.sources.map(source => <li key={source.id}><span>{source.title}<small>{source.path}</small></span></li>)}</ul></details></>}</div> : preview ? <p className="sb-hint">Rules changed. Preview again to check the next run.</p> : null}
  </fieldset>;
}
