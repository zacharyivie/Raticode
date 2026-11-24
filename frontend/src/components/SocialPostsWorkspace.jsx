import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertOctagon, CalendarClock, Check, CheckCircle2, ChevronDown, Clock, Columns3, Copy, ExternalLink, Inbox, LayoutList, LayoutTemplate, Link2, Megaphone,
  PauseCircle, Pencil, PlayCircle, Plug, Plus, Search, Send, Sparkles, Trash2, TriangleAlert, Users, X,
} from "lucide-react";
import {
  LANES, accountNotice, approvePost, audienceLabel, bulkApprovable, cancelPost, charCounter, createPost, declinePost, deleteTemplate, duplicateDraft, editPost,
  foldText, formatWhen, groupPosts, lineDiff, linkParts, loadSocial, markPosted, optionLines, publishVariant, requestChanges, saveTemplate, setPaused, sourceLabel,
  variantActions, weekStrip,
} from "../lib/socialApi.js";
import SocialAccounts from "./SocialAccounts.jsx";
import SocialPostEditor, { CharMeter } from "./SocialPostEditor.jsx";
import { PlatformIcon } from "./SocialPostsParts.jsx";
import "./OrganizationHome.css";
import "./SocialPostsWorkspace.css";

const STATE_LABELS = {
  draft: "Draft", proposed: "Needs review", changes_requested: "Changes requested", declined: "Declined", expired: "Expired",
  approved: "Approved", scheduled: "Scheduled", due: "Due now", publishing: "Publishing", published: "Published",
  partly_published: "Partly published", failed: "Failed", missed: "Missed", cancelled: "Cancelled", needs_attention: "Needs attention",
  pending: "Waiting for approval", held_by_platform: "Held by the platform", needs_reconnect: "Reconnect the account",
  outcome_unknown: "Result unknown", held: "Held at a cap", ready_to_post: "Ready to paste", posted_by_you: "Posted by you",
};
const stateLabel = state => STATE_LABELS[state] || state;
const POLL_MS = 10_000;
const LAYOUT_KEY = "raticode.posts.layout";
const LANE_INFO = {
  review: { icon: Inbox, hint: "Waiting for your OK", empty: "Nothing waiting for you. New proposals from you, Rem and workflows land here." },
  scheduled: { icon: CalendarClock, hint: "Approved, waiting for their time", empty: "Approved posts wait here until their time." },
  due: { icon: Clock, hint: "Your tap is needed now", empty: "LinkedIn posts land here at their time for your Publish tap." },
  published: { icon: CheckCircle2, hint: "Out in the world", empty: "Sent posts show here with links to them." },
  problems: { icon: AlertOctagon, hint: "Need a decision", empty: "No problems. Failed, missed or held posts would show here." },
};

function readLayout() {
  try { return window.localStorage?.getItem(LAYOUT_KEY) === "list" ? "list" : "columns"; } catch { return "columns"; }
}

export default function SocialPostsWorkspace() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [view, setView] = useState("board");
  const [editor, setEditor] = useState(null);
  const [busy, setBusy] = useState(false);
  const [connectFor, setConnectFor] = useState("");

  const refresh = useCallback(async () => {
    try {
      setData(await loadSocial());
    } catch (failure) {
      setError(failure.message || "Posts couldn't be loaded.");
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(() => { if (typeof document === "undefined" || document.visibilityState !== "hidden") refresh(); }, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  // One backend call: { ok, value } or { ok: false, error }. The board reloads either way.
  const attempt = useCallback(async action => {
    setBusy(true);
    try {
      const value = await action();
      await refresh();
      return { ok: true, value };
    } catch (failure) {
      await refresh();
      return { ok: false, error: failure.message || "That didn't work." };
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  // Board actions show their errors once, in the banner.
  const run = useCallback(async action => {
    setError("");
    const outcome = await attempt(action);
    if (!outcome.ok) { setError(outcome.error); return null; }
    return outcome.value ?? true;
  }, [attempt]);

  const notices = (data?.accounts || []).map(accountNotice).filter(Boolean);
  const attention = (data?.accounts || []).some(account => ["needs_reconnect", "reconnect_soon"].includes(account.status));
  const openConnect = platform => { setView("accounts"); setConnectFor(platform || ""); setEditor(null); };
  const clearConnect = useCallback(() => setConnectFor(""), []);

  return <div className="oh sp" id="main-panel-posts" data-testid="posts-dashboard">
    <div className="oh-main">
      <header className="oh-top sp-top">
        <h1><Megaphone size={15} aria-hidden="true" /> Posts</h1>
        <span className="ui-badge sp-experimental" data-tone="warning" title="Posts is untested and may change. Nothing publishes until you approve it.">Experimental</span>
        <div className="oh-seg sp-viewseg" role="group" aria-label="Posts view">
          <button type="button" aria-pressed={view === "board"} onClick={() => setView("board")}>Board</button>
          <button type="button" aria-pressed={view === "templates"} onClick={() => setView("templates")}>Templates</button>
          <button type="button" aria-pressed={view === "accounts"} onClick={() => setView("accounts")}>
            Accounts{attention ? <span className="sp-attn" aria-label="needs attention"><TriangleAlert size={11} aria-hidden="true" /></span> : null}
          </button>
        </div>
        <span className="grow" />
        {data ? <button type="button" className={`oh-btn small ${data.paused ? "sp-paused" : ""}`} aria-pressed={data.paused} disabled={busy}
          onClick={() => run(() => setPaused(!data.paused))}>
          {data.paused ? <PlayCircle size={14} aria-hidden="true" /> : <PauseCircle size={14} aria-hidden="true" />}
          <span className="sp-hide-narrow">{data.paused ? "Paused · Resume publishing" : "Pause all publishing"}</span>
          <span className="sp-show-narrow">{data.paused ? "Resume" : "Pause"}</span>
        </button> : null}
        <button className="oh-btn primary" type="button" disabled={!data} onClick={() => setEditor({ title: "New post" })}><Plus size={14} aria-hidden="true" />New post</button>
      </header>
      {error ? <p role="alert" className="sp-error"><TriangleAlert size={13} aria-hidden="true" /><span>{error}</span><button type="button" className="oh-btn small ghost" onClick={() => setError("")}>Dismiss</button></p> : null}
      {notices.length && view === "board" ? <p className="sp-notice" role="status"><TriangleAlert size={13} aria-hidden="true" /><span>{notices.join(" · ")}</span>
        <button type="button" className="oh-btn small ghost" onClick={() => setView("accounts")}>Open Accounts</button></p> : null}
      {data?.paused ? <p className="sp-notice" role="status"><PauseCircle size={13} aria-hidden="true" />Publishing is paused. Times that pass while paused are skipped, never caught up.</p> : null}
      {!data ? <p className="oh-loading" role="status">Loading posts…</p>
        : view === "accounts" ? <SocialAccounts data={data} attempt={attempt} busy={busy} openLink={openLink} connectFor={connectFor} onConnectHandled={clearConnect} />
          : view === "templates" ? <Templates data={data} run={run} busy={busy} onUse={template => setEditor({ title: "New post", template })} />
          : <Board data={data} run={run} busy={busy} onEdit={post => setEditor({ title: "Edit post", post })}
            onDuplicate={post => setEditor({ title: "Duplicate post", draft: duplicateDraft(post) })}
            onNew={() => setEditor({ title: "New post" })} onConnect={() => openConnect("")} onTemplates={() => setView("templates")} />}
    </div>
    {editor && data ? <SocialPostEditor title={editor.title} post={editor.post} draft={editor.draft} template={editor.template} data={data} busy={busy}
      onCancel={() => setEditor(null)} onConnect={() => openConnect("")} onSaveTemplate={body => run(() => saveTemplate(body))}
      onSave={async body => {
        const done = editor.post ? await run(() => editPost(editor.post.id, { revision: editor.post.revision, ...body })) : await run(() => createPost(body));
        if (done) setEditor(null);
      }} /> : null}
  </div>;
}

function Board({ data, run, busy, onEdit, onDuplicate, onNew, onConnect, onTemplates }) {
  const [layout, setLayout] = useState(readLayout);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState(() => new Set());
  const [confirming, setConfirming] = useState(false);
  const chooseLayout = value => { setLayout(value); try { window.localStorage?.setItem(LAYOUT_KEY, value); } catch { /* storage off */ } };

  const needle = query.trim().toLowerCase();
  const matches = post => !needle || [post.title, post.rationale, ...(post.variants || []).flatMap(item => [item.text, item.account_name, item.platform_label])]
    .some(value => String(value || "").toLowerCase().includes(needle));
  const visible = useMemo(() => (data.posts || []).filter(matches), [data.posts, needle]); // eslint-disable-line react-hooks/exhaustive-deps
  const lanes = useMemo(() => groupPosts(visible), [visible]);
  const all = useMemo(() => groupPosts(data.posts), [data.posts]);

  // Drop selections that left Needs review (approved elsewhere, edited, declined).
  useEffect(() => {
    setSelected(current => {
      const review = new Set(all.review.map(post => post.id));
      const next = new Set([...current].filter(id => review.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [all]);

  const toggle = id => setSelected(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const chosen = all.review.filter(post => selected.has(post.id));
  const approvable = chosen.filter(post => bulkApprovable(post, data.platforms));
  const approveChosen = async () => {
    setConfirming(false);
    // One request per post, each pinned to the version on screen, exactly like a single Approve.
    for (const post of approvable) {
      if (!await run(() => approvePost(post))) break;
    }
    setSelected(new Set());
  };
  const declineChosen = async () => {
    for (const post of chosen) {
      if (!await run(() => declinePost(post.id, ""))) break;
    }
    setSelected(new Set());
  };

  if (!(data.posts || []).length) return <div className="sp-view"><BoardEmpty data={data} onNew={onNew} onConnect={onConnect} onTemplates={onTemplates} /></div>;

  const cardProps = { data, run, busy, onEdit, onDuplicate, selected, toggle };
  const listLanes = filter === "all" ? [...LANES.map(([lane]) => lane), "closed"] : [filter];
  return <div className="sp-view">
    <WeekStrip posts={data.posts} />
    <div className="sp-toolbar">
      <div className="oh-seg" role="group" aria-label="Board layout">
        <button type="button" aria-pressed={layout === "columns"} onClick={() => chooseLayout("columns")}><Columns3 size={13} aria-hidden="true" /> Columns</button>
        <button type="button" aria-pressed={layout === "list"} onClick={() => chooseLayout("list")}><LayoutList size={13} aria-hidden="true" /> List</button>
      </div>
      <label className="sp-search"><Search size={13} aria-hidden="true" /><span className="oh-sr">Search posts</span>
        <input type="search" value={query} placeholder="Search posts" onChange={event => setQuery(event.target.value)} /></label>
      {layout === "list" ? <div className="sp-filters" role="group" aria-label="Show">
        {[["all", "All", visible.length], ...LANES.map(([lane, label]) => [lane, label, lanes[lane].length]), ["closed", "Closed", lanes.closed.length]].map(([value, label, count]) =>
          <button key={value} type="button" className="sp-chip-btn" aria-pressed={filter === value} onClick={() => setFilter(value)}>{label} <span className="n">{count}</span></button>)}
      </div> : null}
    </div>
    {chosen.length ? <div className="sp-bulk" role="region" aria-label="Selected posts">
      {confirming ? <>
        <div className="sp-bulk-text">
          <strong>Approve {approvable.length} {approvable.length === 1 ? "post" : "posts"}?</strong> Each goes out exactly as shown, at its own time:
          <ul>{approvable.map(post => <li key={post.id}>{post.title || "Untitled post"} · {formatWhen(post.scheduled_at, { timeZone: post.timezone || undefined })} · {audienceLabel(post)}</li>)}</ul>
        </div>
        <button type="button" className="oh-btn small primary" disabled={busy} onClick={approveChosen}><Check size={13} aria-hidden="true" />Yes, approve {approvable.length}</button>
        <button type="button" className="oh-btn small ghost" onClick={() => setConfirming(false)}>Go back</button>
      </> : <>
        <span className="sp-bulk-text"><strong>{chosen.length} selected</strong>{approvable.length < chosen.length ? ` · ${chosen.length - approvable.length} with warnings or text over a limit need their own review` : ""}</span>
        <button type="button" className="oh-btn small primary" disabled={busy || !approvable.length} onClick={() => setConfirming(true)}><Check size={13} aria-hidden="true" />Approve {approvable.length || ""}…</button>
        <button type="button" className="oh-btn small" disabled={busy} onClick={declineChosen}><X size={13} aria-hidden="true" />Decline {chosen.length}</button>
        <button type="button" className="oh-btn small ghost" onClick={() => setSelected(new Set())}>Clear</button>
      </>}
    </div> : null}
    {needle && !visible.length ? <p className="sp-empty-inline" role="status">No posts match “{query}”.</p> : null}
    {layout === "columns" ? <div className="sp-lanes">
      {LANES.map(([lane, label]) => {
        const Icon = LANE_INFO[lane].icon;
        return <section key={lane} className={`sp-lane sp-lane-${lane}`} aria-labelledby={`sp-lane-${lane}`}>
          <header>
            <span id={`sp-lane-${lane}`} className="sp-lane-title"><Icon size={13} aria-hidden="true" />{label}</span>
            <span className="n" aria-label={`${lanes[lane].length} posts`}>{lanes[lane].length}</span>
            <small>{LANE_INFO[lane].hint}</small>
          </header>
          <div className="sp-lane-body">
            {lanes[lane].length ? lanes[lane].map(post => <PostCard key={post.id} post={post} {...cardProps} />)
              : <p className="sp-lane-empty">{LANE_INFO[lane].empty}</p>}
          </div>
        </section>;
      })}
    </div> : <div className="sp-list">
      {listLanes.map(lane => lanes[lane].length ? <section key={lane} aria-label={lane === "closed" ? "Closed" : LANES.find(([id]) => id === lane)[1]}>
        <h2 className="sp-list-h">{lane === "closed" ? "Closed" : LANES.find(([id]) => id === lane)[1]} <span className="n">{lanes[lane].length}</span></h2>
        {lanes[lane].map(post => <PostCard key={post.id} post={post} {...cardProps} />)}
      </section> : filter === lane ? <p key={lane} className="sp-empty-inline">{LANE_INFO[lane]?.empty || "No closed posts."}</p> : null)}
    </div>}
    {layout === "columns" && lanes.closed.length ? <details className="sp-closed">
      <summary>Closed: declined, cancelled or expired ({lanes.closed.length})</summary>
      {lanes.closed.map(post => <PostCard key={post.id} post={post} {...cardProps} />)}
    </details> : null}
  </div>;
}

function BoardEmpty({ data, onNew, onConnect, onTemplates }) {
  const hasAccounts = data.accounts.length > 0;
  return <div className="sp-empty-state sp-board-empty">
    <Megaphone size={30} aria-hidden="true" className="sp-empty-mark" />
    <h3>Your posting queue is empty</h3>
    <p>Write posts yourself or let Rem and your workflows draft them. Every post waits here for your OK, then goes out on time.</p>
    <ol className="sp-start">
      <li className={hasAccounts ? "done" : ""}>
        <span className="sp-start-n" aria-hidden="true">{hasAccounts ? <Check size={13} /> : "1"}</span>
        <div><strong>Connect an account</strong><p>{hasAccounts ? `${data.accounts.length} connected.` : "Discord, X, LinkedIn, a Facebook Page or YouTube."}</p></div>
        {hasAccounts ? <span className="sp-done-word">Done</span> : <button type="button" className="oh-btn small" onClick={onConnect}><Plug size={13} aria-hidden="true" />Connect</button>}
      </li>
      <li>
        <span className="sp-start-n" aria-hidden="true">2</span>
        <div><strong>Write a post, or ask Rem</strong><p>Start from a template such as New release, or ask Rem: “Draft a post about our latest release for X and Discord.”</p></div>
        <span className="sp-start-actions">
          <button type="button" className="oh-btn small" onClick={onTemplates}><LayoutTemplate size={13} aria-hidden="true" />Templates</button>
          <button type="button" className="oh-btn small primary" onClick={onNew}><Plus size={13} aria-hidden="true" />New post</button>
        </span>
      </li>
      <li>
        <span className="sp-start-n" aria-hidden="true">3</span>
        <div><strong>Approve it</strong><p>Nothing goes out until you click Approve. Any later change sends it back for another look.</p></div>
      </li>
    </ol>
  </div>;
}

function WeekStrip({ posts }) {
  const days = weekStrip(posts);
  const total = days.reduce((sum, day) => sum + day.items.length, 0);
  return <section className="sp-week-wrap" aria-label="The next seven days">
    <h2 className="sp-week-h">Next 7 days <span>{total ? `${total} ${total === 1 ? "post" : "posts"} planned` : "Nothing planned yet"}</span></h2>
    <ol className="sp-week">
      {days.map(({ date, items }, index) => <li key={date.toISOString()} className={`${items.length ? "has" : ""} ${index === 0 ? "today" : ""}`}>
        <span className="d">{index === 0 ? "Today" : new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(date)} <b>{date.getDate()}</b></span>
        {items.length ? items.slice(0, 3).map(post => <span key={post.id} className="t" title={post.title}>
          <span className="tm">{new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(post.scheduled_at))}</span> {post.title || "Untitled"}</span>)
          : <span className="c">Free</span>}
        {items.length > 3 ? <span className="c">+{items.length - 3} more</span> : null}
      </li>)}
    </ol>
  </section>;
}

function PostCard({ post, data, run, busy, onEdit, onDuplicate, selected, toggle }) {
  const [tab, setTab] = useState(post.variants?.[0]?.id || "");
  const [noteFor, setNoteFor] = useState("");
  const [note, setNote] = useState("");
  const variant = post.variants?.find(item => item.id === tab) || post.variants?.[0] || null;
  const platform = data.platforms.find(item => item.id === variant?.platform);
  const reviewing = ["proposed", "changes_requested", "draft"].includes(post.state);
  const live = ["approved", "scheduled", "due", "publishing", "needs_attention", "missed", "failed", "partly_published"].includes(post.state);
  const previous = post.previous?.variants?.find(item => item.account_id === variant?.account_id && item.platform === variant?.platform)
    || post.previous?.variants?.find(item => item.account_id === variant?.account_id);
  const submitNote = async () => {
    const action = noteFor === "decline" ? () => declinePost(post.id, note) : () => requestChanges(post.id, note);
    if (await run(action)) { setNoteFor(""); setNote(""); }
  };
  const title = post.title || "Untitled post";
  return <article className={`sp-card st-${post.state} ${selected.has(post.id) ? "selected" : ""}`} aria-label={title}>
    <div className="sp-card-head">
      {reviewing ? <input type="checkbox" className="sp-select" aria-label={`Select ${title}`} checked={selected.has(post.id)} onChange={() => toggle(post.id)} /> : null}
      <h3>{title}</h3>
      <span className={`sp-state st-${post.state}`}>{stateLabel(post.state)}</span>
    </div>
    <p className="sp-meta"><CalendarClock size={12} aria-hidden="true" /><span>{formatWhen(post.scheduled_at, { timeZone: post.timezone || undefined })}</span>
      <span className="sp-meta-src"><Users size={11} aria-hidden="true" />{sourceLabel(post.created_by)}</span></p>
    {post.filled_in ? <p className="sp-filled"><Sparkles size={12} aria-hidden="true" />Filled in by {sourceLabel(post.created_by)}. You can edit any of it before approving.</p> : null}
    {post.flags?.length ? <ul className="sp-flags" aria-label="Check before approving">
      {post.flags.map(flag => <li key={`${flag.kind}:${flag.message}`}><TriangleAlert size={12} aria-hidden="true" />{flag.message}</li>)}
    </ul> : null}
    {post.timing?.length ? <ul className="sp-timing" aria-label="About this post's time">
      {post.timing.map(hint => <li key={`${hint.kind}:${hint.message}`}><Clock size={12} aria-hidden="true" />{hint.message}</li>)}
    </ul> : null}
    {post.variants?.length > 1 ? <div className="sp-tabs" role="tablist" aria-label="Platforms">
      {post.variants.map(item => <button key={item.id} type="button" role="tab" aria-selected={item.id === variant?.id} className={`st-${item.state}`}
        onClick={() => setTab(item.id)}><PlatformIcon platform={item.platform} size={14} />{item.account_name || item.platform_label || item.platform}</button>)}
    </div> : null}
    {variant ? <VariantPreview post={post} variant={variant} platform={platform} previous={previous} run={run} busy={busy} /> : null}
    {post.rationale ? <p className="sp-rationale"><strong>Why: </strong>{post.rationale}</p> : null}
    {post.sources?.length ? <details className="sp-sources"><summary>Sources ({post.sources.length})</summary>
      <ul>{post.sources.map(source => <li key={source.url}><a href={source.url} onClick={event => { event.preventDefault(); openLink(source.url); }}>{source.title || source.url}</a></li>)}</ul>
    </details> : null}
    {noteFor ? <div className="sp-note">
      <label className="oh-sr" htmlFor={`note-${post.id}`}>{noteFor === "decline" ? "Why decline (optional)" : "What should change"}</label>
      <input id={`note-${post.id}`} className="sp-input" value={note} autoFocus placeholder={noteFor === "decline" ? "Why decline (optional)" : "What should change"} onChange={event => setNote(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter") submitNote(); if (event.key === "Escape") setNoteFor(""); }} />
      <button type="button" className="oh-btn small" disabled={busy} onClick={submitNote}>{noteFor === "decline" ? "Decline" : "Send"}</button>
      <button type="button" className="oh-btn small ghost" onClick={() => setNoteFor("")}>Cancel</button>
    </div> : null}
    <div className="sp-actions">
      {reviewing ? <>
        <button type="button" className="oh-btn small primary" disabled={busy || !post.variants?.length} onClick={() => run(() => approvePost(post))}>
          <Check size={13} aria-hidden="true" />{post.scheduled_at ? "Approve and schedule" : "Approve and publish now"}
        </button>
        <button type="button" className="oh-btn small" disabled={busy} onClick={() => onEdit(post)}><Pencil size={12} aria-hidden="true" />Edit</button>
      </> : null}
      {live ? <button type="button" className="oh-btn small" disabled={busy} onClick={() => onEdit(post)}><Pencil size={12} aria-hidden="true" />Edit or change time</button> : null}
      <details className="sp-more">
        <summary className="oh-btn small ghost" aria-label={`More actions for ${title}`}>More<ChevronDown size={12} aria-hidden="true" /></summary>
        <div className="sp-more-menu">
          {reviewing && post.scheduled_at ? <button type="button" disabled={busy || !post.variants?.length} onClick={() => run(() => approvePost(post, { publishNow: true }))}><Send size={13} aria-hidden="true" />Approve and publish now</button> : null}
          {reviewing ? <button type="button" disabled={busy} onClick={() => setNoteFor("changes")}><Pencil size={13} aria-hidden="true" />Request changes</button> : null}
          <button type="button" disabled={busy} onClick={() => onDuplicate(post)}><Copy size={13} aria-hidden="true" />Duplicate</button>
          {reviewing ? <button type="button" disabled={busy} onClick={() => setNoteFor("decline")}><X size={13} aria-hidden="true" />Decline</button> : null}
          {live ? <button type="button" className="danger" disabled={busy} onClick={() => run(() => cancelPost(post.id))}><X size={13} aria-hidden="true" />Cancel post</button> : null}
        </div>
      </details>
    </div>
    {live ? <p className="sp-hint">Editing an approved post sends it back to review.</p> : null}
  </article>;
}

function VariantPreview({ post, variant, platform, previous, run, busy }) {
  const counter = charCounter(variant, platform);
  const links = linkParts(variant);
  const actions = variantActions(variant);
  const [linkInput, setLinkInput] = useState("");
  const [askLink, setAskLink] = useState(false);
  const diff = previous && previous.text !== variant.text ? lineDiff(previous.text || "", variant.text || "") : null;
  const copy = () => navigator.clipboard?.writeText(variant.text + (variant.link && !variant.text.includes(variant.link) ? `\n${variant.link}` : ""));
  const linkedInWaits = variant.delivery === "tap_to_publish" && ["scheduled", "approved"].includes(variant.state);
  return <div className="sp-variant" role={post.variants?.length > 1 ? "tabpanel" : undefined}>
    <div className="sp-variant-state">
      <PlatformIcon platform={variant.platform} size={18} label={variant.platform_label || variant.platform} />
      <span className="sp-variant-who">{variant.account_name || variant.platform_label}</span>
      <span className={`sp-vstate st-${variant.state}`}>{stateLabel(variant.state)}</span>
      {variant.delivery === "platform_held" ? <span className="sp-chip">Sent to {variant.platform_label} early</span> : null}
      {variant.delivery === "manual" ? <span className="sp-chip">You paste this one</span> : null}
      {variant.cost_usd != null ? <span className="sp-chip" title="X charges per post">${Number(variant.cost_usd).toFixed(3)}</span> : null}
    </div>
    {variant.options?.title ? <p className="sp-vtitle">{variant.options.title}</p> : null}
    {diff ? <pre className="sp-diff" aria-label="Changes since you last saw it">{diff.map((line, index) => <span key={index} className={line.kind}>{line.kind === "added" ? "+ " : line.kind === "removed" ? "− " : "  "}{line.text}{"\n"}</span>)}</pre>
      : <CardText text={variant.text} platform={platform} />}
    {links.length ? <ul className="sp-links" aria-label="Links">
      {links.map(link => <li key={link.url}><Link2 size={11} aria-hidden="true" /><span>{link.before}<mark>{link.host}</mark>{link.after}</span></li>)}
    </ul> : null}
    {optionLines(variant).length ? <dl className="sp-options" aria-label="Also sent with this post">
      {optionLines(variant).map(line => <div key={line.key}><dt>{line.label}</dt><dd>{line.value}</dd></div>)}
    </dl> : null}
    {variant.media?.length ? <ul className="sp-media">{variant.media.map(item => <li key={item.sha256}>{item.name} · {item.mime} · {Math.ceil((item.size || 0) / 1024)} KB</li>)}</ul> : null}
    <CharMeter counter={counter} platform={variant.platform} />
    {linkedInWaits ? <p className="sp-hint">You'll be asked to tap Publish at {formatWhen(post.scheduled_at)}.</p> : null}
    {variant.message ? <p className="sp-message"><TriangleAlert size={12} aria-hidden="true" />{variant.message}</p> : null}
    {variant.next_retry_at ? <p className="sp-hint">Next try {formatWhen(variant.next_retry_at)}.</p> : null}
    {variant.url ? <p className="sp-hint"><a href={variant.url} onClick={event => { event.preventDefault(); openLink(variant.url); }}><ExternalLink size={11} aria-hidden="true" /> View the post</a></p> : null}
    {actions.length ? <div className="sp-actions">
      {actions.includes("publish") ? <button type="button" className="oh-btn primary sp-publish" disabled={busy} onClick={() => run(() => publishVariant(post, variant.id))}><Send size={15} aria-hidden="true" />Publish</button> : null}
      {actions.includes("publish-now") ? <button type="button" className="oh-btn small" disabled={busy} onClick={() => run(() => publishVariant(post, variant.id))}><Send size={13} aria-hidden="true" />Publish now</button> : null}
      {actions.includes("send-again") ? <button type="button" className="oh-btn small" disabled={busy} onClick={() => run(() => publishVariant(post, variant.id))}>Send again</button> : null}
      {actions.includes("copy") ? <button type="button" className="oh-btn small" onClick={copy}><Copy size={13} aria-hidden="true" />Copy text</button> : null}
      {actions.includes("open") && variant.open_url ? <button type="button" className="oh-btn small" onClick={() => openLink(variant.open_url)}><ExternalLink size={13} aria-hidden="true" />Open {variant.platform_label}</button> : null}
      {actions.includes("mark-posted") || actions.includes("it-posted") ? <button type="button" className="oh-btn small" disabled={busy} onClick={() => setAskLink(true)}>
        <Check size={13} aria-hidden="true" />{actions.includes("it-posted") ? "It posted" : "Mark posted"}</button> : null}
    </div> : null}
    {askLink ? <div className="sp-note">
      <label className="oh-sr" htmlFor={`link-${variant.id}`}>Link to the post</label>
      <input id={`link-${variant.id}`} className="sp-input" value={linkInput} placeholder="Link to the post (optional)" onChange={event => setLinkInput(event.target.value)} />
      <button type="button" className="oh-btn small" disabled={busy} onClick={async () => { if (await run(() => markPosted(post, variant.id, linkInput.trim()))) { setAskLink(false); setLinkInput(""); } }}>Save</button>
      <button type="button" className="oh-btn small ghost" onClick={() => setAskLink(false)}>Cancel</button>
    </div> : null}
  </div>;
}

// The card's text with a marker where the platform's feed cuts it with "see more".
function CardText({ text, platform }) {
  const { shown, rest } = foldText(text, platform);
  if (!rest) return <p className="sp-text">{text}</p>;
  return <p className="sp-text">{shown}<span className="sp-fold-mark" title={`${platform?.label || "The feed"} cuts the text here; readers tap “see more” for the rest`}>see more</span>{rest}</p>;
}

function Templates({ data, run, busy, onUse }) {
  const [removing, setRemoving] = useState("");
  const templates = data.templates || [];
  const label = id => data.platforms.find(item => item.id === id)?.label || id;
  const own = templates.filter(item => !item.builtin);
  const starters = templates.filter(item => item.builtin);
  const card = item => <article key={item.id} className="sp-template-card" aria-label={item.name}>
    <header>
      <h3>{item.name}</h3>
      {item.builtin ? <span className="sp-chip">Starter</span> : null}
    </header>
    {item.description ? <p className="sp-hint">{item.description}</p> : null}
    <p className="sp-template-platforms">{(item.variants || []).map(variant => <span key={variant.account_id || variant.platform} className="sp-tag">
      <PlatformIcon platform={variant.platform} size={14} />{label(variant.platform)}</span>)}</p>
    {item.blanks?.length ? <p className="sp-hint">Blanks: {item.blanks.map(name => `{${name}}`).join(" ")}</p> : null}
    {item.title ? <p className="sp-template-sample"><strong>Title:</strong> {item.title}</p> : null}
    <p className="sp-template-sample">{item.variants?.[0]?.text}</p>
    <div className="sp-actions">
      <button type="button" className="oh-btn small primary" onClick={() => onUse(item)}><Plus size={13} aria-hidden="true" />Use</button>
      {!item.builtin ? (removing === item.id ? <>
        <span className="sp-hint">Delete this template?</span>
        <button type="button" className="oh-btn small danger" disabled={busy} onClick={async () => { if (await run(() => deleteTemplate(item.id))) setRemoving(""); }}>Delete</button>
        <button type="button" className="oh-btn small ghost" onClick={() => setRemoving("")}>Keep</button>
      </> : <button type="button" className="oh-btn small ghost" onClick={() => setRemoving(item.id)}><Trash2 size={12} aria-hidden="true" />Delete</button>) : null}
    </div>
  </article>;
  return <div className="sp-view sp-templates">
    <p className="sp-intro">Templates are posts you reuse. Write blanks in braces, like {"{version}"}, and fill them in each time. A post made from a template still waits for your approval. Rem can use them too: ask “announce 0.6 with my release template”.</p>
    <section aria-labelledby="sp-own-templates">
      <h2 id="sp-own-templates" className="sp-list-h">Your templates <span className="n">{own.length}</span></h2>
      {own.length ? <div className="sp-template-grid">{own.map(card)}</div>
        : <p className="sp-empty-inline">None yet. Write a post with blanks in braces, then choose <strong>Save as template</strong> in the editor. Or use a starter below and save your version.</p>}
    </section>
    <section aria-labelledby="sp-starter-templates">
      <h2 id="sp-starter-templates" className="sp-list-h">Starters <span className="n">{starters.length}</span></h2>
      <div className="sp-template-grid">{starters.map(card)}</div>
    </section>
  </div>;
}

function openLink(url) {
  if (/^https?:\/\//i.test(url || "")) window.open(url, "_blank", "noopener,noreferrer");
}
