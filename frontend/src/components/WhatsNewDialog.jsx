import { ExternalLink } from "lucide-react";
import { useMemo } from "react";
// Bundled at build time; this dialog loads on first use, so the text stays out of startup.
import changelog from "../../../CHANGELOG.md?raw";
import { openHelpLink } from "../lib/issueReport.js";
import { changelogSections, releasePageUrl } from "../lib/whatsNew.js";
import { Dialog } from "./Dialog.jsx";
import MarkdownContent from "./MarkdownContent.jsx";

// What's new: the CHANGELOG sections after `since` up to and including `version`.
export default function WhatsNewDialog({ since = "", version, onClose }) {
  const sections = useMemo(() => changelogSections(changelog, since, version), [since, version]);
  const hasRunning = sections.some((section) => section.version === version);
  const subtitle = since && since !== version ? `Changes since ${since}` : `Raticode ${version}`;
  return (
    <Dialog
      onClose={onClose}
      overlayClassName="fixed inset-0 z-[100] grid place-items-center ui-scrim px-4"
      panelClassName="ui-dialog flex max-h-[85vh] w-full max-w-2xl flex-col p-4"
      panelProps={{ "data-testid": "whats-new" }}
      title={`What's new in Raticode ${version}`}
    >
      <div className="shrink-0">
        <h2 className="text-[15px] font-semibold text-ink">What&apos;s new</h2>
        <p className="mt-0.5 text-[12px] text-muted">{subtitle}</p>
      </div>
      <div className="mt-3 min-h-0 flex-1 overflow-y-auto pr-1 text-[13px]">
        {hasRunning ? null : (
          <p className="mb-3 text-ink">The changelog in this copy of Raticode has no notes for {version}. The release page lists what changed.</p>
        )}
        {sections.length ? <MarkdownContent compact images={false} value={sections.map((section) => section.markdown).join("\n\n")} /> : null}
      </div>
      <div className="mt-4 flex shrink-0 items-center justify-between gap-2">
        <button type="button" className="ui-button" onClick={() => openHelpLink(releasePageUrl(version))}>
          <ExternalLink aria-hidden="true" size={13} />
          {`${version} release page`}
        </button>
        <button type="button" className="ui-button ui-button--primary" data-dialog-initial-focus onClick={onClose}>Close</button>
      </div>
    </Dialog>
  );
}
