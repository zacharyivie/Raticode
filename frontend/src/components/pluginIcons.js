import { Brain, Building2, Folder, GitBranch, Globe, Search, SquareKanban, Waypoints } from "lucide-react";
import RatFaceIcon from "./RatFaceIcon.jsx";
import RatSwarmIcon from "./RatSwarmIcon.jsx";

export const PLUGIN_ICONS = Object.freeze({
  rem: RatFaceIcon,
  workflows: Waypoints,
  files: Folder,
  browser: Globe,
  search: Search,
  "source-control": GitBranch,
  kanban: SquareKanban,
  swarms: RatSwarmIcon,
  organizations: Building2,
  "second-brain": Brain,
});
