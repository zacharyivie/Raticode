import { Brain, Building2, Folder, GitBranch, Search, Waypoints } from "lucide-react";
import RatFaceIcon from "./RatFaceIcon.jsx";
import RatSwarmIcon from "./RatSwarmIcon.jsx";

export const PLUGIN_ICONS = Object.freeze({
  rem: RatFaceIcon,
  workflows: Waypoints,
  files: Folder,
  search: Search,
  "source-control": GitBranch,
  swarms: RatSwarmIcon,
  organizations: Building2,
  "second-brain": Brain,
});
