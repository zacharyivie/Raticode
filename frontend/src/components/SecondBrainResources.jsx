import { useEffect } from "react";
import { Brain } from "lucide-react";
import { brainUsable, disabledBrainIds, selectThreadBrains } from "../lib/remResources.js";
import { ensureLibrary, useSecondBrain } from "../lib/secondBrainStore.js";
import { ToolRow, ToolSection } from "./RemToolRows.jsx";

// Thread-level choice of brains Rem may search; independent of the brain being browsed.
// Brains start on, and the thread records the ones turned off.
export default function SecondBrainResources({ resources, legacyRoot, onChange }) {
  const { brains } = useSecondBrain();
  useEffect(() => { void ensureLibrary(); }, []);
  const off = disabledBrainIds(resources, brains);
  const isOn = (brain) => brainUsable(brain, legacyRoot) && !off.includes(brain.id);
  const { served } = selectThreadBrains(resources, brains, { enabled: true, root: legacyRoot });
  const detail = (brain) => {
    if (brain.state !== "available") return "Unavailable";
    if (!brainUsable(brain, legacyRoot)) return "Allow cloud processing in brain settings to use it with Rem";
    // The Memory folder without cloud permission cannot join registered brains.
    if (isOn(brain) && !served.includes(brain.id)) return "Skipped while other brains are on. Allow cloud processing to use them together";
    return "Search and cite notes";
  };
  return brains.length ? <ToolSection title="Second Brains" summary={`${served.length} on`}>
    {brains.map((brain) => <ToolRow
      key={brain.id}
      icon={Brain}
      title={brain.name}
      detail={detail(brain)}
      checked={isOn(brain)}
      disabled={!brainUsable(brain, legacyRoot)}
      onChange={(on) => onChange(on ? off.filter((id) => id !== brain.id) : [...off, brain.id])}
    />)}
  </ToolSection> : null;
}
