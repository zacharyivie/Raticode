"""Projects, provenance, connections and topic groups for a Second Brain.

Everything here keeps two kinds of relationship apart:

- Evidence: something a file says (a link, front matter, a folder) or something
  Raticode saw happen (a Rem run saved a report, a thread read a note, an
  organization ticket read or quoted it). These are facts and say where they
  came from.
- Suggestions: similarity between documents, from local embeddings when the
  brain has related-meaning search on, or from shared distinctive words when it
  doesn't. Suggestions are recomputed and always labelled as such.

All processing is local. Nothing here calls a model or a network service.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

WINDOW_MIN_WORDS = 180
WINDOW_MAX_WORDS = 380
SIMILAR_LIMIT = 6
TOPIC_NEIGHBOURS = 6
MIN_TOPIC_SIZE = 3
MEANING_THRESHOLD = 0.62
WORDS_THRESHOLD = 0.18
REPORT_CONTAINERS = {"reports", "rem"}
LABEL_NOISE = {"src", "frontend", "backend", "jsx", "tsx", "json", "py", "test", "tests", "passed"}

STOPWORDS = set(
    """
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing done down during each either
else even ever every few for from further get gets got had has have having he her here
hers him his how however i if in into is it its itself just less like made make many may
me might more most much must my near need new no nor not now of off often on once one
only or other our ours out over own per rather same see she should since so some still
such than that the their theirs them then there these they this those though through to
too under until up upon us use used uses using very via was way we well were what when
where whether which while who whom whose why will with within without would yet you your
yours also each etc into onto shall per across along around because two three four five
six seven eight nine ten first second third last next new old today yesterday tomorrow
html http https www com org md file files page pages report reports note notes document
documents section sections thing things item items use done line lines time times day days
""".split()
)


# ----------------------------------------------------------------- projects


def project_key(name: str) -> str:
    """Compare project names without caring about case, spaces or punctuation."""
    return re.sub(r"[^0-9a-z]+", "", str(name).casefold())


@functools.lru_cache(maxsize=512)
def project_root(path: str) -> str:
    """A git worktree belongs to the repository it was created from."""
    text = str(path).rstrip("/")
    match = re.match(r"(.+?)/\.worktrees/[^/]+", text)
    if match:
        return match.group(1)
    try:
        # A linked worktree's .git is a file: "gitdir: <repo>/.git/worktrees/<name>".
        pointer = Path(text) / ".git"
        if pointer.is_file() and pointer.stat().st_size < 4096:
            found = re.match(r"gitdir:\s*(.+?)/\.git/worktrees/[^/]+\s*$", pointer.read_text())
            if found:
                return found.group(1).rstrip("/")
    except OSError:
        pass
    return text


def root_name(root: str) -> str:
    """A readable project name for a folder: /Raticode Mobile/raticode-mobile -> Raticode Mobile."""
    path = Path(project_root(root))
    name = path.name or str(path)
    parent = path.parent.name
    if parent and project_key(parent) == project_key(name):
        return parent
    return name


def folder_project(relative: str) -> str:
    """The project folder named by a report path, such as Reports/<Project>/file.html."""
    parts = relative.split("/")[:-1]
    for index, part in enumerate(parts):
        if part.casefold() != "reports":
            continue
        rest = parts[index + 1 :]
        while rest and rest[0].casefold() in REPORT_CONTAINERS:
            rest = rest[1:]
        if rest:
            return rest[0]
    return ""


def _usable_root(root: str, brain_root: str) -> bool:
    if not root:
        return False
    resolved = project_root(root)
    home = str(Path.home())
    return resolved not in {brain_root.rstrip("/"), home, "/"}


class ProjectIndex:
    """Names, aliases and folders that identify each project."""

    def __init__(self, brain_root: str) -> None:
        self.brain_root = brain_root.rstrip("/")
        self.projects: dict[str, dict[str, Any]] = {}
        self.by_key: dict[str, str] = {}
        self.by_root: dict[str, str] = {}

    def add_saved(self, item: dict[str, Any]) -> None:
        project = {
            "id": item["id"],
            "name": item["name"],
            "aliases": list(item.get("aliases", [])),
            "roots": [project_root(root) for root in item.get("roots", [])],
            "color": item.get("color", ""),
            "source": "saved",
            "saved": True,
        }
        self.projects[project["id"]] = project
        for name in [project["name"], *project["aliases"]]:
            if project_key(name):
                self.by_key.setdefault(project_key(name), project["id"])
        for root in project["roots"]:
            self.by_root[root] = project["id"]

    def for_name(self, name: str, source: str) -> str:
        key = project_key(name)
        if not key:
            return ""
        if key in self.by_key:
            return self.by_key[key]
        pid = f"{source}:{key}"
        self.projects[pid] = {
            "id": pid,
            "name": name.strip(),
            "aliases": [],
            "roots": [],
            "color": "",
            "source": source,
            "saved": False,
        }
        self.by_key[key] = pid
        return pid

    def for_root(self, root: str) -> str:
        if not _usable_root(root, self.brain_root):
            return ""
        resolved = project_root(root)
        if resolved in self.by_root:
            return self.by_root[resolved]
        pid = self.for_name(root_name(resolved), "root")
        if pid:
            self.by_root[resolved] = pid
            roots = self.projects[pid]["roots"]
            if resolved not in roots:
                roots.append(resolved)
        return pid


def provenance_for(report: dict[str, Any]) -> dict[str, Any]:
    return dict(report.get("provenance") or {})


def resolve_projects(
    brain: dict[str, Any],
    documents: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    saved: list[dict[str, Any]],
    conversations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assign each document to at most one project and say why.

    Priority: a project you chose, what Raticode observed when the file was saved,
    what the file says about itself, then the folder it sits in.
    """
    index = ProjectIndex(brain["root"])
    for item in saved:
        index.add_saved(item)
    by_path = {report["artifactPath"]: report for report in reports}
    live = [doc for doc in documents if not doc.get("missing")]
    # Folder names come first, so aliases and roots can join an existing folder project.
    for doc in live:
        if name := folder_project(doc["path"]):
            index.for_name(name, "folder")
    assignments: dict[str, dict[str, Any]] = {}
    for doc in live:
        metadata = doc.get("metadata") or {}
        chosen = str(metadata.get("project") or "").strip()
        written = str(doc.get("fileProject", doc.get("project", "")) or "").strip()
        report = by_path.get(doc["path"])
        provenance = provenance_for(report) if report else {}
        candidates: list[tuple[str, str, str]] = []
        if chosen and chosen != written:
            candidates.append((index.for_name(chosen, "named"), "you", "You set the project"))
        if provenance.get("projectRoot"):
            pid = index.for_root(str(provenance["projectRoot"]))
            label = provenance.get("taskTitle") or provenance.get("threadTitle") or ""
            via = {
                "rem_task": "Saved by a Rem task",
                "rem_thread": "Saved from a Rem thread",
                "conversation": "Linked from a Rem conversation",
            }.get(str(provenance.get("savedVia")), "Saved by Rem")
            candidates.append(
                (
                    pid,
                    "observed",
                    f"{via}{f' · {label}' if label else ''} in {provenance['projectRoot']}",
                )
            )
        if written:
            candidates.append((index.for_name(written, "named"), "written", "The file names it"))
        folder = folder_project(doc["path"])
        if folder:
            candidates.append((index.for_name(folder, "folder"), "folder", f"In {folder}/"))
        else:
            for part in doc["path"].split("/")[:-1]:
                if project_key(part) in index.by_key:
                    candidates.append((index.by_key[project_key(part)], "folder", f"In {part}/"))
                    break
        candidates = [item for item in candidates if item[0]]
        if candidates:
            pid, basis, detail = candidates[0]
            assignments[doc["id"]] = {
                "projectId": pid,
                "basis": basis,
                "detail": detail,
                "others": sorted({c[0] for c in candidates[1:] if c[0] != pid}),
            }
    threads = []
    for thread in conversations:
        pid = index.for_root(str(thread.get("projectRoot") or ""))
        threads.append({**thread, "projectId": pid})
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    report_docs = {report["artifactPath"] for report in reports}
    for doc in live:
        assignment = assignments.get(doc["id"])
        if not assignment:
            continue
        kind = (
            "reports" if doc["path"] in report_docs or doc.get("type") == "report" else "knowledge"
        )
        if kind == "knowledge" and folder_project(doc["path"]):
            # Files saved under a Reports folder are reports, even without a record.
            kind = "reports"
        assignment["kind"] = kind
        counts[assignment["projectId"]][kind] += 1
    for thread in threads:
        if thread["projectId"]:
            counts[thread["projectId"]]["conversations"] += 1
    projects = []
    for project in index.projects.values():
        tally = counts.get(project["id"], Counter())
        if not project["saved"] and not sum(tally.values()):
            continue
        projects.append(
            {
                **project,
                "counts": {
                    "reports": tally["reports"],
                    "knowledge": tally["knowledge"],
                    "conversations": tally["conversations"],
                },
            }
        )
    projects.sort(key=lambda item: (-sum(item["counts"].values()), item["name"].casefold()))
    assigned = set(assignments)
    unassigned = [
        doc["id"] for doc in live if doc["id"] not in assigned and not doc.get("excluded")
    ]
    return {
        "projects": projects,
        "assignments": assignments,
        "conversations": threads,
        "unassigned": unassigned,
        "suggestedMerges": suggested_merges(projects),
    }


def suggested_merges(projects: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Projects whose names look like the same thing; the user decides."""
    result = []
    for left in projects:
        for right in projects:
            if left["id"] >= right["id"]:
                continue
            a, b = project_key(left["name"]), project_key(right["name"])
            if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
                result.append({"from": left["id"], "into": right["id"]})
    return result[:20]


# --------------------------------------------------------------- conversations


def conversation_archives(root: Path) -> list[Path]:
    """Rem conversation archives stored inside the brain."""
    found = []
    try:
        for child in root.iterdir():
            if (
                child.is_dir()
                and not child.is_symlink()
                and (child / "README.raticode.md").is_file()
                and (child / "index.json").is_file()
            ):
                found.append(child)
    except OSError:
        return []
    return found[:4]


def conversations(root: Path) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    for archive in conversation_archives(root):
        try:
            index = json.loads((archive / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in (index.get("threads") or {}).values():
            if not isinstance(entry, dict) or entry.get("deleted"):
                continue
            threads.append(
                {
                    "threadId": str(entry.get("threadId") or ""),
                    "title": str(entry.get("title") or "Untitled thread")[:200],
                    "projectRoot": str(entry.get("projectRoot") or ""),
                    "updatedAt": str(entry.get("updatedAt") or ""),
                    "messageCount": int(entry.get("messageCount") or 0),
                    "snapshot": str(archive.relative_to(root) / str(entry.get("snapshot") or "")),
                }
            )
    threads.sort(key=lambda item: str(item["updatedAt"]), reverse=True)
    return threads


# --------------------------------------------------------------- provenance


def receipts(data_dir: Path) -> list[dict[str, Any]]:
    """Saved-report receipts from Rem's run store, read without taking a write lock."""
    database = data_dir / "rem-home.sqlite3"
    if not database.is_file():
        return []
    found = []
    try:
        db = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        titles = {}
        for row in db.execute("SELECT id, body FROM tasks"):
            try:
                titles[row[0]] = str(json.loads(row[1]).get("title") or "")
            except ValueError:
                continue
        for row in db.execute("SELECT body FROM runs"):
            try:
                run = json.loads(row[0])
            except ValueError:
                continue
            for artifact in run.get("artifacts") or []:
                if artifact.get("superseded") or not artifact.get("path"):
                    continue
                recipe, config = run.get("recipe") or {}, run.get("config") or {}
                found.append(
                    {
                        "path": str(artifact["path"]),
                        "sha256": str(artifact.get("sha256") or ""),
                        "runId": str(run.get("id") or ""),
                        "taskId": str(run.get("task_id") or ""),
                        "threadId": str(run.get("thread_id") or ""),
                        "taskTitle": str(
                            recipe.get("title") or titles.get(run.get("task_id")) or ""
                        ),
                        "projectRoot": str(recipe.get("project_dir") or ""),
                        "provider": str(config.get("provider") or ""),
                        "model": str(config.get("model") or ""),
                        "at": str(run.get("finished_at") or run.get("started_at") or ""),
                    }
                )
    except sqlite3.Error:
        return found
    finally:
        db.close()
    return found


def conversation_mentions(root: Path, paths: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Find the Rem thread whose conversation links each report, by absolute path."""
    wanted = {str(root / path): path for path in paths}
    if not wanted:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for archive in conversation_archives(root):
        try:
            index = json.loads((archive / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = sorted(
            (e for e in (index.get("threads") or {}).values() if isinstance(e, dict)),
            key=lambda e: str(e.get("updatedAt") or ""),
        )
        for entry in entries:
            snapshot = archive / str(entry.get("snapshot") or "")
            try:
                if not snapshot.is_file() or snapshot.stat().st_size > 32 * 1024 * 1024:
                    continue
                text = snapshot.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for absolute, relative in wanted.items():
                if relative not in found and absolute in text:
                    found[relative] = {
                        "threadId": str(entry.get("threadId") or ""),
                        "threadTitle": str(entry.get("title") or "")[:200],
                        "projectRoot": str(entry.get("projectRoot") or ""),
                        "at": str(entry.get("updatedAt") or ""),
                    }
    return found


# ------------------------------------------------------------------ vectors


def windows(doc: dict[str, Any]) -> list[tuple[str, list[str], str]]:
    """Group short passages into windows of a few hundred words for embedding.

    Headings, table cells and labels make poor embeddings on their own. Each window
    maps back to the original passages, so citations still point at real blocks.
    """
    result: list[tuple[str, list[str], str]] = []
    blocks: list[str] = []
    parts: list[str] = []
    words = 0

    def flush() -> None:
        nonlocal blocks, parts, words
        if blocks:
            text = f"{doc['title']}\n" + "\n".join(parts)
            result.append((f"w{len(result)}-{blocks[0]}", blocks, text))
        blocks, parts, words = [], [], 0

    for block in doc.get("blocks", []):
        text = block["text"].strip()
        if not text:
            continue
        count = len(text.split())
        if words and words + count > WINDOW_MAX_WORDS:
            flush()
        blocks.append(block["id"])
        parts.append(text)
        words += count
        if words >= WINDOW_MIN_WORDS:
            flush()
    flush()
    return result


def pack(vector: Any) -> bytes:
    import numpy as np

    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("The embedding model returned an empty vector")
    return bytes((array / norm).astype(np.float32).tobytes())


def tokens(text: str) -> list[str]:
    return [
        word
        for word in re.findall(r"[a-z][a-z0-9-]{2,}", text.casefold())
        if word not in STOPWORDS and not word.strip("-").isdigit()
    ]


def word_vectors(documents: list[dict[str, Any]], dims: int = 1024) -> tuple[list[str], Any]:
    """Hashed TF-IDF document vectors: a local, model-free stand-in for meaning."""
    import numpy as np

    counts = []
    df: Counter[str] = Counter()
    for doc in documents:
        text = " ".join([doc["title"]] * 3 + [block["text"] for block in doc.get("blocks", [])])
        tally = Counter(tokens(text[:400_000]))
        counts.append(tally)
        df.update(tally.keys())
    total = max(1, len(documents))
    matrix = np.zeros((len(documents), dims), dtype=np.float32)
    for row, tally in enumerate(counts):
        for word, count in tally.items():
            frequency = df[word]
            if frequency < 2 or frequency > 0.5 * total:
                continue
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            column = int.from_bytes(digest[:4], "little") % dims
            sign = 1.0 if digest[4] & 1 else -1.0
            matrix[row, column] += sign * (1 + math.log(count)) * math.log(total / frequency)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return [doc["id"] for doc in documents], matrix / norms


def revision_stem(path: str) -> str:
    stem = Path(path).stem
    return re.sub(r"(?:[-_ ]v\d+|[-_ ]copy|\s\(\d+\))$", "", stem, flags=re.I)


def similar(
    doc_id: str,
    ids: list[str],
    matrix: Any,
    paths: dict[str, str],
    threshold: float,
    limit: int = SIMILAR_LIMIT,
) -> list[dict[str, Any]]:
    """Nearest documents, with revisions of one report collapsed into a single entry."""
    import numpy as np

    if doc_id not in ids:
        return []
    row = ids.index(doc_id)
    scores = matrix @ matrix[row]
    order = np.argsort(-scores)
    own = revision_stem(paths.get(doc_id, ""))
    seen: set[str] = set()
    result = []
    for position in order[: limit * 6 + 1]:
        other = ids[int(position)]
        score = float(scores[int(position)])
        if other == doc_id or score < threshold:
            continue
        stem = revision_stem(paths.get(other, ""))
        if stem == own and Path(paths.get(other, "")).parent == Path(paths.get(doc_id, "")).parent:
            result.append({"docId": other, "score": round(score, 3), "revision": True})
            continue
        if stem in seen:
            continue
        seen.add(stem)
        result.append({"docId": other, "score": round(score, 3), "revision": False})
        if sum(not item["revision"] for item in result) >= limit:
            break
    return result


# ------------------------------------------------------------------- topics


def nearest_pairs(matrix: Any, k: int, threshold: float) -> list[tuple[int, int, float]]:
    """Each row's k most similar rows above a threshold, in bounded-memory chunks."""
    import numpy as np

    count = len(matrix)
    if count < 2:
        return []
    k = min(k, count - 1)
    pairs = []
    for start in range(0, count, 1024):
        scores = matrix[start : start + 1024] @ matrix.T
        for offset in range(len(scores)):
            scores[offset, start + offset] = -1
        top = np.argpartition(-scores, k - 1, axis=1)[:, :k]
        for offset, columns in enumerate(top):
            for column in columns:
                weight = float(scores[offset, column])
                if weight >= threshold:
                    pairs.append((start + offset, int(column), weight))
    return pairs


def _label(cluster_tokens: list[Counter[str]], all_tokens: Counter[str]) -> list[str]:
    """Class-based TF-IDF: words frequent in this group and rare elsewhere."""
    combined: Counter[str] = Counter()
    for tally in cluster_tokens:
        combined.update(tally)
    classes = max(1, len(cluster_tokens))
    average = sum(all_tokens.values()) / max(1, classes)
    scored = []
    for word, count in combined.items():
        # Labels skip dated file names and code paths; they make poor topic names.
        if count < 2 or any(char.isdigit() for char in word) or word in LABEL_NOISE:
            continue
        scored.append((count * math.log(1 + average / max(1, all_tokens[word])), word))
    scored.sort(reverse=True)
    picked: list[str] = []
    for _, word in scored:
        if any(word in other or other in word for other in picked):
            continue
        picked.append(word)
        if len(picked) == 3:
            break
    return picked


def compute_topics(
    documents: list[dict[str, Any]],
    ids: list[str],
    matrix: Any,
    threshold: float,
    previous: list[dict[str, Any]],
    named: list[dict[str, Any]],
) -> dict[str, Any]:
    """Group documents by similarity and by their written links.

    NetworkX's Leiden function only dispatches to optional backends, so this uses its
    built-in Louvain method and then splits any community that isn't connected,
    which is the defect Leiden was designed to remove.
    """
    import networkx as nx

    by_id = {doc["id"]: doc for doc in documents}
    graph = nx.Graph()
    graph.add_nodes_from(ids)
    for row, column, weight in nearest_pairs(matrix, TOPIC_NEIGHBOURS, threshold):
        a, b = ids[row], ids[column]
        current = graph.get_edge_data(a, b, {}).get("weight", 0.0)
        graph.add_edge(a, b, weight=max(current, weight), similar=True)
    for doc in documents:
        for target in doc.get("cites", []):
            if target in by_id and target != doc["id"]:
                data = graph.get_edge_data(doc["id"], target, {})
                graph.add_edge(
                    doc["id"],
                    target,
                    weight=data.get("weight", 0.0) + 1.0,
                    similar=data.get("similar", False),
                    written=True,
                )
    communities = nx.community.louvain_communities(graph, weight="weight", seed=7, resolution=1.0)
    groups: list[set[str]] = []
    for community in communities:
        for component in nx.connected_components(graph.subgraph(community)):
            groups.append(set(component))
    clusters = [group for group in groups if len(group) >= MIN_TOPIC_SIZE]
    loose = sorted(
        set().union(*[g for g in groups if len(g) < MIN_TOPIC_SIZE]) if groups else set()
    )
    token_cache = {
        doc_id: Counter(
            tokens(
                " ".join(
                    [by_id[doc_id]["title"]] * 3
                    + [block["text"] for block in by_id[doc_id].get("blocks", [])[:400]]
                )
            )
        )
        for doc_id in ids
    }
    all_tokens: Counter[str] = Counter()
    for cluster in clusters:
        merged: Counter[str] = Counter()
        for doc_id in cluster:
            merged.update(set(token_cache[doc_id]))
        all_tokens.update(merged)
    topics: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    def match(members: set[str], pool: list[dict[str, Any]]) -> dict[str, Any] | None:
        best, best_score = None, 0.3
        for item in pool:
            if item["id"] in used_ids:
                continue
            others = set(item.get("members", []))
            score = len(members & others) / max(1, len(members | others))
            if score > best_score:
                best, best_score = item, score
        return best

    for cluster in sorted(clusters, key=len, reverse=True):
        sub = graph.subgraph(cluster)
        central = max(cluster, key=lambda node: (sub.degree(node, weight="weight"), node))
        words = _label([token_cache[d] for d in cluster], all_tokens)
        pinned = match(cluster, named)
        prior = pinned or match(cluster, previous)
        topic_id = (
            prior["id"]
            if prior
            else "topic-" + hashlib.sha1("\n".join(sorted(cluster)).encode()).hexdigest()[:10]
        )
        used_ids.add(topic_id)
        topics.append(
            {
                "id": topic_id,
                "name": pinned["name"] if pinned else "",
                "named": bool(pinned),
                "terms": words,
                "label": pinned["name"] if pinned else " · ".join(words) or by_id[central]["title"],
                "central": central,
                "members": sorted(cluster),
                "size": len(cluster),
            }
        )
    topic_of: dict[str, str] = {
        doc_id: str(topic["id"]) for topic in topics for doc_id in topic["members"]
    }
    between: dict[tuple[str, str], dict[str, float]] = {}
    for a, b, data in graph.edges(data=True):
        ta, tb = topic_of.get(a), topic_of.get(b)
        if not ta or not tb or ta == tb:
            continue
        key = (ta, tb) if ta < tb else (tb, ta)
        entry = between.setdefault(key, {"weight": 0.0, "written": 0, "similar": 0})
        entry["weight"] += float(data.get("weight", 0.0))
        entry["written"] += 1 if data.get("written") else 0
        entry["similar"] += 1 if data.get("similar") else 0
    edges = [
        {"a": a, "b": b, **{k: round(v, 3) for k, v in data.items()}}
        for (a, b), data in sorted(between.items())
    ]
    layout_graph = nx.Graph()
    layout_graph.add_nodes_from(topic["id"] for topic in topics)
    for edge in edges:
        layout_graph.add_edge(edge["a"], edge["b"], weight=edge["weight"])
    positions = (
        nx.spring_layout(layout_graph, seed=7, weight="weight", k=1.6 / math.sqrt(len(topics)))
        if len(topics) > 1
        else {topic["id"]: (0.0, 0.0) for topic in topics}
    )
    for topic in topics:
        x, y = positions[topic["id"]]
        topic["x"], topic["y"] = round(float(x), 4), round(float(y), 4)
    return {"topics": topics, "edges": edges, "loose": loose, "topicOf": topic_of}


def topic_layout(
    members: list[str],
    ids: list[str],
    matrix: Any,
    documents: dict[str, dict[str, Any]],
    limit: int = 120,
) -> dict[str, Any]:
    """Positions and links for the documents inside one topic.

    Large topics show their most connected documents; the list view shows them all.
    """
    import networkx as nx
    import numpy as np

    position = {doc_id: row for row, doc_id in enumerate(ids)}
    rows = [doc_id for doc_id in members if doc_id in position]
    sub = matrix[[position[doc_id] for doc_id in rows]] if rows else matrix[:0]
    scores = sub @ sub.T if rows else np.zeros((0, 0))
    if len(rows) > limit:
        strength = scores.sum(axis=1)
        keep = sorted(np.argsort(-strength)[:limit].tolist())
        rows = [rows[i] for i in keep]
        scores = scores[np.ix_(keep, keep)]
    graph = nx.Graph()
    graph.add_nodes_from(rows)
    edges = []
    for i, a in enumerate(rows):
        order = [int(j) for j in np.argsort(-scores[i]) if int(j) != i][:3]
        for j in order:
            b = rows[j]
            if graph.has_edge(a, b):
                continue
            weight = max(float(scores[i, j]), 0.01)
            graph.add_edge(a, b, weight=weight)
            edges.append({"a": a, "b": b, "kind": "similar", "score": round(weight, 3)})
    kept = set(rows)
    for a in rows:
        for b in documents[a].get("cites", []):
            if b in kept and b != a:
                graph.add_edge(a, b, weight=1.0)
                edges.append({"a": a, "b": b, "kind": "written"})
    positions = (
        nx.spring_layout(graph, seed=7, weight="weight")
        if len(rows) > 1
        else {doc_id: (0.0, 0.0) for doc_id in rows}
    )
    return {
        "nodes": [
            {"docId": doc_id, "x": round(float(x), 4), "y": round(float(y), 4)}
            for doc_id, (x, y) in positions.items()
        ],
        "edges": edges,
        "shown": len(rows),
        "total": len(members),
    }


# ------------------------------------------------------- organizations bridge


def organization_references(organizations: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """Organization knowledge that comes from or was copied into this brain.

    Returns, per brain-relative path, the tickets that read the note (observed) and the
    claims that quote it (asserted, with Raticode's current check of the quote), plus
    the organization that wrote a mirrored note.
    """
    from gofer.ui.organization_knowledge import claim_state, ticket_key

    used: dict[str, list[dict[str, Any]]] = defaultdict(list)
    made: dict[str, dict[str, Any]] = {}
    resolved_root = root.resolve()
    for org in organizations:
        config, runtime = org.get("config") or {}, org.get("runtime") or {}
        tasks = {task["id"]: task for task in runtime.get("tasks", [])}
        entry_paths: dict[str, str] = {}
        for entry in (runtime.get("knowledge") or {}).values():
            for field, relation in (("source", "imported"), ("mirror", "mirrored")):
                value = (entry.get(field) or {}).get("path")
                if not value:
                    continue
                try:
                    relative = Path(value).resolve().relative_to(resolved_root).as_posix()
                except (OSError, ValueError):
                    continue
                entry_paths[entry["id"]] = relative
                if relation == "mirrored":
                    task = tasks.get(entry.get("taskId") or "")
                    made[relative] = {
                        "organization": config.get("name", ""),
                        "organizationId": org["id"],
                        "entryId": entry["id"],
                        "author": entry.get("author", ""),
                        "ticket": ticket_key(config, task) if task else "",
                        "at": entry.get("createdAt", ""),
                    }
        if not entry_paths:
            continue
        for link in (runtime.get("links") or {}).values():
            if link.get("removed") or not str(link.get("from", "")).startswith("ticket:"):
                continue
            target = str(link.get("to", "")).removeprefix("kb:")
            task = tasks.get(str(link["from"]).removeprefix("ticket:"))
            if target in entry_paths and task:
                used[entry_paths[target]].append(
                    {
                        "kind": "ticket",
                        "basis": "observed" if link.get("kind") == "read" else "asserted",
                        "relation": "read" if link.get("kind") == "read" else "linked",
                        "organization": config.get("name", ""),
                        "organizationId": org["id"],
                        "taskId": task["id"],
                        "ticket": ticket_key(config, task),
                        "title": task.get("title", ""),
                        "at": link.get("at", ""),
                    }
                )
        for claim in (runtime.get("claims") or {}).values():
            evidence = claim.get("evidence") or {}
            if evidence.get("kind") != "quote" or evidence.get("entryId") not in entry_paths:
                continue
            task = tasks.get(claim.get("taskId") or "")
            state = claim_state(org, claim)
            used[entry_paths[evidence["entryId"]]].append(
                {
                    "kind": "claim",
                    "basis": "asserted",
                    "relation": "quoted",
                    "organization": config.get("name", ""),
                    "organizationId": org["id"],
                    "taskId": claim.get("taskId", ""),
                    "ticket": ticket_key(config, task) if task else "",
                    "title": claim.get("text", ""),
                    "quote": evidence.get("quote", ""),
                    "state": state.get("state", "none"),
                    "at": claim.get("at", ""),
                }
            )
    return {"used": dict(used), "made": made}
