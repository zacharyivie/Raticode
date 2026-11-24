import { useMemo, useState } from "react";
import { Folder } from "lucide-react";
import { FOLDER_RULES, folderTree, ruleForFolder } from "../lib/secondBrain.js";
import { useSecondBrain, setFolderRule, notify } from "../lib/secondBrainStore.js";

const FIND_AFTER = 12;
const SAVED = {
  normal: folder => `${folder} is searched normally.`,
  "list-after": folder => `${folder} is listed after other results.`,
  "leave-out": folder => `${folder} is left out of search. Its files stay in the folder.`,
};

// Brain settings › Folders: one search rule per folder. A rule covers its subfolders unless
// they have their own. Rules are saved in the brain's settings; files are never touched.
export function FolderRules({ brain }) {
  const store = useSecondBrain();
  const rules = useMemo(() => brain.settings.folderRules || {}, [brain.settings.folderRules]);
  const folders = useMemo(() => folderTree(store.documents.filter(doc => doc.brainId === brain.id), rules), [store.documents, brain.id, rules]);
  const [find, setFind] = useState("");
  const shown = find.trim() ? folders.filter(folder => folder.path.toLowerCase().includes(find.trim().toLowerCase())) : folders;
  const counts = Object.values(rules).reduce((total, rule) => ({ ...total, [rule]: (total[rule] || 0) + 1 }), {});
  return <section aria-labelledby="sb-folder-rules-heading"><h4 id="sb-folder-rules-heading">Folders</h4>
    <p className="sb-hint">Choose how search treats each folder. <strong>List after other results</strong> keeps a folder searchable but ranks it below everything else. <strong>Leave out of search</strong> stops indexing its files and keeps them from Rem; the Library still lists them under More filters › Left out. A rule covers subfolders unless they have their own. Rules are stored with the brain’s library metadata and travel with the folder and its packages. Files are never moved or changed.</p>
    {counts["leave-out"] || counts["list-after"] ? <p className="sb-hint" role="status">{[counts["leave-out"] ? `${counts["leave-out"]} left out` : "", counts["list-after"] ? `${counts["list-after"]} listed after other results` : ""].filter(Boolean).join(" · ")}</p> : null}
    {folders.length > FIND_AFTER ? <input className="sb-popover-search" aria-label="Find a folder" placeholder="Find a folder" value={find} onChange={event => setFind(event.target.value)} /> : null}
    {shown.length ? <ul className="sb-folder-rules">{shown.map(folder => {
      const current = ruleForFolder(folder.path, rules);
      const rule = current?.rule || "normal";
      const inherited = current && current.folder !== folder.path ? current.folder : "";
      return <li key={folder.path} data-rule={rule} style={{ "--depth": find.trim() ? 0 : folder.depth }}>
        <Folder size={14} aria-hidden="true" />
        <span className="sb-folder-rule-name"><strong title={folder.path}>{find.trim() ? folder.path : folder.name}</strong><small>{folder.count} file{folder.count === 1 ? "" : "s"}{inherited ? ` · from ${inherited}` : ""}</small></span>
        <select aria-label={`Search rule for ${folder.path}`} value={rule} onChange={event => { setFolderRule(brain.id, folder.path, event.target.value); notify(SAVED[event.target.value](folder.path)); }}>{FOLDER_RULES.map(([id, text]) => <option key={id} value={id}>{text}</option>)}</select>
      </li>;
    })}</ul> : <p className="sb-hint">{folders.length ? "No folder matches." : "This brain has no folders yet."}</p>}
  </section>;
}
