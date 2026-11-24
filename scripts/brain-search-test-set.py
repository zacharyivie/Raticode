"""Measure Second Brain search on requests from Rem's own task history.

Rem's task threads often name the brain file the work is about ("A prior report is
available as reference material: <brain>/Reports/x.html"). Each such message gives a
search test: the request sentence is the query and the named file is the document
search should find. Rem's own searches give more: a Second Brain search followed by a
``read_note`` before the next search pairs the query Rem typed with the document it
opened (``querySource`` "rem-search"). This script collects both kinds of pairs from a
brain's conversation archive, runs them through ``BrainLibrary.search`` and keeps every
run so changes to ranking can be compared.

Run with the project's Python environment, from the repository root:

    PYTHONPATH=src .venv/bin/python scripts/brain-search-test-set.py collect
    PYTHONPATH=src .venv/bin/python scripts/brain-search-test-set.py run
    PYTHONPATH=src .venv/bin/python scripts/brain-search-test-set.py prune <pair id> ...

``--brain`` picks a brain by ID, name or folder when the library has more than one.
``--data-dir`` reads another data folder's library (the default is Raticode's).

The pairs quote the user's own requests, so they are private: they are saved with
owner-only permissions in Raticode's data folder
(``second-brains/search-test-sets/<brain ID>/``), or with ``--store brain`` in the
brain's ``.raticode/search-test-set/`` folder. The script refuses to write inside this
repository. To drop a pair by hand, run ``prune`` or move its ``id`` from ``pairs`` to
``pruned`` in ``pairs.json``; ``collect`` never adds a pruned pair back and keeps
queries edited by hand.

``run`` reports, for exact search and (when the brain has related meaning on) combined
search: top-1, top-5, top-10 and top-20 (the share of pairs whose document is among the
first 1, 5, 10 or 20 documents), the mean reciprocal rank, and how often the document is
in the first 50 passages Rem's search tool returns, for all pairs and separately for
task-prompt pairs and rem-search pairs. It also reports the share of those passages that
are only a section's heading, with no text. Each run is saved in ``runs/`` and compared with
the previous one.

Each pair is scored as of its time: the task thread's creation for a task-prompt pair,
the search call for a rem-search pair. Documents that appeared later (the earlier of
their added and modified dates is after that time) are left out before ranking, so a
report written after the request can't push the answer down.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FORMAT = "raticode-brain-search-test-set"
RUN_FORMAT = "raticode-brain-search-run"
STORE_FOLDER = "search-test-sets"
BRAIN_STORE = ".raticode/search-test-set"
# Rem's search tool shows at most this many passages on one full page.
TOOL_PASSAGES = 50
CUTOFFS = (1, 5, 10, 20)
# A message that names many files is a list, not a request about each one.
MAX_FILES_PER_MESSAGE = 3
MAX_QUERY_CHARS = 300
MIN_QUERY_WORDS = 5
MODE_NAMES = {"exact": "exact", "hybrid": "combined"}
REM_SEARCH = "rem-search"
TASK_PROMPT = "task-prompt"
SOURCES = (TASK_PROMPT, REM_SEARCH)
# Rem's Second Brain tools as a thread's trace names them. Claude Code prefixes the
# server name; Codex shows the bare tool name with the server name as the detail.
SEARCH_TOOL = "mcp__second_brain__search"
READ_TOOL = "mcp__second_brain__read_note"
BRAIN_SERVER = "second_brain"
# A search followed by many reads is browsing, not a pick.
MAX_READS_PER_SEARCH = 3

_URL_OR_PATH = re.compile(r"(?:[a-z][a-z0-9+.-]*://)?/?(?:[^\s/`\"'<>|]+/)+[^\s`\"'<>|]*")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")


# ------------------------------------------------------------------ storage


def store_folder(data_dir: Path, brain: dict[str, Any], where: str) -> Path:
    """Where a brain's pairs and runs live. Never inside this repository."""
    folder = (
        Path(brain["root"]) / BRAIN_STORE
        if where == "brain"
        else data_dir / "second-brains" / STORE_FOLDER / brain["id"]
    )
    resolved = folder.resolve()
    if resolved.is_relative_to(REPO_ROOT.resolve()):
        raise SystemExit(f"Refusing to save the private test set inside the repository: {folder}")
    return folder


def write_private(path: Path, data: dict[str, Any]) -> None:
    """Write JSON readable only by this account, replacing the file in one step."""
    for parent in reversed([path.parent, *path.parent.parents]):
        if not parent.exists():
            parent.mkdir(mode=0o700)
    handle, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not a JSON object")
    return data


def empty_set(brain: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": 1,
        "brainId": brain["id"],
        "brainRoot": brain["root"],
        "pairs": [],
        "pruned": [],
    }


# ------------------------------------------------------------- extraction


def archive_threads(root: Path) -> Iterable[dict[str, Any]]:
    """Every stored thread snapshot in the brain's conversation archives."""
    from gofer.ui.brain_graph import conversation_archives

    for archive in conversation_archives(root):
        try:
            index = json.loads((archive / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = index.get("threads") or {}
        for entry in entries.values() if isinstance(entries, dict) else entries:
            if not isinstance(entry, dict) or entry.get("deleted") or not entry.get("snapshot"):
                continue
            snapshot = (archive / str(entry["snapshot"])).resolve()
            if not snapshot.is_relative_to(archive.resolve()):
                continue
            try:
                thread = json.loads(snapshot.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(thread, dict) and not thread.get("deleted"):
                yield thread


def first_request(thread: dict[str, Any]) -> str:
    for message in thread.get("messages") or []:
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("body") or "")
    return ""


def named_files(text: str, root: Path, known: dict[str, str]) -> list[tuple[str, str]]:
    """(brain-relative path, text that names it) for each indexed brain file the text names.

    Absolute paths and file:// links under the brain folder count, and so do paths
    relative to the brain folder that have a folder in them. ``text`` is already
    percent-decoded. The longest indexed path wins, so folder names with spaces work.
    """
    found: dict[str, str] = {}
    prefix = str(root) + "/"
    for match in re.finditer(re.escape(prefix), text):
        rest = text[match.end() : match.end() + 400].split("\n")[0]
        best = next((rest[:end] for end in range(len(rest), 0, -1) if rest[:end] in known), None)
        if best and best not in found:
            begin = match.start()
            if text[max(0, begin - 7) : begin] == "file://":
                begin -= 7
            found[best] = text[begin : match.end() + len(best)]
    for relative in known:
        if "/" not in relative or relative in found:
            continue
        at = text.find(relative)
        while at >= 0:
            before = text[at - 1] if at else " "
            if not (before.isalnum() or before in "/_.-"):
                found[relative] = relative
                break
            at = text.find(relative, at + 1)
    order = {path: text.find(mention) for path, mention in found.items()}
    return sorted(found.items(), key=lambda item: order[item[0]])


def plain_words(text: str) -> list[str]:
    """The text as words the search reads as plain terms, never as query syntax."""
    return [word if word != "OR" else "or" for word in _WORD.findall(text)]


def request_query(
    text: str, mention: str, title: str, mentions: Sequence[str] = ()
) -> tuple[str, str]:
    """The request sentence for one named file, and which sentence it came from.

    The message's first sentence long enough to be a request comes first: it says what
    the work is, which is what Rem searches with. Then the sentence that names the
    file, then the thread title. File names and links are left out, so the test never
    searches for the answer's own path.
    """
    placeholder = "\x00"
    for name in sorted({mention, *mentions}, key=len, reverse=True):
        text = text.replace(name, placeholder)
    sentences = [part for part in _SENTENCE_END.split(text) if part.strip()]

    def cleaned(sentence: str) -> str:
        sentence = _URL_OR_PATH.sub(" ", sentence.replace(placeholder, " "))
        query = ""
        for word in plain_words(sentence):
            if len(query) + len(word) + 1 > MAX_QUERY_CHARS:
                break
            query = f"{query} {word}".strip()
        return query

    def long_enough(query: str) -> bool:
        return sum(len(word) > 2 for word in query.split()) >= MIN_QUERY_WORDS

    for sentence in sentences:
        if long_enough(query := cleaned(sentence)):
            return query, "first"
    naming = next((sentence for sentence in sentences if placeholder in sentence), "")
    if naming and long_enough(query := cleaned(naming)):
        return query, "mention"
    return cleaned(title) or cleaned(naming), "title"


def pair_id(thread_id: str, path: str) -> str:
    return hashlib.sha256(f"{thread_id}\0{path}".encode()).hexdigest()[:12]


def extract_pairs(
    root: Path, known: dict[str, str], *, include_chats: bool = False
) -> list[dict[str, Any]]:
    """Pairs from the first message of each archived task thread that names a brain file.

    ``known`` maps brain-relative paths to document IDs. Task threads are the ones Rem
    started for a worker (they have a managed role); ``include_chats`` adds the user's
    own chat threads.
    """
    pairs: list[dict[str, Any]] = []
    archives = {path.name for path in _archives(root)}
    usable = {path: doc_id for path, doc_id in known.items() if Path(path).parts[0] not in archives}
    for thread in archive_threads(root):
        info = thread.get("thread") or {}
        task = bool(info.get("managedRole"))
        if not task and not include_chats:
            continue
        text = urllib.parse.unquote(first_request(thread))
        title = str(info.get("title") or "")
        named = named_files(text, root, usable)
        mentions = [mention for _, mention in named]
        for path, mention in named[:MAX_FILES_PER_MESSAGE]:
            query, source = request_query(text, mention, title, mentions)
            if not query:
                continue
            thread_id = str(info.get("id") or "")
            pairs.append(
                {
                    "id": pair_id(thread_id, path),
                    "query": query,
                    "path": path,
                    "docId": usable[path],
                    "querySource": source,
                    "time": info.get("createdAt"),
                    "thread": {
                        "id": thread_id,
                        "title": title,
                        "kind": "task" if task else "chat",
                        "createdAt": info.get("createdAt"),
                    },
                }
            )
    pairs.sort(key=lambda pair: (str(pair["thread"].get("createdAt") or ""), pair["id"]))
    # The same request about the same file in several threads is one test.
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for pair in pairs:
        unique.setdefault((pair["query"].casefold(), pair["docId"]), pair)
    return list(unique.values())


def _archives(root: Path) -> list[Path]:
    from gofer.ui.brain_graph import conversation_archives

    return conversation_archives(root)


def _tool_input(value: Any) -> dict[str, Any]:
    """A trace's tool input, which threads store as JSON text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _brain_tool(trace: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Which Second Brain tool a traced call is: "search", "read" or None for neither."""
    title = str(trace.get("title") or "")
    if title == SEARCH_TOOL:
        return "search"
    if title == READ_TOOL:
        return "read"
    bare = "brainId" in arguments or trace.get("detail") == BRAIN_SERVER
    if title == "search" and bare:
        return "search"
    if title == "read_note" and bare:
        return "read"
    return None


def journal_calls(journal: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The thread's latest fields and its Second Brain tool calls in the order they started.

    A tool call is journaled as a start record and then update or result records with
    the same trace ID; the arguments often arrive only in a later record. Codex reuses
    trace IDs in later turns, so a new start record opens a new call.
    """
    thread: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    open_calls: dict[str, dict[str, Any]] = {}
    try:
        lines = journal.read_text(encoding="utf-8").splitlines()
    except OSError:
        return thread, calls
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") == "thread" and isinstance(record.get("thread"), dict):
            thread = {**thread, **record["thread"]}
            continue
        message = record.get("message")
        trace = message.get("trace") if isinstance(message, dict) else None
        if not isinstance(trace, dict) or trace.get("kind") != "tool" or not trace.get("id"):
            continue
        key = str(trace["id"])
        call = open_calls.get(key)
        if call is None or trace.get("phase") == "start":
            call = {"tool": None, "time": record.get("time"), "arguments": {}, "status": ""}
            open_calls[key] = call
            calls.append(call)
        arguments = _tool_input(trace.get("input"))
        if arguments:
            call["arguments"] = arguments
        call["tool"] = _brain_tool(trace, call["arguments"]) or call["tool"]
        call["status"] = str(trace.get("status") or call["status"])
    return thread, [call for call in calls if call["tool"]]


def _opened_document(
    arguments: dict[str, Any], root: Path, known: dict[str, str], by_id: dict[str, str]
) -> tuple[str, str] | None:
    """(brain-relative path, document ID) of the document a read_note call opened.

    ``known`` maps paths to document IDs and ``by_id`` the other way round.
    """
    doc_id = str(arguments.get("documentId") or "")
    if doc_id in by_id:
        return by_id[doc_id], doc_id
    path = str(arguments.get("path") or "")
    if path.startswith(str(root) + "/"):
        path = path[len(str(root)) + 1 :]
    if path in known:
        return path, known[path]
    return None


def search_pairs(root: Path, known: dict[str, str], brain_id: str) -> list[dict[str, Any]]:
    """Pairs from Rem's own searches in the brain's conversation archive journals.

    A Second Brain search followed by a read_note before the next search pairs the
    query Rem typed with each document it opened (up to three). Searches or reads aimed
    at another brain, failed calls and files in the archive itself are left out.
    """
    pairs: list[dict[str, Any]] = []
    for archive in _archives(root):
        usable = {
            path: doc_id for path, doc_id in known.items() if Path(path).parts[0] != archive.name
        }
        by_id = {doc_id: path for path, doc_id in usable.items()}
        try:
            index = json.loads((archive / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = index.get("threads") or {}
        deleted = {
            Path(str(entry.get("snapshot") or "")).stem
            for entry in (entries.values() if isinstance(entries, dict) else entries)
            if isinstance(entry, dict) and entry.get("deleted")
        }
        folder = archive / "threads"
        journals = sorted(folder.glob("*.jsonl")) if folder.is_dir() else []
        for journal in journals:
            if journal.stem in deleted or journal.is_symlink():
                continue
            thread, calls = journal_calls(journal)
            if thread.get("deleted"):
                continue
            current: dict[str, Any] | None = None
            opened: list[str] = []
            for call in calls:
                arguments = call["arguments"]
                if call["tool"] == "search":
                    query = str(arguments.get("query") or "").strip()
                    aimed = str(arguments.get("brainId") or brain_id)
                    failed = call["status"] in {"error", "failed"}
                    usable_search = query and aimed == brain_id and not failed
                    current = {**call, "query": query} if usable_search else None
                    opened = []
                    continue
                if current is None or len(opened) >= MAX_READS_PER_SEARCH:
                    continue
                if str(arguments.get("brainId") or brain_id) != brain_id:
                    continue
                if call["status"] in {"error", "failed"}:
                    continue
                document = _opened_document(arguments, root, usable, by_id)
                if not document or document[0] in opened:
                    continue
                opened.append(document[0])
                path, doc_id = document
                pairs.append(
                    {
                        "id": pair_id(f"{REM_SEARCH}\0{journal.stem}\0{current['time']}", path),
                        "query": current["query"],
                        "path": path,
                        "docId": doc_id,
                        "querySource": REM_SEARCH,
                        "time": current["time"],
                        "thread": {
                            "id": str(thread.get("id") or journal.stem),
                            "title": str(thread.get("title") or ""),
                            "kind": "task" if thread.get("managedRole") else "chat",
                            "createdAt": thread.get("createdAt"),
                        },
                    }
                )
    pairs.sort(key=lambda pair: (str(pair["time"] or ""), pair["id"]))
    # The same query leading to the same document again is one test.
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for pair in pairs:
        unique.setdefault((pair["query"].casefold(), pair["docId"]), pair)
    return list(unique.values())


def merge_pairs(existing: dict[str, Any], found: list[dict[str, Any]]) -> int:
    """Add new pairs. Pruned pairs stay out and edited queries stay as edited.

    Pairs collected before pairs had a time get the time of the matching new pair.
    """
    seen = {pair["id"] for pair in existing["pairs"]} | set(existing["pruned"])
    times = {pair["id"]: pair.get("time") for pair in found}
    for pair in existing["pairs"]:
        if not pair.get("time") and times.get(pair["id"]):
            pair["time"] = times[pair["id"]]
    added = [pair for pair in found if pair["id"] not in seen]
    existing["pairs"].extend(added)
    return len(added)


def prune(existing: dict[str, Any], ids: Sequence[str]) -> list[str]:
    wanted = set(ids)
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for pair in existing["pairs"]:
        (dropped if pair["id"] in wanted else kept).append(pair)
    existing["pairs"] = kept
    existing["pruned"] = sorted(set(existing["pruned"]) | {pair["id"] for pair in dropped})
    return [pair["id"] for pair in dropped]


# -------------------------------------------------------------- evaluation


def modes_for(brain: dict[str, Any]) -> list[str]:
    """Exact search always; combined search only when the brain has related meaning on."""
    return ["exact", "hybrid"] if brain["settings"].get("semantic") else ["exact"]


def parse_time(value: Any) -> datetime | None:
    """A stored time as an aware datetime: ISO text (Z or offset) or epoch milliseconds."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value / 1000, UTC)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def pair_time(pair: dict[str, Any]) -> datetime | None:
    """When the pair's request was made. Older stored pairs fall back to the thread's start."""
    return parse_time(pair.get("time")) or parse_time((pair.get("thread") or {}).get("createdAt"))


def source_of(pair: dict[str, Any]) -> str:
    return REM_SEARCH if pair.get("querySource") == REM_SEARCH else TASK_PROMPT


def document_dates(library: Any, brain_id: str) -> dict[str, datetime]:
    """When each document appeared: the earlier of its added and modified dates."""
    with library.connect() as db:
        rows = db.execute(
            "SELECT id, json_extract(body, '$.dates.added'), json_extract(body, '$.dates.modified')"
            " FROM entities WHERE kind='documents' AND brain = ?",
            (brain_id,),
        ).fetchall()
    appeared = {}
    for doc_id, added, modified in rows:
        times = [time for time in (parse_time(added), parse_time(modified)) if time]
        if times:
            appeared[doc_id] = min(times)
    return appeared


def later_documents(pair: dict[str, Any], appeared: dict[str, datetime] | None) -> frozenset[str]:
    """Documents that appeared after the pair's request, never the pair's own document."""
    asked = pair_time(pair)
    if not asked or not appeared:
        return frozenset()
    return frozenset(
        doc_id for doc_id, time in appeared.items() if time > asked and doc_id != pair["docId"]
    )


def document_rank(
    hits: list[dict[str, Any]], doc_id: str, leave_out: frozenset[str] = frozenset()
) -> int | None:
    """1-based rank of a document among the distinct documents of a hit list.

    Documents in ``leave_out`` don't count, as if search couldn't see them.
    """
    found = (hit["docId"] for hit in hits if hit["docId"] not in leave_out)
    for rank, document in enumerate(dict.fromkeys(found), start=1):
        if document == doc_id:
            return rank
    return None


def tool_passages(
    library: Any, brain: dict[str, Any], query: str, leave_out: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """The first 50 passages Rem's search tool returns, in order.

    Each is its document's ID and whether its text is only its section's heading.
    """
    from gofer.ui.second_brain import heading_only, search_documents

    with library.read_snapshot():
        reply = search_documents(
            library, [brain], {"query": query, "limit": TOOL_PASSAGES}, wrap=False
        )
    passages = [
        {"documentId": result["documentId"], "headingOnly": heading_only(passage)}
        for result in reply["results"]
        if result["documentId"] not in leave_out
        for passage in result.get("passages", [])
    ]
    return passages[:TOOL_PASSAGES]


def summarize(ranks: list[int | None]) -> dict[str, Any]:
    count = len(ranks) or 1
    summary: dict[str, Any] = {
        f"top{cutoff}": sum(rank is not None and rank <= cutoff for rank in ranks) / count
        for cutoff in CUTOFFS
    }
    summary["mrr"] = sum(1 / rank for rank in ranks if rank) / count
    summary["found"] = sum(rank is not None for rank in ranks)
    return summary


def score(results: list[dict[str, Any]], modes: Sequence[str]) -> dict[str, Any]:
    """Metrics per mode and Rem's tool's first-50-passages share for some results."""
    return {
        "pairs": len(results),
        "metrics": {
            mode: summarize([result["ranks"][mode] for result in results]) for mode in modes
        },
        "toolTop50Passages": sum(result["inToolPassages"] for result in results)
        / (len(results) or 1),
        "toolHeadingOnly": heading_only_share(results),
    }


def heading_only_share(results: list[dict[str, Any]]) -> float:
    """The share of Rem's tool passages, over all results, that are only a heading."""
    shown = sum(result.get("toolPassages", 0) for result in results)
    return sum(result.get("headingOnlyPassages", 0) for result in results) / (shown or 1)


def evaluate(
    library: Any,
    brain: dict[str, Any],
    pairs: list[dict[str, Any]],
    *,
    modes: Sequence[str] | None = None,
    appeared: dict[str, datetime] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Rank every pair's document in each mode and check Rem's tool's first 50 passages.

    ``appeared`` maps document IDs to when they appeared (see ``document_dates``); each
    pair leaves out the documents that appeared after its time.
    """
    modes = list(modes or modes_for(brain))
    results = []
    for number, pair in enumerate(pairs, start=1):
        leave_out = later_documents(pair, appeared)
        ranks = {}
        for mode in modes:
            hits = library.search({"brainIds": [brain["id"]], "query": pair["query"], "mode": mode})
            ranks[mode] = document_rank(hits["hits"], pair["docId"], leave_out)
        passages = tool_passages(library, brain, pair["query"], leave_out)
        results.append(
            {
                "id": pair["id"],
                "source": source_of(pair),
                "ranks": ranks,
                "laterDocuments": len(leave_out),
                "inToolPassages": any(item["documentId"] == pair["docId"] for item in passages),
                "toolPassages": len(passages),
                "headingOnlyPassages": sum(item["headingOnly"] for item in passages),
            }
        )
        if progress:
            progress(number, len(pairs))
    overall = score(results, modes)
    return {
        "format": RUN_FORMAT,
        "version": 1,
        "brainId": brain["id"],
        "pairs": len(pairs),
        "modes": modes,
        "metrics": overall["metrics"],
        "toolTop50Passages": overall["toolTop50Passages"],
        "toolHeadingOnly": overall["toolHeadingOnly"],
        "sources": {
            source: score([result for result in results if result["source"] == source], modes)
            for source in SOURCES
            if any(result["source"] == source for result in results)
        },
        "results": results,
    }


def _changes(after: dict[str, Any], before: dict[str, Any]) -> str:
    keys = [f"top{cutoff}" for cutoff in CUTOFFS] + ["mrr"]
    return ", ".join(f"{key} {after[key] - before[key]:+.3f}" for key in keys if key in before)


def compare(current: dict[str, Any], previous: dict[str, Any] | None) -> list[str]:
    """Lines saying how this run differs from the previous one."""
    if not previous:
        return ["No earlier run to compare with."]
    lines = [f"Compared with {previous.get('ranAt', 'the previous run')}:"]
    for mode in current["modes"]:
        before = (previous.get("metrics") or {}).get(mode)
        if not before:
            continue
        lines.append(f"  {MODE_NAMES[mode]}: {_changes(current['metrics'][mode], before)}")
        for source, scored in current.get("sources", {}).items():
            prior = ((previous.get("sources") or {}).get(source) or {}).get("metrics", {})
            if mode in prior:
                lines.append(f"    {source}: {_changes(scored['metrics'][mode], prior[mode])}")
    tool_change = current["toolTop50Passages"] - previous.get("toolTop50Passages", 0)
    lines.append(f"  Rem's tool, first 50 passages: {tool_change:+.3f}")
    if "toolHeadingOnly" in previous:
        heading_change = current["toolHeadingOnly"] - previous["toolHeadingOnly"]
        lines.append(f"  Rem's tool, passages that are only a heading: {heading_change:+.3f}")
    old = {result["id"]: result for result in previous.get("results", [])}
    mode = current["modes"][0]
    better, worse = [], []
    for result in current["results"]:
        prior = old.get(result["id"])
        if not prior or mode not in prior.get("ranks", {}):
            continue
        now, then = result["ranks"][mode] or 10**9, prior["ranks"][mode] or 10**9
        if now < then:
            better.append(result["id"])
        elif now > then:
            worse.append(result["id"])
    lines.append(
        f"  {MODE_NAMES[mode]} ranks: {len(better)} pairs better, {len(worse)} worse"
        + (f" (worse: {', '.join(worse[:10])})" if worse else "")
    )
    return lines


def _metric_line(label: str, metrics: dict[str, Any]) -> str:
    return (
        f"{label}: top-1 {metrics['top1']:.3f}  top-5 {metrics['top5']:.3f}"
        f"  top-10 {metrics['top10']:.3f}  top-20 {metrics['top20']:.3f}  MRR {metrics['mrr']:.3f}"
    )


def report(run: dict[str, Any]) -> list[str]:
    sources = run.get("sources") or {}
    counts = ", ".join(f"{source} {scored['pairs']}" for source, scored in sources.items())
    lines = [f"{run['pairs']} pairs" + (f" ({counts})" if counts else "")]
    later = [result.get("laterDocuments", 0) for result in run["results"]]
    if any(later):
        lines.append(
            f"  Left out documents that appeared after the request:"
            f" {sum(map(bool, later))} pairs, {max(later)} documents at most"
        )
    for mode in run["modes"]:
        lines.append(_metric_line(f"  {MODE_NAMES[mode]:>8}", run["metrics"][mode]))
        for source, scored in sources.items():
            lines.append(_metric_line(f"    {source:>11}", scored["metrics"][mode]))
    lines.append(f"  Rem's tool, document in first 50 passages: {run['toolTop50Passages']:.3f}")
    for source, scored in sources.items():
        lines.append(f"    {source:>11}: {scored['toolTop50Passages']:.3f}")
    if "toolHeadingOnly" in run:
        share = run["toolHeadingOnly"]
        lines.append(f"  Rem's tool, passages that are only a heading: {share:.3f}")
        for source, scored in sources.items():
            lines.append(f"    {source:>11}: {scored.get('toolHeadingOnly', 0):.3f}")
    return lines


# ------------------------------------------------------------------- CLI


def choose_brain(library: Any, wanted: str | None) -> dict[str, Any]:
    brains = library.brains()
    if wanted:
        folder = str(Path(wanted).expanduser().resolve()) if "/" in wanted else None
        matches = [
            brain
            for brain in brains
            if wanted in {brain["id"], brain["name"]} or brain["root"] == folder
        ]
    else:
        matches = brains
    if len(matches) != 1:
        names = ", ".join(f"{brain['name']} ({brain['id']})" for brain in brains) or "none"
        raise SystemExit(f"Choose one brain with --brain. Brains in this library: {names}")
    return dict(matches[0])


def known_documents(library: Any, brain_id: str) -> dict[str, str]:
    return {
        str(header["path"]): doc_id
        for doc_id, header in library.document_headers([brain_id]).items()
        if header.get("path") and not header.get("missing") and not header.get("excluded")
    }


def resolve_pairs(pairs: list[dict[str, Any]], known: dict[str, str]) -> list[dict[str, Any]]:
    """Pairs whose file is still indexed, each with the document ID at its path now."""
    documents = set(known.values())
    resolved = []
    for pair in pairs:
        doc_id = known.get(str(pair.get("path") or "")) or pair.get("docId")
        if doc_id in documents:
            resolved.append({**pair, "docId": doc_id})
    return resolved


# ---------------------------------------------------------------- fixture

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "brain-search"
# The recorded baseline keeps the keys it was first recorded with.
BASELINE_KEYS = ("top1", "top5", "top10", "mrr")


def fixture_run(scratch: Path) -> dict[str, Any]:
    """Evaluate the committed fixture brain and queries in a throwaway library."""
    import shutil
    import time

    from gofer.ui.brain_library import BrainLibrary

    root = scratch / "brain"
    shutil.copytree(FIXTURE / "brain", root, ignore=shutil.ignore_patterns(".*"))
    # One date for every file, so search's recency lift leaves the fixture's match order
    # alone instead of following the order a checkout happened to write the files in.
    moment = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    for path in root.rglob("*"):
        os.utime(path, (moment, moment))
    test_set = read_json(FIXTURE / "queries.json") or {}
    library = BrainLibrary(scratch / "data", start=False)
    try:
        brain = library.open({"root": str(root)})
        deadline = time.monotonic() + 60
        while any(task["state"] == "running" for task in library.snapshot()["tasks"]):
            if time.monotonic() > deadline:
                raise RuntimeError("Indexing the fixture brain did not finish in 60 seconds")
            time.sleep(0.02)
        brain = library.brain(brain["id"])
        known = known_documents(library, brain["id"])
        pairs = resolve_pairs(test_set.get("pairs", []), known)
        if len(pairs) != len(test_set.get("pairs", [])):
            raise RuntimeError("A fixture query names a file the fixture brain doesn't have")
        return evaluate(library, brain, pairs)
    finally:
        library.close()


def baseline_of(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "pairs": run["pairs"],
        "metrics": {
            mode: {key: round(metrics[key], 4) for key in BASELINE_KEYS}
            for mode, metrics in run["metrics"].items()
        },
        "toolTop50Passages": round(run["toolTop50Passages"], 4),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("collect", "run", "prune", "fixture"))
    parser.add_argument("ids", nargs="*", help="pair IDs for prune")
    parser.add_argument("--brain", help="brain ID, name or folder")
    parser.add_argument("--data-dir", type=Path, help="Raticode data folder to read")
    parser.add_argument(
        "--store",
        choices=("data", "brain"),
        default="data",
        help="keep the test set in the data folder (default) or the brain's .raticode",
    )
    parser.add_argument(
        "--include-chats", action="store_true", help="collect from chat threads too"
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="with fixture: save the result as the fixture's baseline.json",
    )
    arguments = parser.parse_args(argv)
    if arguments.command == "fixture":
        with tempfile.TemporaryDirectory(prefix="raticode-brain-search-") as scratch:
            run = fixture_run(Path(scratch))
        print("\n".join(report(run)))
        if arguments.record:
            write_private(FIXTURE / "baseline.json", baseline_of(run))
            (FIXTURE / "baseline.json").chmod(0o644)
            print(f"Recorded {FIXTURE / 'baseline.json'}")
        return 0

    from gofer.ui.brain_library import BrainLibrary
    from gofer.utils.paths import get_data_dir

    data_dir = (arguments.data_dir or get_data_dir()).expanduser()
    if not (data_dir / "second-brains" / "library.sqlite3").is_file():
        raise SystemExit(
            f"No Second Brain library in {data_dir}. Pass --data-dir with Raticode's data"
            " folder (the desktop app's GOFER_DATA_DIR)."
        )
    library = BrainLibrary(data_dir, start=False, recover=False)
    try:
        brain = choose_brain(library, arguments.brain)
        folder = store_folder(data_dir, brain, arguments.store)
        pairs_file = folder / "pairs.json"
        test_set = read_json(pairs_file) or empty_set(brain)
        if arguments.command == "collect":
            known = known_documents(library, brain["id"])
            root = Path(brain["root"])
            prompts = extract_pairs(root, known, include_chats=arguments.include_chats)
            searches = search_pairs(root, known, brain["id"])
            added = merge_pairs(test_set, prompts + searches)
            test_set["updatedAt"] = datetime.now(UTC).isoformat()
            write_private(pairs_file, test_set)
            total = len(test_set["pairs"])
            print(
                f"Found {len(prompts)} {TASK_PROMPT} and {len(searches)} {REM_SEARCH} pairs,"
                f" added {added}. {total} in {pairs_file}"
            )
            return 0
        if arguments.command == "prune":
            if not arguments.ids:
                parser.error("prune needs pair IDs")
            dropped = prune(test_set, arguments.ids)
            write_private(pairs_file, test_set)
            print(f"Pruned {len(dropped)} pairs. {len(test_set['pairs'])} left in {pairs_file}")
            return 0
        if not test_set["pairs"]:
            raise SystemExit(f"No pairs in {pairs_file}. Run collect first.")
        pairs = resolve_pairs(test_set["pairs"], known_documents(library, brain["id"]))
        if len(pairs) < len(test_set["pairs"]):
            print(f"Skipping {len(test_set['pairs']) - len(pairs)} pairs whose file is gone.")

        def progress(number: int, total: int) -> None:
            if number % 10 == 0 or number == total:
                print(f"  {number}/{total}", file=sys.stderr)

        appeared = document_dates(library, brain["id"])
        run = evaluate(library, brain, pairs, appeared=appeared, progress=progress)
        run["ranAt"] = datetime.now(UTC).isoformat()
        runs = folder / "runs"
        earlier = sorted(runs.glob("*.json")) if runs.is_dir() else []
        previous = read_json(earlier[-1]) if earlier else None
        stamp = run["ranAt"].replace(":", "").replace("-", "").split(".")[0]
        write_private(runs / f"{stamp}.json", run)
        print("\n".join(report(run) + compare(run, previous)))
        print(f"Saved {runs / (stamp + '.json')}")
        return 0
    finally:
        library.close()


if __name__ == "__main__":
    raise SystemExit(main())
