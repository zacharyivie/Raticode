import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { highlightSearchMatch } from "../lib/searchHighlight.js";

// Search owns a separate, paged viewport. The live conversation cache continues
// receiving replies without pulling a reader away from an older match.
export default function ThreadHistoryMatch({ repository, target, scrollRef, renderMessages }) {
  const [page, setPage] = useState(null);
  const [loading, setLoading] = useState("match");
  const [error, setError] = useState("");
  const [showSource, setShowSource] = useState(false);
  const rootRef = useRef(null);
  const busyRef = useRef(false);
  const generationRef = useRef(0);
  const positionedRef = useRef(false);
  const anchorRef = useRef(null);
  const loadRef = useRef(null);
  const failedDirectionRef = useRef("match");

  async function load(direction) {
    if (busyRef.current) return;
    busyRef.current = true;
    failedDirectionRef.current = direction;
    const generation = ++generationRef.current;
    setLoading(direction);
    setError("");
    try {
      const result = direction === "match" ? await repository.around(target.threadId, target.messageId)
        : direction === "older" ? await repository.page(target.threadId, page.before)
        : await repository.newer(target.threadId, page.after);
      if (generation !== generationRef.current) return;
      if (direction === "older") {
        const scroll = scrollRef.current;
        const element = [...rootRef.current.querySelectorAll("[data-history-anchor]")].find(node => node.getBoundingClientRect().bottom > scroll.getBoundingClientRect().top);
        anchorRef.current = { element, top: element?.getBoundingClientRect().top, height: scroll.scrollHeight, scrollTop: scroll.scrollTop };
      }
      setPage(current => direction === "match" ? result : direction === "older"
        ? { ...current, before: result.before, hasMore: result.hasMore, messages: [...result.messages, ...current.messages] }
        : { ...current, after: result.after, hasNewer: result.hasNewer, messages: [...current.messages, ...result.messages] });
      setLoading("");
    } catch (failure) {
      if (generation === generationRef.current) { setError(failure.message || "The matching history could not be loaded."); setLoading(""); }
    } finally { if (generation === generationRef.current) busyRef.current = false; }
  }
  loadRef.current = load;

  useEffect(() => {
    void loadRef.current("match");
    return () => { generationRef.current += 1; busyRef.current = false; CSS.highlights?.delete("rem-thread-match"); };
  }, []);

  useEffect(() => {
    const scroll = scrollRef.current;
    const onScroll = () => {
      if (!page || error || !positionedRef.current || busyRef.current) return;
      if (scroll.scrollTop < 80 && page.hasMore) void loadRef.current("older");
      else if (scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80 && page.hasNewer) void loadRef.current("newer");
    };
    scroll.addEventListener("scroll", onScroll);
    return () => scroll.removeEventListener("scroll", onScroll);
  }, [page, error, scrollRef]);

  useEffect(() => {
    // Tool disclosures can unmount and recreate their text after the initial
    // jump. Refresh ranges without scrolling or overriding the reader's choice.
    const observer = new MutationObserver(() => {
      if (!positionedRef.current) return;
      const element = rootRef.current?.querySelector("[data-thread-search-match]");
      if (element) highlightSearchMatch(element, target.query);
    });
    observer.observe(rootRef.current, { childList: true, characterData: true, subtree: true });
    return () => observer.disconnect();
  }, [target.query]);

  useLayoutEffect(() => {
    if (!page) return;
    const root = rootRef.current;
    const element = root.querySelector("[data-thread-search-match]");
    const ranges = element ? highlightSearchMatch(element, target.query) : [];
    if (!ranges.length && !showSource) { setShowSource(true); return; }
    const scroll = scrollRef.current;
    if (!positionedRef.current && element) {
      const bounds = ranges[0]?.getBoundingClientRect() || element.getBoundingClientRect();
      scroll.scrollTop += bounds.top - scroll.getBoundingClientRect().top - scroll.clientHeight / 3;
      element.focus({ preventScroll: true });
      positionedRef.current = true;
    } else if (anchorRef.current) {
      const anchor = anchorRef.current;
      scroll.scrollTop = anchor.element?.isConnected
        ? scroll.scrollTop + anchor.element.getBoundingClientRect().top - anchor.top
        : anchor.scrollTop + scroll.scrollHeight - anchor.height;
      anchorRef.current = null;
    }
  }, [page, target, showSource, scrollRef]);

  const loader = direction => loading === direction ? <div role="status" className="flex min-h-8 items-center justify-center gap-2 text-xs text-muted"><Loader2 aria-hidden="true" size={14} className="motion-safe:animate-spin" />{direction === "match" ? "Loading matching message…" : `Loading ${direction === "older" ? "earlier" : "later"} messages…`}</div> : null;
  return <div ref={rootRef} className="space-y-4" data-search-history>
    {loader("match")}
    {error ? <div className="text-xs text-muted"><p role="alert">{error}</p><button type="button" className="rounded px-2 py-1 text-brand focus-visible:outline-brand" onClick={() => loadRef.current(failedDirectionRef.current)}>Retry</button></div> : null}
    {loader("older") || (page?.hasMore ? <button className="block mx-auto min-h-8 rounded px-2 py-1 text-xs text-muted hover:text-ink focus-visible:outline-brand" disabled={Boolean(loading)} type="button" onClick={() => load("older")}>Load earlier messages</button> : null)}
    {page ? renderMessages(page.messages, target.messageId, showSource) : null}
    {loader("newer") || (page?.hasNewer ? <button className="block mx-auto min-h-8 rounded px-2 py-1 text-xs text-muted hover:text-ink focus-visible:outline-brand" disabled={Boolean(loading)} type="button" onClick={() => load("newer")}>Load later messages</button> : null)}
  </div>;
}
