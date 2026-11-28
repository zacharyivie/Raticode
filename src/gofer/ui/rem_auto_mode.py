"""What auto mode lets Rem decide on its own, and which project a ticket's work belongs in.

With auto mode on ("Approve its own backlog tickets" in Settings > Rem) the user doesn't
review work at the ticket level. A ticket only waits for them when the work is impossible
without a person: missing credentials or an account, a payment, or an action only they can
take. Review findings, unresolved checks and "please confirm" requests never park a ticket
on the user; Rem repairs, retries, or records what it couldn't verify.

Every check here is a plain text rule, so the same evidence always gives the same answer.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

AUTO_APPROVE = "auto_approve_proposals"

# Things only a person can supply or do. Asking for approval, a review or a test run is not
# on this list: in auto mode Rem decides those itself.
HUMAN_ONLY = re.compile(
    r"\bcredentials?\b|\bpasswords?\b|\bpassphrase\b|\bapi[ _-]?keys?\b|\baccess keys?\b|"
    r"\bsecret keys?\b|\bsign(?:ed|ing)?[ -]?in\b|\blog(?:ged|ging)?[ -]?in\b|\blogin\b|"
    r"\baccounts?\b|\b2fa\b|two[- ]factor|one[- ]time (?:code|password)|verification code|"
    r"\bbilling\b|\bpayments?\b|\bpurchase\b|\bcaptcha\b|\blicen[cs]e keys?\b|\boauth\b|"
    r"\bkeychain\b|only (?:you|the user|the owner) can",
    re.IGNORECASE,
)

# A request for the user's approval, review or sign-off, or a check only a person can do by hand.
SIGNOFF = re.compile(
    r"\b(?:the )?(?:user|owner|human)(?:'s|’s)? (?:review|approval|approve|sign[- ]?off|"
    r"confirm(?:ation)?|ok(?:ay)?|go[- ]ahead|consent|acceptance|verification)\b|"
    r"\b(?:ask|wait for|until|once|have|get) (?:the )?(?:user|owner|human)\b|"
    r"\bmake sure (?:that )?(?:the )?(?:user|owner|you)\b|\bsign[- ]?off\b|"
    r"\b(?:user|you) (?:approves?|confirms?|signs? off|reviews?|accepts?|verif(?:y|ies))\b|"
    r"\bmanual(?:ly)? (?:verif\w*|review\w*|test\w*|check\w*|approv\w*)|"
    r"\bby hand\b|\b(?:on|with) a (?:real|physical) (?:phone|device)\b|\breal[- ]device\b|"
    r"\b(?:real|physical) phone\b|\bplease (?:verify|confirm|review|approve)\b|"
    r"\bcould you (?:run|check|confirm|verify|try)\b",
    re.IGNORECASE,
)

# Evidence this computer's sandbox can't produce: the reviewer and worker say so in words
# like these, or start the evidence with UNVERIFIABLE.
UNVERIFIABLE = "Unverifiable here:"
SANDBOX_LIMIT = re.compile(
    r"unverifiable here|\bsandbox\b|\bbubblewrap\b|\bbwrap\b|\bnamespaces?\b|/dev/kvm|\bkvm\b|"
    r"\bemulator\b|\badb\b|normal terminal|outside (?:the|this|a) (?:\w+ )?sandbox|"
    r"can(?:no|')t reach github|\bno network\b|after (?:a |the )?push|\bci run\b|"
    r"\bnightly\b|\bmac\b|\bwindows machine\b",
    re.IGNORECASE,
)

# A worker or reviewer saying the thread is rooted in a folder that can't hold the work.
WRONG_ROOT = re.compile(
    r"read-only file system|(?:is|stays|was|are) read-only (?:here|from here|to me|to it|"
    r"in this|for this|throughout)|can only write (?:to|inside|in|under|there)|"
    r"can(?:no|')t write (?:to|there|into)|cannot write|wrong project|wrong folder|"
    r"project_?dir (?:is|must be|set to|should be|=)|\brooted (?:at|in) |"
    r"not this (?:task's |thread's )?project|folder needed|"
    r"(?:re-?run|run|start|send) (?:it|this|the ticket|the task)?\s*(?:again )?"
    r"(?:in|as) a (?:new )?(?:task|thread|worker)",
    re.IGNORECASE,
)

# A write that failed, or a folder the worker says it can't write to. A path in the same
# sentence is where the write was refused.
WRITE_FAILURE = re.compile(
    r"read-only file system|(?:is|stays|was|are|remains)(?: still| now)? read-only|"
    r"reports read-only|can only write|"
    r"can(?:no|')t write|cannot write|couldn't write|unable to write|"
    r"permission denied|operation not permitted|cannot touch|can't create|cannot create|"
    r"failed to (?:create|write)",
    re.IGNORECASE,
)
# A result saying no work happened at all.
NOTHING_BUILT = re.compile(
    r"nothing (?:was |has been |is |could be )?(?:built|changed|done|edited|committed|"
    r"implemented|landed)|(?:changed|built|edited|committed|implemented|did) nothing|"
    r"no (?:work|changes|code changes|edits) (?:was |were |has been |have been )?(?:done|made)|"
    r"none of the work (?:is|was|has been) done|made no (?:changes|edits)|"
    r"(?:haven't|have not|hasn't|has not|wasn't|was not|never) (?:been )?(?:built|started)|"
    r"no code changed|nothing to land|(?:didn't|did not|without) chang(?:e|ing) anything|"
    r"nothing (?:was|has been) created",
    re.IGNORECASE,
)
# A result reporting work that landed; such a run goes to review, whatever else it says.
LANDED = re.compile(
    r"\b(?:landed|merged|fast-forwarded|committed)\b[^.\n]{0,40}?\b(?:on|onto|into|to) "
    r"(?:local )?`?dev\b|\bcommit `?[0-9a-f]{7,40}`? (?:is|was) (?:still )?on (?:local )?`?dev",
    re.IGNORECASE,
)
NEGATED = re.compile(r"\b(?:nothing|not|no|never|none)\b|n't\b", re.IGNORECASE)
# A sentence naming the folder the work should run in ("rerun it with project_dir X").
NAMES_ROOT = re.compile(
    r"re-?run|run (?:it|this|the task|the ticket)?\s*again|\bneeds? (?:a|to)\b|must be|"
    r"set to|should be|folder needed|belongs in|rooted (?:in|at)|project_?dir|start (?:it|a)",
    re.IGNORECASE,
)
# Links a worker made to what it saved (reports, notes); they aren't where the code is.
LINK = re.compile(r"\]\(<?[^)>]*>?\)")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
OUTPUT_PARTS = frozenset({"reports", "notes", "chat-attachments"})
OUTPUT_SUFFIXES = frozenset({".html", ".pdf", ".htm"})

# Assigned project roots are found literally in the text (see _mentions), so a root with a
# space in it, or a Windows root with no leading /, still counts. These are what may come
# right after a root: a separator and more path, which is a path below it, or the end of
# the path, perhaps after a trailing separator or a sentence's punctuation.
SEPARATORS = "".join(dict.fromkeys("/" + os.sep))
_SEP = f"[{re.escape(SEPARATORS)}]"
BELOW_ROOT = re.compile(rf"{_SEP}((?:[\w.@+-]+{_SEP})*[\w.@+-]+)")
ROOT_END = re.compile(rf"{_SEP}?(?:$|[\s\"'`)\]>]|[.,;:!?]+(?:$|[\s\"'`)\]>]))")
# A character before a root that makes it part of a longer name or path.
NOT_ROOT_START = frozenset("._-~@+" + SEPARATORS)
# Paths inside a project, such as src/gofer/ui/server.py or website/docs/rem/board.md,
# including one that ends a sentence.
RELATIVE_PATH = re.compile(
    r"(?<![\w./~-])((?:[\w.@+-]+/)+[\w.@+-]+\.\w{1,6})(?=[\s:`'\"),;]|\.(?:\s|$)|$)"
)
# Bound the work for one ticket's text.
MAX_PATHS = 60


def on(settings: dict[str, Any] | None) -> bool:
    """Whether auto mode is on in Rem's self-work settings."""
    return bool((settings or {}).get(AUTO_APPROVE))


def human_only(text: str) -> bool:
    """Whether a question or block reason names something only a person can provide."""
    return bool(HUMAN_ONLY.search(text or ""))


def asks_signoff(text: str) -> bool:
    """Whether a criterion or request asks for the user's approval, review or hand check."""
    return bool(SIGNOFF.search(text or ""))


def unverifiable(text: str) -> bool:
    """Whether evidence says it can't be produced here (sandbox, device, CI or a person)."""
    return bool(SANDBOX_LIMIT.search(text or "") or SIGNOFF.search(text or ""))


def strip_signoff(text: str) -> str:
    """Drop the sentences of a done-when that ask for the user's sign-off; may return ''."""
    sentences = re.split(r"(?<=[.!?;])\s+|\n+", text or "")
    return " ".join(s for s in sentences if s.strip() and not asks_signoff(s)).strip()


def limitations(criteria: Iterable[dict[str, Any]]) -> list[str] | None:
    """In auto mode, the gaps a review may accept as unverified; None if any must be fixed.

    Every criterion must pass, or be unknown with evidence that it can only be proven
    outside this computer's sandbox or by a person. A failed criterion is never accepted.
    """
    gaps: list[str] = []
    for criterion in criteria:
        result = criterion.get("result")
        if result == "pass":
            continue
        evidence = str(criterion.get("evidence") or "")
        if result != "unknown" or not unverifiable(evidence):
            return None
        gaps.append(evidence.strip())
    return gaps


def wrong_root(text: str) -> bool:
    """Whether a result says its thread is rooted in a folder that can't hold the work."""
    return bool(WRONG_ROOT.search(text or ""))


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _output(path: Path) -> bool:
    """A report, note or attachment the worker saved or linked, not the code's location."""
    return bool(OUTPUT_PARTS.intersection(path.parts)) or path.suffix.lower() in OUTPUT_SUFFIXES


def _clean(text: str) -> str:
    """The text without its Markdown link targets, which point at saved outputs."""
    return LINK.sub("]", text or "")


def _mentions(text: str, roots: Iterable[str]) -> list[tuple[str, Path]]:
    """Each distinct path the text writes at or below one of these roots, as (root, path).

    A root counts only with a path boundary on both sides: alone, or followed by a
    separator and more path, which is the path below it. So ``/x/code`` isn't found in
    ``/x/codebase`` or ``/y/x/code``. Matching uses os.path.normcase, so a Windows build
    ignores case and treats / and \\ alike.
    """
    haystack = os.path.normcase(text or "")
    found: list[tuple[str, Path]] = []
    for root in dict.fromkeys(roots):
        needle = os.path.normcase(root or "").rstrip(SEPARATORS)
        if not needle:
            continue
        seen: set[Path] = set()
        start = haystack.find(needle)
        while start >= 0 and len(seen) < MAX_PATHS:
            end = start + len(needle)
            before = haystack[start - 1] if start else ""
            path: Path | None = None
            if not (before.isalnum() or before in NOT_ROOT_START):
                if below := BELOW_ROOT.match(haystack, end):
                    path = Path(root) / below.group(1).rstrip(".,:;")
                elif ROOT_END.match(haystack, end):
                    path = Path(root)
            if path is not None and path not in seen:
                seen.add(path)
                found.append((root, path))
            start = haystack.find(needle, start + 1)
    return found


def landed(text: str) -> bool:
    """Whether a result reports work that landed on dev ("nothing landed" doesn't count)."""
    for match in LANDED.finditer(text or ""):
        before = text[max(0, match.start() - 30) : match.start()]
        clause = re.split(r"[.;:\n]", before)[-1]
        if not NEGATED.search(clause + " " + match.group(0).split()[0]):
            return True
    return False


def misrooted_target(text: str, current: str | None, projects: Iterable[str]) -> str | None:
    """The assigned project a run's work belongs in, when its own root couldn't hold it.

    Only two kinds of result count, and never one that reports work landed on dev:

    - a failed write or read-only error naming a path inside another assigned project;
    - a result that says nothing was built and gives a wrong-root reason, whose code paths
      point at another assigned project.

    Paths the worker linked or saved outputs to (reports, notes) are not evidence.
    """
    clean = _clean(text)
    roots = [p for p in dict.fromkeys(projects) if p and p != current]
    if not roots:
        return None
    if landed(clean):
        return None
    nothing = bool(NOTHING_BUILT.search(clean))
    refused: set[str] = set()
    for sentence in SENTENCE.split(clean):
        if not WRITE_FAILURE.search(sentence):
            continue
        for root, path in _mentions(sentence, roots):
            if _output(path) or (current and _inside(path, Path(current))):
                continue
            refused.add(root)
    if len(refused) == 1:
        target = refused.pop()
        # A result that did some work must also point at the other project's files more
        # than at its own; a passing remark about a read-only folder moves nothing.
        if nothing or pick_project(clean, current, list(projects), misrooted=True) == target:
            return target
        return None
    if not (nothing and wrong_root(clean)):
        return None
    # Nothing was built for a wrong-root reason: the folder the worker or reviewer says the
    # rerun needs decides, when they name exactly one; otherwise the files named do.
    named = {
        root
        for sentence in SENTENCE.split(clean)
        if NAMES_ROOT.search(sentence)
        for root, path in _mentions(sentence, roots)
        if path == Path(root)
    }
    if len(named) == 1:
        return named.pop()
    return pick_project(clean, current, list(projects), misrooted=True)


def _score(text: str, root: str) -> tuple[int, int, int]:
    """(absolute paths below root, mentions of root itself, project paths that exist in root)."""
    base = Path(root)
    deep = bare = relative = 0
    for _, path in _mentions(_clean(text), [root]):
        if _output(path):
            continue
        if path == base:
            bare += 1
        else:
            deep += 1
    for raw in list(dict.fromkeys(RELATIVE_PATH.findall(text)))[:MAX_PATHS]:
        name = raw.split(":", 1)[0]
        if name.startswith("../"):
            continue
        try:
            if (base / name).exists():
                relative += 1
        except OSError:
            continue
    return deep, bare, relative


def pick_project(
    text: str, current: str | None, projects: Iterable[str], *, misrooted: bool = False
) -> str | None:
    """The assigned project the text's file paths point at, when it isn't ``current``.

    Absolute paths count three times a project-relative path that exists in the project.
    A project wins only with more evidence than the current project and every other one, so
    a ticket that names files in its own project stays where it is. With ``misrooted``
    (the run said its root can't hold the work) a mention of the current root itself
    doesn't count for it: a worker complaining about its root names that root.
    """
    text = text or ""
    roots = [p for p in dict.fromkeys(projects) if p]
    if not roots:
        return None

    def weight(root: str, *, own: bool = False) -> int:
        deep, bare, relative = _score(text, root)
        if not misrooted:
            return (deep + bare) * 3 + relative
        # A worker complaining about its root names the root itself; that is no evidence
        # for it. Files below a root are, wherever the worker sits.
        return deep * 3 + relative + (0 if own else bare)

    here = weight(current, own=True) if current else 0
    scores = {root: weight(root) for root in roots if root != current}
    if not scores:
        return None
    best = max(scores, key=lambda r: scores[r])
    if scores[best] == 0 or scores[best] <= here:
        return None
    # Two projects with the same evidence (one nested in the other) is no evidence.
    if sum(1 for r in scores if scores[r] == scores[best]) > 1:
        return None
    return best


def reroot_target(
    ticket: dict[str, Any], run: dict[str, Any] | None, projects: Iterable[str]
) -> str | None:
    """Where a blocked ticket's work belongs, when its run hit the wrong project root."""
    current = ticket.get("project_dir")
    choices = [p for p in projects if p != current]
    if run is not None and run.get("wrong_project") in choices:
        return str(run["wrong_project"])
    evidence = "\n".join(
        str(x or "")
        for x in (
            ticket.get("block_reason"),
            (run or {}).get("worker_result"),
            (run or {}).get("review_feedback"),
            (run or {}).get("summary_short"),
        )
    )
    if not misrooted_target(evidence, current, projects):
        return None
    # Confirmed misrooted: the ticket's own description may add where its code is.
    return misrooted_target(
        evidence + "\n" + str(ticket.get("description") or ""), current, projects
    )


def file_under(ticket: dict[str, Any], projects: Iterable[str]) -> str | None:
    """The assigned project a ticket's own text points at, if not the one it's filed under.

    Used before work starts, so a second-brain ticket whose code is in another project is
    filed under that project. Only file paths count, and the current project must have none.
    """
    current = ticket.get("project_dir")
    text = f"{ticket.get('title') or ''}\n{ticket.get('description') or ''}"
    if current and sum(_score(text, current)) > 0:
        return None
    return pick_project(text, current, list(projects))


WORKER_GUIDANCE = (
    "\nAuto mode is on: the user does not review work at the ticket level. Don't ask the "
    "user to review, approve, confirm or test anything, and don't wait for their sign-off. "
    "If a command or check can't run in your sandbox (for example it needs namespaces, a "
    "real device, a push or the network), don't ask anyone to run it: say "
    f"'{UNVERIFIABLE} <the command and why>' in your result and finish the rest. Stop for "
    "the user only when the work is impossible without them: missing credentials or an "
    "account, a payment, or an action only they can take.\n"
)

REVIEW_GUIDANCE = (
    "\nAuto mode is on: the user does not review or sign off on this work. Never require the "
    "user's approval, confirmation, a hand check or a real-device test unless a criterion "
    "authored_by user asks for it. When a criterion can only be proven by something this "
    "computer's sandbox can't do (a command the sandbox blocks, a real device, a push or CI, "
    "a person's confirmation), return unknown with evidence starting "
    f"'{UNVERIFIABLE}' and saying what was checked instead. Use fail only for a real defect "
    "or missing work.\n"
)

ANSWER_GUIDANCE = (
    "\nAuto mode is on: the user does not review, approve or confirm work at the ticket "
    "level. needs_user:true is only for credentials, an account, a payment or an action only "
    "the user can take. For approval, review, confirmation or a command the worker's "
    "sandbox can't run, answer with needs_user:false: tell the worker to choose the "
    f"reasonable default, finish, and list what it couldn't run as '{UNVERIFIABLE} ...'.\n"
)

CRITERIA_RULE = (
    "Never write criteria asking for the user's approval, review, confirmation or sign-off "
    "unless the user asked for that or Rem's auto mode is off. "
)


def limitation_note(gaps: list[str]) -> str:
    """The line a finished run adds for what it accepted without verifying here."""
    if not gaps:
        return ""
    shown = [g.removeprefix(UNVERIFIABLE).strip()[:300] for g in gaps[:4]]
    return "Not verified here (auto mode): " + " · ".join(shown)
