import { useEffect, useMemo, useRef, useState } from "react";
import { BookmarkPlus, CalendarClock, Check, Copy, LayoutTemplate, Lightbulb, Link2, Paperclip, Plus, Send, Sparkles, X } from "lucide-react";
import {
  LATE_WINDOW_CHOICES, charCounter, durationLabel, foldText, formatWhen, linkParts, localTimeZone, quickTimes, slotLabel, suggestTimes, templateBlanks,
  templateBody, templateDraft, timeTargets, timeZoneChoices, zonedInput, zonedToIso,
} from "../lib/socialApi.js";
import { PlatformIcon } from "./SocialPostsParts.jsx";

// New, edit and duplicate share this dialog. Times are typed into a draft as the clock reads in
// the chosen time zone and only turned into UTC on save, so clearing or retyping never snaps back.
export default function SocialPostEditor({ title, post, draft, template: startTemplate, data, busy, onCancel, onSave, onConnect, onSaveTemplate }) {
  const start = post || draft || null;
  const platforms = useMemo(() => data.platforms || [], [data.platforms]);
  const zone = start?.timezone || localTimeZone();
  const [form, setForm] = useState(() => ({
    title: start?.title || "",
    timing: start?.scheduled_at ? "later" : "now",
    when: zonedInput(start?.scheduled_at, zone),
    zone,
    late: start?.late_window_minutes ?? data.settings?.late_window_minutes ?? 120,
    rationale: start?.rationale || "",
    variants: (start?.variants || []).map(item => ({
      id: post ? item.id : undefined, account_id: item.account_id || null, platform: item.platform, text: item.text || "", link: item.link || "",
      options: { ...(item.options || {}) }, media: (item.media || []).map(media => ({ sha256: media.sha256, name: media.name, path: media.path })),
    })),
  }));
  const [active, setActive] = useState(0);
  const [mediaPath, setMediaPath] = useState("");
  const [tried, setTried] = useState(false);
  // A template being filled in, its blank values, and platforms it left out for want of an account.
  const [picking, setPicking] = useState(startTemplate || null);
  const [blanks, setBlanks] = useState({});
  const [skipped, setSkipped] = useState([]);
  const [templateName, setTemplateName] = useState(null);
  const [templateNote, setTemplateNote] = useState("");
  const [slots, setSlots] = useState({ key: "", items: [], note: "" });
  const firstField = useRef(null);
  const textArea = useRef(null);
  useEffect(() => { (form.variants.length ? textArea.current : firstField.current)?.focus(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const set = patch => setForm(current => ({ ...current, ...patch }));
  const setVariant = (index, patch) => setForm(current => ({ ...current, variants: current.variants.map((item, at) => at === index ? { ...item, ...patch } : item) }));

  const targets = useMemo(() => [
    ...data.accounts.map(account => ({ key: `account:${account.id}`, account_id: account.id, platform: account.platform, name: account.name, label: account.platform_label, off: !account.enabled, quiet: account.quiet })),
    ...platforms.filter(item => !item.api).map(item => ({ key: `manual:${item.id}`, account_id: null, platform: item.id, name: item.label, label: "You paste it", manual: true })),
  ], [data.accounts, platforms]);
  const keyOf = item => (item.account_id ? `account:${item.account_id}` : `manual:${item.platform}`);
  const chosen = new Set(form.variants.map(keyOf));
  const toggleTarget = target => {
    const index = form.variants.findIndex(item => keyOf(item) === target.key);
    if (index >= 0) {
      setForm(current => ({ ...current, variants: current.variants.filter(item => keyOf(item) !== target.key) }));
      setActive(current => Math.max(0, Math.min(current > index ? current - 1 : current, form.variants.length - 2)));
      return;
    }
    setForm(current => {
      if (current.variants.some(item => keyOf(item) === target.key)) return current;
      const seed = current.variants[active] || current.variants[0];
      setActive(current.variants.length);
      return { ...current, variants: [...current.variants, { account_id: target.account_id, platform: target.platform, text: seed?.text || "", link: seed?.link || "", options: {}, media: [] }] };
    });
  };

  const current = form.variants[active] || null;
  const currentPlatform = platforms.find(item => item.id === current?.platform);
  const currentTarget = current ? targets.find(item => item.key === keyOf(current)) : null;
  const counters = form.variants.map(item => charCounter(item, platforms.find(entry => entry.id === item.platform)));
  const over = counters.some(counter => counter.over);
  const scheduledIso = form.timing === "later" ? zonedToIso(form.when, form.zone) : null;
  const local = localTimeZone();
  const targetsKey = timeTargets(form.variants).join(",");
  // The backend's next good times for the chosen accounts; refetched when they or the zone change.
  useEffect(() => {
    if (form.timing !== "later" || !targetsKey) return undefined;
    let live = true;
    const timer = setTimeout(() => {
      suggestTimes(targetsKey.split(","), form.zone)
        .then(found => { if (live) setSlots({ key: `${targetsKey}|${form.zone}`, items: found.slots || [], note: found.note || "" }); })
        .catch(() => { if (live) setSlots({ key: `${targetsKey}|${form.zone}`, items: [], note: "" }); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [targetsKey, form.zone, form.timing]);
  const shownSlots = slots.key === `${targetsKey}|${form.zone}` ? slots.items : [];
  const templates = data.templates || [];
  const pickingBlanks = picking ? picking.blanks || templateBlanks([picking.title, ...(picking.variants || []).flatMap(item => [item.text, item.link])]) : [];
  const useTemplate = () => {
    const made = templateDraft(picking, blanks, data.accounts, platforms);
    setForm(current => ({ ...current, title: made.draft.title || current.title, variants: made.draft.variants }));
    setActive(0);
    setSkipped(made.skipped);
    setPicking(null);
    setBlanks({});
  };
  const saveAsTemplate = async () => {
    const body = templateBody(templateName, form);
    if (!body.name || !body.variants.length) return;
    const saved = await onSaveTemplate?.(body);
    if (saved) { setTemplateName(null); setTemplateNote(`Saved “${body.name}” to your templates.`); }
  };
  const problems = [];
  if (!form.variants.length) problems.push("Choose at least one place to post.");
  if (form.variants.some(item => item.platform !== "youtube" && !item.text.trim())) problems.push("Every platform needs some text.");
  if (form.variants.some(item => item.platform === "youtube" && !item.media.length)) problems.push("YouTube needs a video file.");
  if (form.timing === "later" && !scheduledIso) problems.push("Pick a date and time, or choose “As soon as I approve it”.");
  if (scheduledIso && Date.parse(scheduledIso) < Date.now() - 60_000) problems.push("That time has already passed.");

  const save = event => {
    event?.preventDefault?.();
    setTried(true);
    if (problems.length) return;
    onSave({
      title: form.title.trim(),
      scheduled_at: scheduledIso,
      timezone: form.zone,
      late_window_minutes: form.late,
      rationale: form.rationale.trim(),
      variants: form.variants.map(item => ({
        ...(item.id ? { id: item.id } : {}),
        ...(item.account_id ? { account_id: item.account_id } : { platform: item.platform }),
        text: item.text, link: item.link.trim(), options: item.options,
        media: item.media.map(media => (media.sha256 ? { sha256: media.sha256 } : { path: media.path })).filter(media => media.sha256 || media.path),
      })),
    });
  };
  const addMedia = () => {
    const path = mediaPath.trim();
    if (!path || !current) return;
    setVariant(active, { media: [...current.media, { path, name: path.split(/[\\/]/).pop() }] });
    setMediaPath("");
  };

  return <div className="sp-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onCancel(); }}>
    <form className="sp-editor" role="dialog" aria-modal="true" aria-labelledby="sp-editor-h" onSubmit={save} noValidate
      onKeyDown={event => {
        if (event.key === "Escape") { event.stopPropagation(); onCancel(); }
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) save(event);
      }}>
      <header className="sp-editor-head">
        <h2 id="sp-editor-h">{title}</h2>
        <button type="button" className="sp-icon-btn" aria-label="Close" onClick={onCancel}><X size={16} aria-hidden="true" /></button>
      </header>
      <div className="sp-editor-body">
        <div className="sp-editor-main">
          {!post && templates.length ? <section className="sp-template-pick" aria-label="Start from a template">
            {picking ? <div className="sp-template-fill">
              <p className="sp-side-h"><LayoutTemplate size={13} aria-hidden="true" /> {picking.name}{picking.description ? <small> · {picking.description}</small> : null}</p>
              {pickingBlanks.length ? <div className="sp-blank-grid">
                {pickingBlanks.map(name => <label key={name} className="sp-field"><span>{name.charAt(0).toUpperCase() + name.slice(1)}</span>
                  <input className="sp-input" value={blanks[name] || ""} onChange={event => setBlanks(current => ({ ...current, [name]: event.target.value }))} /></label>)}
              </div> : <p className="sp-field-hint">This template has no blanks to fill in.</p>}
              <p className="sp-field-hint">Blanks you leave empty stay in the text as {"{name}"}, so you can fill them in below.</p>
              <div className="sp-row sp-row-end">
                <button type="button" className="oh-btn small ghost" onClick={() => { setPicking(null); setBlanks({}); }}>Back</button>
                <button type="button" className="oh-btn small primary" onClick={useTemplate}><Check size={13} aria-hidden="true" />Use this template</button>
              </div>
            </div> : <>
              <span className="sp-side-h"><LayoutTemplate size={13} aria-hidden="true" /> Start from a template</span>
              <div className="sp-chips">
                {templates.map(item => <button key={item.id} type="button" className="sp-chip-btn" title={item.description || undefined} onClick={() => { setPicking(item); setBlanks({}); }}>
                  {item.name}{item.builtin ? <small className="sp-starter-tag">starter</small> : null}</button>)}
              </div>
            </>}
          </section> : null}
          {skipped.length ? <p className="sp-field-hint" role="status">Left out {skipped.join(", ")}: no account connected for {skipped.length === 1 ? "it" : "them"} yet.</p> : null}
          <label className="sp-field"><span>Title <em className="sp-optional">only you see it</em></span>
            <input ref={firstField} className="sp-input" value={form.title} maxLength={200} placeholder="e.g. 0.6 release announcement" onChange={event => set({ title: event.target.value })} /></label>

          <fieldset className="sp-targets">
            <legend>Post to</legend>
            {targets.some(item => !item.manual) ? null : <p className="sp-field-hint">No accounts connected yet. <button type="button" className="sp-link-btn" onClick={onConnect}>Connect one</button> to post automatically, or pick a site you paste into yourself.</p>}
            <div className="sp-chips">
              {targets.map(target => <button key={target.key} type="button" className={`sp-target ${target.manual ? "manual" : ""}`} aria-pressed={chosen.has(target.key)} onClick={() => toggleTarget(target)}>
                <PlatformIcon platform={target.platform} size={18} />
                <span>{target.name}{target.off ? " (off)" : ""}</span>
                {target.manual ? <small>paste</small> : null}
                {chosen.has(target.key) ? <Check size={12} aria-hidden="true" /> : <Plus size={12} aria-hidden="true" />}
              </button>)}
            </div>
          </fieldset>

          {current ? <div className="sp-compose">
            <div className="sp-compose-tabs" role="tablist" aria-label="Text for each platform">
              {form.variants.map((item, index) => {
                const target = targets.find(entry => entry.key === keyOf(item));
                const counter = counters[index];
                return <button key={keyOf(item)} type="button" role="tab" aria-selected={index === active} id={`sp-tab-${index}`} aria-controls="sp-compose-panel"
                  className={counter.over ? "over" : ""} onClick={() => setActive(index)}
                  onKeyDown={event => {
                    if (event.key === "ArrowRight") { event.preventDefault(); setActive((index + 1) % form.variants.length); }
                    if (event.key === "ArrowLeft") { event.preventDefault(); setActive((index - 1 + form.variants.length) % form.variants.length); }
                  }}>
                  <PlatformIcon platform={item.platform} size={16} />{target?.name || item.platform}
                  {counter.over ? <span className="sp-over-tag">too long</span> : null}
                </button>;
              })}
            </div>
            <div id="sp-compose-panel" role="tabpanel" aria-labelledby={`sp-tab-${active}`} className="sp-compose-panel">
              {current.platform === "youtube" ? <label className="sp-field"><span>Video title <em>{(current.options.title || "").length}/100</em></span>
                <input className="sp-input" maxLength={100} value={current.options.title || ""} onChange={event => setVariant(active, { options: { ...current.options, title: event.target.value } })} /></label> : null}
              <label className="sp-field"><span>{current.platform === "youtube" ? "Description" : "Text"}</span>
                <textarea ref={textArea} className="sp-input sp-textarea" rows={6} value={current.text} placeholder={`What should ${currentTarget?.name || "this account"} say?`}
                  onChange={event => setVariant(active, { text: event.target.value })} /></label>
              <CharMeter counter={counters[active]} platform={current.platform} />
              <div className="sp-row">
                <label className="sp-field"><span><Link2 size={12} aria-hidden="true" /> Link <em className="sp-optional">optional</em></span>
                  <input className="sp-input" type="url" value={current.link} placeholder="https://raticode.dev/…" onChange={event => setVariant(active, { link: event.target.value })} /></label>
                {current.platform === "linkedin" ? <label className="sp-field sp-field-narrow"><span>Who can see it</span>
                  <select className="sp-input" value={current.options.visibility || "PUBLIC"} onChange={event => setVariant(active, { options: { ...current.options, visibility: event.target.value } })}>
                    <option value="PUBLIC">Anyone</option><option value="CONNECTIONS">Connections only</option></select></label> : null}
              </div>
              {current.platform === "linkedin" && current.link ? <label className="sp-field"><span>Link preview title <em className="sp-optional">optional</em></span>
                <input className="sp-input" value={current.options.article_title || ""} onChange={event => setVariant(active, { options: { ...current.options, article_title: event.target.value } })} /></label> : null}
              <div className="sp-media-edit">
                <span className="sp-media-label"><Paperclip size={12} aria-hidden="true" /> {current.platform === "youtube" ? "Video file" : "Images or video"}</span>
                {current.media.length ? <ul className="sp-chips" aria-label="Attached files">{current.media.map((media, index) => <li key={`${media.sha256 || media.path}-${index}`} className="sp-tag">
                  <span>{media.name || media.path}</span>
                  <button type="button" aria-label={`Remove ${media.name || media.path}`} onClick={() => setVariant(active, { media: current.media.filter((_, at) => at !== index) })}><X size={11} aria-hidden="true" /></button>
                </li>)}</ul> : null}
                <div className="sp-row">
                  <input className="sp-input" aria-label="File path to attach" value={mediaPath} placeholder="/path/to/image.png" onChange={event => setMediaPath(event.target.value)}
                    onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); addMedia(); } }} />
                  <button type="button" className="oh-btn small" disabled={!mediaPath.trim()} onClick={addMedia}>Attach</button>
                </div>
              </div>
              {form.variants.length > 1 ? <button type="button" className="sp-link-btn" onClick={() => set({ variants: form.variants.map((item, index) => index === active ? item : { ...item, text: current.text, link: current.link }) })}>
                <Copy size={12} aria-hidden="true" />Use this text and link for every platform</button> : null}
            </div>
          </div> : <p className="sp-empty-inline">Pick where to post above, then write the text for each place.</p>}
        </div>

        <aside className="sp-editor-side">
          {current ? <section aria-label="Preview">
            <h3 className="sp-side-h">Preview on {currentPlatform?.label || current.platform}</h3>
            <PlatformPreview variant={current} target={currentTarget} platform={currentPlatform} />
          </section> : null}

          <fieldset className="sp-when">
            <legend><CalendarClock size={13} aria-hidden="true" /> When</legend>
            <label className="sp-radio"><input type="radio" name="sp-timing" checked={form.timing === "now"} onChange={() => set({ timing: "now" })} />As soon as I approve it</label>
            <label className="sp-radio"><input type="radio" name="sp-timing" checked={form.timing === "later"} onChange={() => set({ timing: "later", when: form.when || zonedInput(quickTimes(new Date(), form.zone)[1].at, form.zone) })} />At a set time</label>
            {form.timing === "later" ? <div className="sp-when-detail">
              <div className="sp-row">
                <label className="sp-field"><span>Date and time</span><input type="datetime-local" className="sp-input" value={form.when} onChange={event => set({ when: event.target.value })} /></label>
                <label className="sp-field"><span>Time zone</span>
                  <select className="sp-input" value={form.zone} onChange={event => set({ zone: event.target.value })}>
                    {timeZoneChoices(form.zone).map(item => <option key={item} value={item}>{item.replace(/_/g, " ")}{item === local ? " · yours" : ""}</option>)}
                  </select></label>
              </div>
              <div className="sp-chips" aria-label="Quick picks">
                {quickTimes(new Date(), form.zone).map(item => <button key={item.label} type="button" className="sp-chip-btn" onClick={() => set({ when: zonedInput(item.at, form.zone) })}>{item.label}</button>)}
              </div>
              {scheduledIso && form.zone !== local ? <p className="sp-field-hint">That's {formatWhen(scheduledIso)} your time.</p> : null}
              {shownSlots.length ? <div className="sp-suggest" aria-label="Good times to post">
                <Lightbulb size={13} aria-hidden="true" />
                <div>
                  <p><strong>Good times</strong> for {fitsLabel(shownSlots[0].fits, platforms)}</p>
                  <div className="sp-chips">
                    {shownSlots.map(slot => <button key={slot.at} type="button" className="sp-chip-btn" aria-pressed={zonedInput(slot.at, form.zone) === form.when}
                      title={slot.reason} onClick={() => set({ when: zonedInput(slot.at, form.zone) })}>{slotLabel(slot.at, form.zone)}</button>)}
                  </div>
                  <p className="sp-field-hint">Common advice, not a reading of your audience. These skip times within 3 hours of your other planned posts to the same accounts.</p>
                </div>
              </div> : null}
            </div> : null}
            <label className="sp-field"><span>If Raticode is late, still post up to</span>
              <select className="sp-input" value={form.late} onChange={event => set({ late: Number(event.target.value) })}>
                {[...new Set([...LATE_WINDOW_CHOICES, form.late])].sort((a, b) => a - b).map(minutes => <option key={minutes} value={minutes}>{durationLabel(minutes)} late</option>)}
              </select></label>
          </fieldset>

          <label className="sp-field"><span>Note to yourself <em className="sp-optional">optional</em></span>
            <input className="sp-input" value={form.rationale} placeholder="Why this post, what it's for" onChange={event => set({ rationale: event.target.value })} /></label>
        </aside>
      </div>
      <footer className="sp-editor-foot">
        {templateName !== null ? <span className="sp-template-save">
          <BookmarkPlus size={13} aria-hidden="true" />
          <input id="sp-template-name" aria-label="Template name" className="sp-input" value={templateName} maxLength={80} placeholder="Template name" autoFocus
            onChange={event => setTemplateName(event.target.value)}
            onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); saveAsTemplate(); } if (event.key === "Escape") { event.stopPropagation(); setTemplateName(null); } }} />
          <button type="button" className="oh-btn small" disabled={busy || !templateName.trim()} onClick={saveAsTemplate}>Save template</button>
          <button type="button" className="sp-icon-btn" aria-label="Don't save a template" onClick={() => setTemplateName(null)}><X size={14} aria-hidden="true" /></button>
        </span>
          : tried && problems.length ? <ul className="sp-problems" role="alert">{problems.map(item => <li key={item}>{item}</li>)}</ul>
          : templateNote ? <p className="sp-field-hint" role="status"><Check size={12} aria-hidden="true" /> {templateNote}</p>
          : <p className="sp-field-hint"><Sparkles size={12} aria-hidden="true" /> Saving puts the post in Needs review. Nothing goes out until you approve it.{over ? " Some text is over a platform's limit." : ""}</p>}
        <span className="grow" />
        {onSaveTemplate && form.variants.length && templateName === null
          ? <button type="button" className="oh-btn ghost" title="Keep this post's text, with any {blanks}, to reuse later"
            onClick={() => { setTemplateName(form.title.replace(/\s+/g, " ").trim().slice(0, 80)); setTemplateNote(""); }}>
            <BookmarkPlus size={13} aria-hidden="true" />Save as template</button> : null}
        <button type="button" className="oh-btn ghost" onClick={onCancel}>Cancel</button>
        <button type="submit" className="oh-btn primary" disabled={busy}><Send size={13} aria-hidden="true" />Save for review</button>
      </footer>
    </form>
  </div>;
}

export function CharMeter({ counter, platform }) {
  if (!counter.limit) return null;
  const ratio = Math.min(1, counter.count / counter.limit);
  const left = counter.limit - counter.count;
  const tone = counter.over ? "over" : ratio > 0.9 ? "near" : "";
  return <div className={`sp-meter ${tone}`}>
    <div className="sp-meter-track" aria-hidden="true"><span style={{ width: `${ratio * 100}%` }} /></div>
    <span className="sp-meter-text" aria-live="polite">{counter.over ? `${-left} over the limit` : `${left} left`} · {counter.count}/{counter.limit}{platform === "x" ? " · links count as 23" : ""}</span>
  </div>;
}

// A light, platform-flavored mock of how the post will read. Not pixel-exact; it shows the
// account, the text with line breaks, the link and attachments.
export function PlatformPreview({ variant, target, platform }) {
  const links = linkParts(variant);
  const name = target?.name || variant.account_name || platform?.label || variant.platform;
  return <div className={`sp-preview-card pv-${variant.platform}`}>
    <div className="sp-preview-who">
      <PlatformIcon platform={variant.platform} size={26} />
      <div><strong>{name}</strong><small>{platform?.label || variant.platform} · now</small></div>
    </div>
    {variant.options?.title ? <p className="sp-preview-title">{variant.options.title}</p> : null}
    {variant.text ? <FoldedText text={variant.text} platform={platform} /> : <p className="sp-preview-text sp-faint">Your text appears here.</p>}
    {variant.link && !variant.text.includes(variant.link) ? <p className="sp-preview-link">{variant.link}</p> : null}
    {variant.link && variant.platform !== "x" && variant.platform !== "discord" ? <div className="sp-preview-og"><span>{variant.options?.article_title || links.find(item => item.url === variant.link)?.host || variant.link}</span></div> : null}
    {variant.media?.length ? <p className="sp-preview-media"><Paperclip size={11} aria-hidden="true" />{variant.media.length} {variant.media.length === 1 ? "file" : "files"} attached</p> : null}
  </div>;
}

// The text as the feed shows it: the part before "see more", then the rest faded, with a
// note saying where the platform cuts it.
export function FoldedText({ text, platform }) {
  const { shown, rest } = foldText(text, platform);
  if (!rest) return <p className="sp-preview-text">{text}</p>;
  return <>
    <p className="sp-preview-text">{shown.replace(/\s+$/, "")}<span className="sp-fold-more"> …see more</span></p>
    <p className="sp-preview-text sp-fold-rest" aria-label="Shown after see more">{rest}</p>
    <p className="sp-fold-note">{platform?.label || "The feed"} shows about the first {platform?.fold_lines ? `${platform.fold_lines} lines or ` : ""}{platform?.fold_chars} characters. Put the hook there.</p>
  </>;
}

// "X, LinkedIn and Discord" for the platforms a suggested time suits.
function fitsLabel(fits = [], platforms = []) {
  const names = fits.map(id => platforms.find(item => item.id === id)?.label || id);
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0] || "these accounts";
}
