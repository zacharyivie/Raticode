import { isLocalFileLink, resolveMarkdownLinkPath } from "../lib/fileLinks.js";
import { remarkFileMentions } from "../lib/fileMentions.js";
import { Suspense, lazy, memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";

// Highlighting and ```rem-chart charts load on first use, outside the main bundle.
const RichCodeBlock = lazy(() => import("./RichCodeBlock.jsx"));
// Web images load through the backend; the loader is its own small chunk too.
const WebImage = lazy(() => import("./WebImage.jsx"));
const DEFAULT_REMARK_PLUGINS = [remarkGfm];
const imagePlaceholder = <span aria-hidden="true" className="block h-40 w-full max-w-md rounded-lg bg-slate-100 dark:bg-[#26262a]" />;

function MarkdownContent({
  className = "",
  compact = false,
  fileMentions = null,
  inverse = false,
  onOpenRelativeLink,
  sourcePath = "",
  value,
}) {
  const rootRef = useRef(null);
  const onOpenRelativeLinkRef = useRef(onOpenRelativeLink);
  onOpenRelativeLinkRef.current = onOpenRelativeLink;
  const spacing = compact ? "space-y-2" : "space-y-4";
  // Full file paths the backend found in the text open like links.
  const mentionsKey = fileMentions?.length ? JSON.stringify(fileMentions) : "";
  const remarkPlugins = useMemo(
    () => (mentionsKey ? [remarkGfm, remarkFileMentions(JSON.parse(mentionsKey))] : DEFAULT_REMARK_PLUGINS),
    [mentionsKey],
  );

  const handleLinkClick = useCallback((event, href) => {
    if (!href) return;
    if (href.startsWith("#")) {
      event.preventDefault();
      const id = decodeURIComponent(href.slice(1));
      const target = [...(rootRef.current?.querySelectorAll("[id]") ?? [])]
        .find((element) => element.id === id);
      target?.scrollIntoView({ block: "start", behavior: "smooth" });
      return;
    }
    event.preventDefault();
    if (isLocalFileLink(href) && onOpenRelativeLinkRef.current) {
      onOpenRelativeLinkRef.current(href);
      return;
    }
    if (isExternalUrl(href)) window.open(websiteLinkHref(href), "_blank", "noopener,noreferrer");
  }, []);

  const components = useMemo(() => ({
    a({ children, href = "", title }) {
      const external = isExternalUrl(href);
      return (
        <a
          className={`font-medium underline decoration-current underline-offset-2 transition ${
            inverse ? "text-white" : "text-brand"
          }`}
          href={external ? websiteLinkHref(href) : href}
          title={resolveMarkdownLinkPath(sourcePath, href) || title || href}
          rel={external ? "noreferrer" : undefined}
          target={external ? "_blank" : undefined}
          onClick={(event) => handleLinkClick(event, href)}
        >
          {children}
        </a>
      );
    },
    blockquote({ children }) {
      return (
        <blockquote
          className={`border-l pl-4 ${inverse ? "border-white/40" : "border-line text-muted"}`}
        >
          {children}
        </blockquote>
      );
    },
    code({ children, className: codeClassName = "" }) {
      return (
        <code
          className={`${codeClassName} rounded px-1 py-0.5 font-mono text-[0.9em] ${
            inverse ? "bg-white/15 text-white" : "bg-slate-100 text-ink dark:bg-[#2a2a2e]"
          }`}
        >
          {children}
        </code>
      );
    },
    h1({ children, id }) {
      return <h1 id={id} className="pt-2 text-2xl font-semibold leading-tight text-inherit">{children}</h1>;
    },
    h2({ children, id }) {
      return <h2 id={id} className="pt-2 text-xl font-semibold leading-tight text-inherit">{children}</h2>;
    },
    h3({ children, id }) {
      return <h3 id={id} className="pt-1 text-base font-semibold leading-snug text-inherit">{children}</h3>;
    },
    h4({ children, id }) {
      return <h4 id={id} className="pt-1 text-sm font-semibold leading-snug text-inherit">{children}</h4>;
    },
    h5({ children, id }) {
      return <h5 id={id} className="text-sm font-semibold leading-snug text-inherit">{children}</h5>;
    },
    h6({ children, id }) {
      return <h6 id={id} className="text-xs font-semibold leading-snug text-inherit">{children}</h6>;
    },
    hr() {
      return <hr className={inverse ? "border-white/25" : "border-line"} />;
    },
    img({ alt = "", src = "" }) {
      // Web images load through the desktop backend, so the site never sees this
      // window's request. They open full size in a browser tab.
      if (isExternalUrl(src)) return <Suspense fallback={imagePlaceholder}><WebImage alt={alt} src={websiteLinkHref(src)} /></Suspense>;
      // A broken local image hides.
      return (
        <img
          alt={alt}
          className="max-h-[32rem] max-w-full rounded-lg object-contain"
          loading="lazy"
          referrerPolicy="no-referrer"
          src={src}
          title={alt || undefined}
          onError={(event) => { event.currentTarget.hidden = true; }}
        />
      );
    },
    input(props) {
      const inputProps = { ...props };
      delete inputProps.node;
      return <input {...inputProps} className="mr-2 align-middle accent-brand" />;
    },
    ol({ children, className: listClassName = "" }) {
      return <ol className={`${listClassName} ml-6 list-decimal space-y-1`}>{children}</ol>;
    },
    p({ children }) {
      return <p className="whitespace-pre-wrap leading-6">{children}</p>;
    },
    pre({ children }) {
      const language = /language-([\w+-]+)/.exec(children?.props?.className || "")?.[1];
      if (!language) return <CodeBlock inverse={inverse}>{children}</CodeBlock>;
      const plain = <CodeBlock inverse={inverse}>{children}</CodeBlock>;
      return (
        <Suspense fallback={plain}>
          <RichCodeBlock code={textFromReactNode(children).replace(/\n$/, "")} fallback={plain} Frame={CodeBlock} inverse={inverse} language={language} />
        </Suspense>
      );
    },
    table({ children }) {
      return (
        <table className="block max-w-full overflow-x-auto border-collapse text-left text-sm">
          {children}
        </table>
      );
    },
    td({ children }) {
      return <td className="border border-line px-3 py-2 align-top">{children}</td>;
    },
    th({ children }) {
      return <th className="border border-line bg-slate-50 px-3 py-2 font-semibold">{children}</th>;
    },
    ul({ children, className: listClassName = "" }) {
      return <ul className={`${listClassName} ml-6 list-disc space-y-1`}>{children}</ul>;
    },
  }), [handleLinkClick, inverse, sourcePath]);

  return (
    <div
      ref={rootRef}
      className={`min-w-0 break-words ${spacing} ${className}`}
    >
      <ReactMarkdown
        components={components}
        rehypePlugins={[rehypeSlug]}
        remarkPlugins={remarkPlugins}
        urlTransform={markdownUrlTransform}
      >
        {String(value ?? "")}
      </ReactMarkdown>
    </div>
  );
}

function CodeBlock({ children, copyText, inverse }) {
  const [copyState, setCopyState] = useState("idle");
  const resetTimerRef = useRef(null);

  useEffect(() => () => clearTimeout(resetTimerRef.current), []);

  async function copyCode() {
    try {
      await navigator.clipboard.writeText((copyText ?? textFromReactNode(children)).replace(/\n$/, ""));
      setCopyState("copied");
      clearTimeout(resetTimerRef.current);
      resetTimerRef.current = setTimeout(() => setCopyState("idle"), 2000);
    } catch {
      setCopyState("error");
    }
  }

  const copied = copyState === "copied";
  const label = copied
    ? "Copied"
    : copyState === "error"
      ? "Copy failed"
      : "Copy";

  return (
    <div
      className={`max-w-full overflow-hidden rounded-lg border ${
        inverse
          ? "border-white/20 bg-black/20 text-white"
          : "border-line bg-slate-50 text-slate-700 dark:bg-[#111113] dark:text-[#d4d4d4]"
      }`}
    >
      <pre className="workflow-scrollbar max-w-full overflow-auto px-3 py-2 font-mono text-[12px] leading-5 [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-inherit">
        {children}
      </pre>
      <div className={`flex justify-end border-t px-2 py-1.5 ${inverse ? "border-white/15" : "border-line"}`}>
        <button
          aria-label="Copy code to clipboard"
          className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[11px] font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-1 ${
            inverse
              ? "text-white/80 hover:bg-white/10 hover:text-white"
              : "text-muted hover:bg-slate-200/70 hover:text-ink dark:hover:bg-white/10"
          }`}
          title="Copy code to clipboard"
          type="button"
          onClick={copyCode}
        >
          {copied ? <Check aria-hidden="true" size={13} /> : <Copy aria-hidden="true" size={13} />}
          <span aria-live="polite">{label}</span>
        </button>
      </div>
    </div>
  );
}

function textFromReactNode(node) {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textFromReactNode).join("");
  return node?.props ? textFromReactNode(node.props.children) : "";
}

export function markdownUrlTransform(url, key) {
  if (key === "href" && isLocalFileLink(url)) return url;
  return defaultUrlTransform(url);
}

export function isExternalUrl(value) {
  return /^(?:https?:|mailto:)/i.test(value) || String(value ?? "").startsWith("//");
}

function websiteLinkHref(href) {
  return href.startsWith("//") ? `https:${href}` : href;
}

export default memo(MarkdownContent);
