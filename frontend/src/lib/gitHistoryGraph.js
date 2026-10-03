// Distinct blue, amber, magenta and teal lanes with a darker light-theme palette.
export const GRAPH_COLORS = Array.from({ length: 4 }, (_, index) => `var(--scm-lane-${index})`);
export const GRAPH_PATTERNS = [undefined, "7 3", "2 3", "9 3 2 3"];
export const GRAPH_LANE_SPACING = 16;
export const GRAPH_LANE_INSET = 8;

export function commitLabels(commit) {
  if (commit.labels) return commit.labels;
  return String(commit.refs || "").split(", ").filter(Boolean).map(ref => {
    const current = ref.startsWith("HEAD -> ");
    const tag = ref.startsWith("tag: ");
    const name = ref.replace(/^(HEAD -> |tag: )/, "");
    return { id: ref, name, current, type: tag ? "tag" : name.includes("/") ? "remote" : "local" };
  });
}

// Project edges through hidden commits so search cannot imply a false branch split.
export function filterHistory(commits, query = "") {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return commits;
  const byHash = new Map(commits.map(commit => [commit.hash, commit]));
  const visible = new Set(commits.filter(commit => {
    const text = [commit.hash, commit.subject, commit.message, commit.author, ...commitLabels(commit).map(ref => ref.name)].join(" ").toLowerCase();
    return words.every(word => text.includes(word));
  }).map(commit => commit.hash));
  return commits.filter(commit => visible.has(commit.hash)).map(commit => {
    const parents = new Set();
    const pending = [...(commit.parents || [])].reverse();
    const visited = new Set();
    while (pending.length) {
      const hash = pending.pop();
      if (visited.has(hash)) continue;
      visited.add(hash);
      if (visible.has(hash) || !byHash.has(hash)) parents.add(hash);
      else pending.push(...[...(byHash.get(hash).parents || [])].reverse());
    }
    return { ...commit, parents: [...parents] };
  });
}

export function layoutHistoryGraph(commits) {
  let lanes = [];
  let nextColor = 0;
  let width = 1;
  const allocate = (hash, color) => {
    let lane = lanes.findIndex(item => !item);
    if (lane < 0) lane = lanes.length;
    lanes[lane] = { hash, color: color || GRAPH_COLORS[nextColor++ % GRAPH_COLORS.length] };
    return lane;
  };
  const rows = commits.map(commit => {
    let lane = lanes.findIndex(item => item?.hash === commit.hash);
    const starts = lane < 0;
    if (starts) lane = allocate(commit.hash);
    const top = lanes.map(item => item && { ...item });
    const color = lanes[lane].color;
    lanes[lane] = null;
    const parents = [...new Set(commit.parents || [])];
    const targets = parents.map((hash, index) => {
      let target = lanes.findIndex(item => item?.hash === hash);
      if (target < 0) target = allocate(hash, index === 0 ? color : undefined);
      return { lane: target, color: index === 0 ? color : lanes[target].color };
    });
    const segments = top.flatMap((item, index) => {
      if (!item || index === lane) return [];
      const target = lanes.findIndex(next => next?.hash === item.hash);
      return target < 0 ? [] : [{ from: index, to: target, color: item.color, full: true }];
    });
    if (!starts) segments.push({ from: lane, to: lane, color, top: true });
    segments.push(...targets.map(target => ({ from: lane, to: target.lane, color: target.color })));
    width = Math.max(width, top.length, lanes.length);
    while (lanes.length && !lanes.at(-1)) lanes.pop();
    return { commit, lane, color, segments };
  });
  return { rows, width: (width - 1) * GRAPH_LANE_SPACING + GRAPH_LANE_INSET * 2 };
}
