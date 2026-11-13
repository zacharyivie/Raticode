"""Auto mode: Rem decides an organization's requests instead of the user.

An organization with ``autoMode`` on (only the user turns it on or off) sends its
waiting requests to a Rem review step: consent requests Rem's own tools made, plans and
proposals waiting for approval, and finished tickets waiting for the user's review. Rem
reads each one in context and decides:

- ``approve``: apply it as asked.
- ``modify``: apply it with safer parameters (JSON-pointer ``changes`` to the request, or
  ``ticketChanges`` to a ticket before approving its plan).
- ``decline``: close it with a reason and, where it helps, a recommended safer change.
- ``halt``: pause the organization with a specific reason and leave the request for the
  user. Only for a dangerous action Rem can't remedy short of stopping everything, or
  when going on without the user is literally impossible.

Raticode checks every decision before applying it. A request that is dangerous
(``assess``) can't be approved as asked: Rem must make it safe, decline it, or halt. A
halt needs a danger Raticode found or a named impossibility, so ordinary or uncertain
requests never stop the organization. Changes only the user may make (Auto mode itself,
the organization's push and folder-isolation policy, and higher budgets or run limits)
are never sent to Rem; they wait for the user while the organization keeps working.

Decisions go through the same path as the user's Approve button
(``OrganizationManager._decide``) in ``approval_order``, re-reading the organization
before each. A decision is saved on its request before it is applied, so a restart
applies it once without asking Rem again.

``approve_on_instruction`` is the other, separate exception to the user-only rule: Rem
approving requests because the user told it to in their own chat message.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gofer.ui.organization_consent import approval_order, approve_all, conflict

if TYPE_CHECKING:
    from gofer.ui.organizations import OrganizationManager

log = logging.getLogger(__name__)

AUTO_ACTOR = "Rem:auto"
INSTRUCTED_ACTOR = "Rem:user-instruction"
# Rem's recent decisions kept on the organization for the UI and the phone.
MAX_DECISIONS = 30
# Provider failures in a row before the organization halts because Rem can't review.
MAX_FAILURES = 3
RETRY_SECONDS = (30, 120, 300)
# Plans, proposals and reviews turned into requests in one pass.
MAX_QUEUED = 10

IMPOSSIBLE = {
    "missing_credentials": "a credential the work needs is missing",
    "hardware": "hardware the work needs isn't available",
    "internet": "there's no internet access",
    "human_sign_in": "a sign-in only you can complete is needed",
}
DECISIONS = {"approve", "modify", "decline", "halt"}
# Request fields Rem's changes may never touch.
PROTECTED = re.compile(r"^/(expectedRevision|taskId|organizationId|revision|requestId)(/|$)")
TICKET_FIELDS = {"title", "description", "doneWhen", "priority", "assignee"}

# Words worth Rem's attention in a ticket or request. They are hints, not verdicts: text
# often says "no live trading". Raticode's own danger checks are structural (assess).
HINTS = (
    (
        r"\blive[- ]trad\w*|\breal[- ]money\b|\bbrokerage\b|\bbroker\b|trading api\b|"
        r"\bplace (?:a |an |real )?orders?\b|\balpaca\b|interactive brokers|\brobinhood\b|"
        r"public\.com\b|\btradier\b|\bcoinbase\b",
        "live trading, real money or a brokerage",
    ),
    (
        r"\bgit push\b|\bforce[- ]push\b|\bpublish\w*|\bdeploy\w*|\bnpm publish\b",
        "pushing, publishing or deploying",
    ),
    (
        r"\brm -rf\b|\bdelete (?:all|every|the (?:repo|repository|database|branch))\b|"
        r"\bdrop (?:table|database)\b|\bwipe\b|\bpurge\b|\birreversibl\w*",
        "irreversible deletion",
    ),
    (r"\bapi[ _-]?keys?\b|\bcredentials?\b|\bpasswords?\b|\baccess tokens?\b", "credentials"),
    (r"\bpurchase\b|\bpay for\b|\bspend\w* (?:money|\$)|\bcredit card\b", "spending money"),
    (r"danger-full-access|bypassPermissions|--dangerously|\bsudo\b", "running without a sandbox"),
)


# Hints that block a plain approval until Rem says why none of it happens (hintsCleared).
# Text often forbids these ("paper only, no live trading"), so they can't refuse outright
# without making the safety wording itself unapprovable.
RISKY_HINTS = frozenset(
    {
        "live trading, real money or a brokerage",
        "pushing, publishing or deploying",
        "irreversible deletion",
        "spending money",
    }
)


def _cleared(raw: dict[str, Any], hints: list[str]) -> str:
    """Rem's explanation of why the risky words in the text don't happen, or a ValueError."""
    risky = [h for h in hints if h in RISKY_HINTS]
    if not risky:
        return ""
    cleared = str(raw.get("hintsCleared") or "").strip()
    if len(cleared) < 20:
        raise ValueError(
            "The request's text mentions " + "; ".join(risky) + ". Approve it only with "
            "hintsCleared saying why none of that happens (for example, the ticket forbids "
            "it). Otherwise modify it to a safe version, decline it, or halt only if nothing "
            "else can stop the danger."
        )
    return cleared[:1000]


def now() -> str:
    return datetime.now(UTC).isoformat()


def auto_on(org: dict[str, Any]) -> bool:
    """Whether Rem decides this organization's requests right now."""
    return bool(org["config"].get("autoMode")) and not org["runtime"].get("autoHalt")


def waits_on_rem(org: dict[str, Any], request: dict[str, Any]) -> bool:
    return request.get("status") == "pending" and auto_on(org) and request.get("route") != "user"


def _age_ok(request: dict[str, Any], ttl_seconds: float) -> bool:
    try:
        asked = datetime.fromisoformat(str(request.get("at")))
    except ValueError:
        return False
    return (datetime.now(UTC) - asked).total_seconds() < ttl_seconds


# What a request would change -------------------------------------------------------------


def requested_config(
    manager: OrganizationManager, project: str, org: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any] | None:
    """The configuration a configure or restore request would save, or None."""
    from gofer.ui.organization_store import OrganizationConfig

    params = request.get("params") or {}
    after: Any = params.get("config")
    if request.get("action") == "restore":
        after = next(
            (
                r["config"]
                for r in manager.store.history(project, org["id"])
                if r["revision"] == params.get("revision")
            ),
            None,
        )
    if request.get("action") not in {"configure", "restore"} or not isinstance(after, dict):
        return None
    try:
        return OrganizationConfig.model_validate(after).model_dump()
    except ValueError:
        return None


def _task(org: dict[str, Any], request: dict[str, Any]) -> dict[str, Any] | None:
    tid = (request.get("params") or {}).get("taskId")
    return next((t for t in org["runtime"]["tasks"] if t["id"] == tid), None)


def config_risks(before: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Dangerous parts of a configuration change, and parts only the user may make."""
    from gofer.ui.organization_store import DANGEROUS_PERMISSION_MODES
    from gofer.ui.organizations import COMPANY_CAPS, OPEN_ENDED_LIMITS, _loosened

    danger: list[str] = []
    user_only: list[str] = []
    old = {e.get("id"): e for e in before.get("employees", [])}
    for employee in after.get("employees", []):
        name = employee.get("name") or employee.get("id")
        previous = old.get(employee.get("id")) or {}
        mode = employee.get("permissionMode")
        if mode in DANGEROUS_PERMISSION_MODES and mode != previous.get("permissionMode"):
            danger.append(f"run {name} without a sandbox or approvals ({mode})")
        if set(employee.get("secretRefs") or []) - set(previous.get("secretRefs") or []):
            danger.append(f"give {name} credentials")
        grants, old_grants = employee.get("workspacePaths"), previous.get("workspacePaths", [])
        if previous and (
            (grants is None and old_grants is not None) or set(grants or []) - set(old_grants or [])
        ):
            danger.append(f"let {name} work in more folders")
        servers = {
            json.dumps([s.get(k) for k in ("type", "url", "command", "args")])
            for s in (employee.get("resources") or {}).get("mcpServers", [])
            if s.get("enabled", True)
        }
        old_servers = {
            json.dumps([s.get(k) for k in ("type", "url", "command", "args")])
            for s in (previous.get("resources") or {}).get("mcpServers", [])
            if s.get("enabled", True)
        }
        if servers - old_servers:
            danger.append(f"give {name} a new tool server that can reach outside services")
        if previous and _loosened(previous, employee, OPEN_ENDED_LIMITS):
            user_only.append(f"raise or remove {name}'s monthly limits")
    if _loosened(before, after, OPEN_ENDED_LIMITS):
        user_only.append("raise or remove the organization's monthly budget or run limits")
    if any((after.get(k) or 0) > (before.get(k) or 0) for k in COMPANY_CAPS if k in before):
        user_only.append("raise run limits")
    initiatives = {p.get("id"): p for p in before.get("projects", [])}
    if any(
        _loosened(initiatives.get(p.get("id"), {}), p, OPEN_ENDED_LIMITS)
        for p in after.get("projects", [])
    ):
        user_only.append("raise or remove initiative limits")
    if set(after.get("projectRoots", [])) - set(before.get("projectRoots", [])):
        danger.append("add project folders")
    targets = {t.get("id"): t for t in before.get("executionTargets", [])}
    if any(targets.get(t.get("id")) != t for t in after.get("executionTargets", [])):
        danger.append("add or change places outside this computer where work runs")
    if any(
        c not in (initiatives.get(p.get("id"), {}).get("checks") or [])
        for p in after.get("projects", [])
        for c in p.get("checks") or []
    ) or any(
        p.get("autoRunChecks") and not initiatives.get(p.get("id"), {}).get("autoRunChecks")
        for p in after.get("projects", [])
    ):
        danger.append("run project check commands outside the sandbox")
    if before.get("blockGitPush", True) and after.get("blockGitPush", True) is False:
        danger.append("allow git push")
    # The organization's safety policy: its own switches stay the user's.
    if bool(after.get("autoMode")) != bool(before.get("autoMode")):
        user_only.append("turn Auto mode on or off")
    if after.get("blockGitPush", True) != before.get("blockGitPush", True):
        user_only.append("change the git push policy")
    if after.get("workspaceIsolation", "worktree") != before.get("workspaceIsolation", "worktree"):
        user_only.append("change how workers' folders are isolated")
    return danger, user_only


def text_hints(text: str) -> list[str]:
    return [label for pattern, label in HINTS if re.search(pattern, text, re.IGNORECASE)]


def assess(
    manager: OrganizationManager, project: str, org: dict[str, Any], request: dict[str, Any]
) -> dict[str, list[str]]:
    """What Raticode itself finds in a request: danger, user-only parts and text hints.

    ``danger`` and ``userOnly`` come from the request's structure. ``hints`` are words
    worth a careful read, such as "brokerage" in a ticket, which may well be negated.
    """
    from gofer.ui.organizations import starting_risks

    action = request.get("action")
    params = request.get("params") or {}
    danger: list[str] = []
    user_only: list[str] = []
    text = [str(request.get("summary") or "")]
    if action in {"configure", "restore"}:
        after = requested_config(manager, project, org, request)
        if after is None:
            danger.append("a configuration Raticode can't read")
        else:
            found, only = config_risks(org["config"], after)
            danger += found
            user_only += only
    elif action == "control" and params.get("state") == "running":
        risky = [
            part
            for part in starting_risks(org["config"]).split("; ")
            if part
            and part not in {"keeps running after restarts", "edits the project folder directly"}
        ]
        danger += [f"start with settings that {part}" for part in risky]
    elif action == "secret_reference":
        danger.append("add or change a credential")
    elif action == "webhook_save" and params.get("enabled"):
        danger.append("let an outside service trigger work")
    elif action == "execution_resolve":
        danger.append("confirm that outside work stopped, which Rem can't inspect")
    elif action == "archive" and params.get("archived", True):
        pass
    else:
        payload = {k: v for k, v in params.items() if k not in {"config"}}
        text.append(json.dumps(payload, default=str)[:20000])
    task = _task(org, request)
    if task:
        text += [task.get("title", ""), task.get("description", ""), task.get("doneWhen", "")]
    return {
        "danger": list(dict.fromkeys(danger)),
        "userOnly": list(dict.fromkeys(user_only)),
        "hints": text_hints("\n".join(str(t) for t in text)),
    }


# Asking Rem ------------------------------------------------------------------------------

RULES = """You are Rem, reviewing a request for an organization whose user turned on Auto mode.
The user is not watching; you decide in their place. Read the request in context: what it
changes, the revision it expects, its risk, and the organization's charter and instructions.

Reply with ONLY one JSON object:
{"summary": "<one short line>", "decision": "approve" | "modify" | "decline" | "halt",
 "reason": "<why, in one or two plain sentences>",
 "changes": [{"op": "replace" | "remove", "path": "/json/pointer", "value": ...}],
 "ticketChanges": {"title"?, "description"?, "doneWhen"?, "priority"?, "assignee"?},
 "recommendation": "<a safer change to make instead, when declining>",
 "hintsCleared": "<when approving text Raticode flagged: why none of it happens>",
 "halt": {"kind": "dangerous" | "impossible", "category": "<see below>",
          "userAction": "<exactly what the user must do to continue>",
          "whyNotFixable": "<for dangerous: why declining or modifying can't fix it>"}}

How to decide:
- approve when the request is reasonable for the organization's charter. Uncertainty is
  not a reason to stop: decide, or recommend a change.
- modify to approve a safer version. "changes" edits the request's params (JSON pointers
  into params, e.g. /config/employees/0/permissionMode). "ticketChanges" edits the ticket
  before its plan is approved (only for plan approvals and proposals). Never change
  expectedRevision or IDs.
- decline with a reason, and a "recommendation" when a safer alternative exists (a
  sandboxed or read-only mode, dropping the dangerous part, another ticket or worker).
- halt ONLY when (a) the action is dangerous AND neither declining nor modifying it can
  stop the danger without stopping the whole organization, or (b) going on without the
  user is literally impossible. Categories for impossible: missing_credentials, hardware,
  internet, human_sign_in. For dangerous use the danger Raticode found. A dangerous request
  you can decline or make safe is NOT a reason to halt: decline or modify it, and the
  organization keeps working.
- Dangerous means: live trading, real money, any brokerage or external trading API,
  running workers without a sandbox or with full access, adding or changing credentials,
  git push, publishing or deploying, irreversible deletion, widening project folders or
  access, or spending beyond the configured budget. Raticode lists what it found under
  "dangerFound"; a request with dangerFound can't be approved as asked.
- "wordsWorthChecking" lists risky words Raticode found in the text. Text often forbids
  them ("paper only, no live trading"). To approve or modify a request whose text mentions
  live trading, a brokerage, real money, deploying, publishing, deletion or spending, give
  "hintsCleared" saying why none of that happens; otherwise decline or modify it.
- Plans and proposals: approving lets workers run that ticket. Reviews: approving accepts
  the result as done; declining sends it back to its worker with your reason.
"""


def _brief(org: dict[str, Any], request: dict[str, Any], found: dict[str, list[str]]) -> str:
    config = org["config"]
    task = _task(org, request)
    params = request.get("params") or {}
    shown = {k: v for k, v in params.items() if k != "config"}
    context: dict[str, Any] = {
        "organization": {
            "name": config.get("name"),
            "mission": config.get("mission"),
            "description": str(config.get("description") or "")[:2000],
            "instructions": str(config.get("instructions") or "")[:6000],
            "revision": org["revision"],
            "state": org["runtime"].get("state"),
            "monthlyBudgetUsd": config.get("monthlyBudgetUsd"),
            "monthlyTurnLimit": config.get("monthlyTurnLimit"),
            "employees": [
                {
                    "id": e.get("id"),
                    "name": e.get("name"),
                    "role": e.get("role") or e.get("title"),
                    "permissionMode": e.get("permissionMode"),
                    "paused": e.get("paused"),
                }
                for e in config.get("employees", [])
            ],
        },
        "request": {
            "kind": request.get("kind") or "consent",
            "action": request.get("action"),
            "summary": request.get("summary"),
            "askedBy": request.get("actor"),
            "params": json.dumps(shown, default=str)[:12000],
        },
        "dangerFound": found["danger"],
        "wordsWorthChecking": found["hints"],
    }
    if request.get("action") in {"configure", "restore"}:
        from gofer.ui.organization_store import config_diff

        after = params.get("config")
        if isinstance(after, dict):
            context["request"]["configChanges"] = config_diff(config, after)[:80]
            context["request"]["employeeIndexes"] = {
                e.get("id"): i for i, e in enumerate(after.get("employees", []))
            }
    if task:
        context["ticket"] = {
            "key": task.get("key") or task.get("number"),
            "title": task.get("title"),
            "description": str(task.get("description") or "")[:6000],
            "doneWhen": task.get("doneWhen"),
            "status": task.get("status"),
            "assignee": task.get("assignee"),
            "revision": task.get("revision"),
            "evidence": str(task.get("evidence") or "")[:4000],
            "lastComments": [
                {"actor": c.get("actor"), "body": str(c.get("body") or "")[:1500]}
                for c in (task.get("comments") or [])[-4:]
            ],
        }
    return RULES + "\nThe request and its context:\n" + json.dumps(context, indent=1)


def _patch(params: dict[str, Any], changes: Any) -> dict[str, Any]:
    """Apply JSON-pointer replace/remove operations to a copy of ``params``."""
    if not isinstance(changes, list) or not changes or len(changes) > 30:
        raise ValueError("changes must be a list of 1 to 30 operations")
    result = copy.deepcopy(params)
    for change in changes:
        if not isinstance(change, dict) or change.get("op") not in {"replace", "remove"}:
            raise ValueError("Each change needs op replace or remove")
        path = change.get("path")
        if not isinstance(path, str) or not path.startswith("/") or PROTECTED.match(path):
            raise ValueError(f"Rem can't change {path!r}")
        parts = [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
        target: Any = result
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        last = parts[-1]
        if isinstance(target, list):
            index = int(last)
            if change["op"] == "remove":
                target.pop(index)
            else:
                target[index] = change.get("value")
        elif isinstance(target, dict):
            if change["op"] == "remove":
                target.pop(last)
            elif last not in target:
                raise ValueError(f"{path} doesn't exist; Rem can only replace or remove")
            else:
                target[last] = change.get("value")
        else:
            raise ValueError(f"{path} doesn't point into the request")
    return result


def check_decision(
    manager: OrganizationManager,
    project: str,
    org: dict[str, Any],
    request: dict[str, Any],
    found: dict[str, list[str]],
    raw: Any,
) -> dict[str, Any]:
    """Validate Rem's answer and return the decision to apply. Raises ValueError."""
    if not isinstance(raw, dict) or raw.get("decision") not in DECISIONS:
        raise ValueError("decision must be approve, modify, decline or halt")
    reason = str(raw.get("reason") or "").strip()
    if not reason:
        raise ValueError("Give a reason")
    decision: dict[str, Any] = {
        "decision": raw["decision"],
        "reason": reason[:1500],
        "summary": str(raw.get("summary") or "")[:300],
        "recommendation": str(raw.get("recommendation") or "").strip()[:1500],
    }
    kind = request.get("kind") or "consent"
    if decision["decision"] == "approve" and found["danger"]:
        raise ValueError(
            "This request is dangerous (" + "; ".join(found["danger"]) + "), so it can't be "
            "approved as asked. Modify it to a safe version, decline it, or halt only if "
            "nothing else can stop the danger."
        )
    if decision["decision"] == "approve" and (cleared := _cleared(raw, found["hints"])):
        decision["hintsCleared"] = cleared
    if decision["decision"] == "modify":
        if raw.get("ticketChanges"):
            changes = raw["ticketChanges"]
            if kind != "plan" or not isinstance(changes, dict) or set(changes) - TICKET_FIELDS:
                raise ValueError(
                    "ticketChanges only apply to plan approvals, with title, description, "
                    "doneWhen, priority or assignee"
                )
            decision["ticketChanges"] = changes
            task = _task(org, request) or {}
            text = "\n".join(str(changes.get(k, task.get(k, ""))) for k in TICKET_FIELDS)
            if found["danger"]:
                raise ValueError("Ticket edits can't make this request safe; decline or halt")
            decision["hints"] = text_hints(text)
            if cleared := _cleared(raw, decision["hints"]):
                decision["hintsCleared"] = cleared
        elif raw.get("changes"):
            if kind != "consent" or request.get("action") not in {
                "configure",
                "task_create",
                "task_update",
                "routine_save",
                "webhook_save",
                "control",
            }:
                raise ValueError("This kind of request can't be modified; approve or decline it")
            params = _patch(request.get("params") or {}, raw["changes"])
            again = assess(manager, project, org, {**request, "params": params})
            if again["danger"] or again["userOnly"]:
                raise ValueError(
                    "The modified request is still dangerous or needs the user: "
                    + "; ".join(again["danger"] + again["userOnly"])
                )
            if cleared := _cleared(raw, again["hints"]):
                decision["hintsCleared"] = cleared
            decision["changes"] = raw["changes"]
            decision["params"] = params
        else:
            raise ValueError("modify needs changes or ticketChanges")
    if decision["decision"] == "halt":
        given = raw.get("halt")
        halt: dict[str, Any] = given if isinstance(given, dict) else {}
        action = str(halt.get("userAction") or "").strip()
        if not action:
            raise ValueError("A halt must say exactly what the user must do to continue")
        if halt.get("kind") == "impossible":
            if halt.get("category") not in IMPOSSIBLE:
                raise ValueError("An impossible halt needs category " + ", ".join(IMPOSSIBLE))
        elif halt.get("kind") == "dangerous":
            if not (found["danger"] or found["hints"]):
                raise ValueError(
                    "Raticode found nothing dangerous in this request, so it can't halt the "
                    "organization. Approve, modify or decline it."
                )
            if not str(halt.get("whyNotFixable") or "").strip():
                raise ValueError(
                    "Say why declining or modifying can't fix it; otherwise decline or modify"
                )
        else:
            raise ValueError("halt.kind must be dangerous or impossible")
        decision["halt"] = {
            "kind": halt["kind"],
            "category": str(halt.get("category") or halt["kind"])[:60],
            "userAction": action[:600],
            "whyNotFixable": str(halt.get("whyNotFixable") or "")[:800],
        }
    return decision


Reviewer = Callable[[str, threading.Event], Any]


def rem_reviewer(config: Callable[[], dict[str, Any]], data_dir: Path) -> Reviewer:
    """Rem's own chat model as the review step: tool-free and sessionless."""

    def review(prompt: str, cancel: threading.Event) -> Any:
        from gofer.ui.rem_home_models import chat_config
        from gofer.ui.rem_home_provider import generate_decision

        return generate_decision(prompt, chat_config(config()), cancel, data_dir)

    return review


def ask_rem(
    reviewer: Reviewer | None,
    prompt: str,
    cancel: threading.Event,
    validate: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Ask Rem, giving one more try with the reason when its answer can't be used.

    Provider failures raise. An answer that still can't be used becomes a decline, so
    nothing unchecked happens and the organization keeps working.
    """
    from gofer.ui.rem_home_provider import MalformedDecision

    if reviewer is None:
        raise RuntimeError("Rem isn't available to review requests")
    error = ""
    for attempt in range(2):
        try:
            raw = reviewer(prompt if not error else f"{prompt}\n\n{error}", cancel)
        except MalformedDecision:
            raw = None
        if cancel.is_set():
            raise InterruptedError("Stopped")
        try:
            return validate(raw)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            error = (
                f"Your previous answer couldn't be used: {exc}. Reply again with ONLY the "
                "JSON object."
            )
            log.info("Rem's auto-mode answer was not usable (attempt %d): %s", attempt + 1, exc)
    return {
        "decision": "decline",
        "reason": "Rem's review didn't give a decision Raticode could check and apply, so it "
        "declined to keep the organization safe. Ask again if it's still needed.",
        "summary": "",
        "recommendation": "",
        "fallback": True,
    }


# Requests from tickets -------------------------------------------------------------------


def queue_ticket_approvals(manager: OrganizationManager, project: str, oid: str) -> int:
    """Turn plans, proposals and reviews waiting for the user into requests for Rem."""
    from gofer.ui.organization_backlog import proposal_waiting

    made: list[str] = []

    def queue(current: dict[str, Any]) -> dict[str, Any]:
        if not auto_on(current):
            return {}
        requests = current["runtime"].setdefault("consentRequests", {})
        reviewed = current["runtime"].setdefault("autoReviewed", {})
        employees = {e["id"] for e in current["config"]["employees"]}
        asked = {
            (r.get("params") or {}).get("taskId")
            for r in requests.values()
            if r.get("status") in {"pending", "applying"}
        }
        pending = sum(r.get("status") == "pending" for r in requests.values())
        for task in current["runtime"]["tasks"]:
            if len(made) >= MAX_QUEUED or pending + len(made) >= 20:
                break
            if task["id"] in asked or reviewed.get(task["id"]) == task["revision"]:
                continue
            kind = action = summary = ""
            if task["status"] in {"todo", "backlog"} and not task.get("claim"):
                waiting = proposal_waiting(current, task)
                if waiting.startswith("Waiting for plan"):
                    kind, action = "plan", "approve"
                    summary = f"Approve the plan for “{task['title']}”"
                elif waiting.startswith("Proposed by a worker"):
                    kind, action = "plan", "approve"
                    summary = f"Accept the proposal “{task['title']}”"
            elif (
                task["status"] == "in_review"
                and not task.get("claim")
                and task.get("reviewer") not in employees
            ):
                kind, action = "review", "review"
                summary = f"Accept the result of “{task['title']}”"
            if not kind:
                continue
            params: dict[str, Any] = {"taskId": task["id"], "expectedRevision": task["revision"]}
            if action == "review":
                params.update(decision="accept", body="")
            rid = f"auto-{task['id'][:8]}-{task['revision']}-{len(requests)}"
            requests[rid] = {
                "id": rid,
                "action": action,
                "kind": kind,
                "params": params,
                "actor": "system",
                "at": now(),
                "summary": summary,
                "status": "pending",
                "route": "rem",
                "fingerprint": json.dumps([action, params], sort_keys=True),
            }
            made.append(rid)
        return (
            {"requestIds": made, "reason": "Auto mode sent waiting tickets to Rem"} if made else {}
        )

    manager.store.mutate(project, oid, "system", "auto_review_queued", queue)
    return len(made)


# Applying decisions ----------------------------------------------------------------------


def _remember(current: dict[str, Any], entry: dict[str, Any]) -> None:
    log_ = current["runtime"].setdefault("autoDecisions", [])
    log_[:] = [e for e in log_ if e.get("requestId") != entry["requestId"]]
    log_.append(entry)
    del log_[:-MAX_DECISIONS]


def _record(
    manager: OrganizationManager,
    project: str,
    oid: str,
    request: dict[str, Any],
    decision: dict[str, Any],
    *,
    applied: bool,
    error: str = "",
    actor: str = AUTO_ACTOR,
) -> dict[str, Any]:
    entry = {
        "requestId": request["id"],
        "at": now(),
        "summary": request.get("summary", ""),
        "action": request.get("action"),
        "kind": request.get("kind") or "consent",
        "decision": decision["decision"],
        "reason": decision.get("reason", ""),
        "recommendation": decision.get("recommendation", ""),
        **({"hintsCleared": decision["hintsCleared"]} if decision.get("hintsCleared") else {}),
        "applied": applied,
        "error": error,
        **({"halt": decision["halt"]} if decision.get("halt") else {}),
        **({"changes": decision["changes"]} if decision.get("changes") else {}),
        **({"ticketChanges": decision["ticketChanges"]} if decision.get("ticketChanges") else {}),
        "by": "Rem (Auto mode)",
    }

    def save(current: dict[str, Any]) -> dict[str, Any]:
        _remember(current, entry)
        item = (current["runtime"].get("consentRequests") or {}).get(request["id"])
        if item is not None:
            item.setdefault("autoReview", {}).update(
                state="halted" if decision["decision"] == "halt" else "done",
                decision={k: v for k, v in decision.items() if k != "params"},
            )
        return {"auto": entry}

    manager.store.mutate(project, oid, actor, "auto_decision", save)
    return entry


def _mark_reviewed(manager: OrganizationManager, project: str, oid: str, task_id: str) -> None:
    def mark(current: dict[str, Any]) -> dict[str, Any]:
        task = next((t for t in current["runtime"]["tasks"] if t["id"] == task_id), None)
        if task:
            current["runtime"].setdefault("autoReviewed", {})[task_id] = task["revision"]
        return {}

    manager.store.mutate(project, oid, AUTO_ACTOR, "auto_review_marked", mark)


def _note_on_ticket(
    manager: OrganizationManager, project: str, oid: str, task_id: str, body: str
) -> None:
    try:
        manager.call(
            project,
            AUTO_ACTOR,
            {
                "action": "comment",
                "organizationId": oid,
                "params": {"taskId": task_id, "body": body},
            },
        )
    except ValueError:
        log.info("Couldn't comment on ticket %s for Auto mode", task_id)


def _halt(
    manager: OrganizationManager,
    project: str,
    oid: str,
    request: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    halt = decision["halt"]
    reason = decision["reason"]
    what = (
        IMPOSSIBLE.get(halt.get("category", ""), "")
        if halt["kind"] == "impossible"
        else "it's dangerous and Rem couldn't make it safe without stopping everything"
    )

    def stop(current: dict[str, Any]) -> dict[str, Any]:
        runtime = current["runtime"]
        item = (runtime.get("consentRequests") or {}).get(request["id"])
        if item is not None:
            item["route"] = "user"
        was_running = runtime["state"] == "running"
        runtime["autoHalt"] = {
            "at": now(),
            "requestId": request["id"],
            "summary": request.get("summary", ""),
            "kind": halt["kind"],
            "category": halt.get("category", ""),
            "reason": reason,
            "userAction": halt["userAction"],
            "resume": was_running,
        }
        if was_running:
            runtime["generation"] += 1
        runtime["state"] = "paused"
        runtime["pauseReason"] = (
            f"Auto mode stopped the organization: “{request.get('summary', '')}” needs "
            f"you because {what}. {reason} To continue: {halt['userAction']}"
        )
        return {"requestId": request["id"], "halt": runtime["autoHalt"]}

    manager.store.mutate(project, oid, AUTO_ACTOR, "auto_mode_halted", stop)
    manager.cancel(oid, "paused")
    return _record(manager, project, oid, request, decision, applied=False)


def apply_decision(
    manager: OrganizationManager,
    project: str,
    oid: str,
    request: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    from gofer.ui.organization_store import OrganizationConflict

    rid = request["id"]
    kind = request.get("kind") or "consent"
    task_id = (request.get("params") or {}).get("taskId")
    choice = decision["decision"]
    if choice == "halt":
        return _halt(manager, project, oid, request, decision)
    org = manager.store.get(project, oid)
    note = f"{decision['reason']}" + (
        f" Recommended instead: {decision['recommendation']}"
        if decision.get("recommendation")
        else ""
    )
    if choice == "decline":
        error = ""
        try:
            manager._decide(
                project,
                org,
                {"requestId": rid, "decision": "decline"},
                decider=AUTO_ACTOR,
                note=note,
            )
        except ValueError as exc:
            error = str(exc)
        if not error and kind == "review" and task_id:
            # Declining a result sends it back to its worker with the reason.
            try:
                task = next(
                    t
                    for t in manager.store.get(project, oid)["runtime"]["tasks"]
                    if t["id"] == task_id
                )
                manager.call(
                    project,
                    AUTO_ACTOR,
                    {
                        "action": "review",
                        "organizationId": oid,
                        "params": {
                            "taskId": task_id,
                            "expectedRevision": task["revision"],
                            "decision": "revise",
                            "body": f"Rem (Auto mode) sent this back: {note}",
                        },
                    },
                    consented=True,
                )
            except (ValueError, StopIteration) as exc:
                error = str(exc)
        elif not error and task_id:
            _note_on_ticket(
                manager,
                project,
                oid,
                task_id,
                f"Rem (Auto mode) declined “{request.get('summary', '')}”: {note}",
            )
        if not error and kind == "plan" and task_id:
            _mark_reviewed(manager, project, oid, task_id)
        return _record(manager, project, oid, request, decision, applied=not error, error=error)
    override: dict[str, Any] | None = decision.get("params")
    error = ""
    try:
        if decision.get("ticketChanges") and task_id:
            task = next(t for t in org["runtime"]["tasks"] if t["id"] == task_id)
            updated = manager.call(
                project,
                AUTO_ACTOR,
                {
                    "action": "task_update",
                    "organizationId": oid,
                    "params": {
                        "taskId": task_id,
                        "expectedRevision": task["revision"],
                        "changes": decision["ticketChanges"],
                    },
                },
                consented=True,
            )
            revision = (updated or {}).get("revision") if isinstance(updated, dict) else None
            if not isinstance(revision, int):
                fresh = manager.store.get(project, oid)
                revision = next(t for t in fresh["runtime"]["tasks"] if t["id"] == task_id)[
                    "revision"
                ]
            override = {**(request.get("params") or {}), "expectedRevision": revision}
        if kind == "review":
            override = {
                **(override or request.get("params") or {}),
                "body": f"Rem (Auto mode): {note}",
            }
        manager._decide(
            project,
            manager.store.get(project, oid),
            {"requestId": rid, "decision": "approve"},
            decider=AUTO_ACTOR,
            note=note,
            override=override,
        )
    except (OrganizationConflict, ValueError, StopIteration) as exc:
        error = str(exc)
    return _record(manager, project, oid, request, decision, applied=not error, error=error)


def _leave_for_user(
    manager: OrganizationManager, project: str, oid: str, request: dict[str, Any], why: str
) -> dict[str, Any]:
    def route(current: dict[str, Any]) -> dict[str, Any]:
        item = (current["runtime"].get("consentRequests") or {}).get(request["id"])
        if item is None or item.get("status") != "pending":
            return {}
        item["route"] = "user"
        item.setdefault("autoReview", {}).update(state="user_only", reason=why)
        return {}

    manager.store.mutate(project, oid, AUTO_ACTOR, "auto_review_routed", route)
    return _record(
        manager,
        project,
        oid,
        request,
        {"decision": "left_for_you", "reason": why, "recommendation": ""},
        applied=False,
    )


def _failed(
    manager: OrganizationManager,
    project: str,
    oid: str,
    request: dict[str, Any],
    exc: Exception,
) -> dict[str, Any] | None:
    """Count a failed review; after MAX_FAILURES in a row, halt as impossible."""
    failures: list[int] = []

    def count(current: dict[str, Any]) -> dict[str, Any]:
        item = (current["runtime"].get("consentRequests") or {}).get(request["id"])
        if item is None:
            return {}
        review = item.setdefault("autoReview", {})
        review["failures"] = int(review.get("failures") or 0) + 1
        delay = RETRY_SECONDS[min(review["failures"] - 1, len(RETRY_SECONDS) - 1)]
        review["retryAt"] = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        review["error"] = str(exc)[:500]
        failures.append(review["failures"])
        return {}

    manager.store.mutate(project, oid, AUTO_ACTOR, "auto_review_failed", count)
    if failures and failures[0] >= MAX_FAILURES:
        return _halt(
            manager,
            project,
            oid,
            request,
            {
                "decision": "halt",
                "reason": f"Rem couldn't review it {failures[0]} times in a row: {str(exc)[:300]}",
                "recommendation": "",
                "halt": {
                    "kind": "impossible",
                    "category": "rem_unavailable",
                    "userAction": "Check that Rem's model in Settings > Rem is signed in and "
                    "online, then approve or decline this request yourself, or start the "
                    "organization again.",
                },
            },
        )
    return None


def review_one(
    manager: OrganizationManager,
    project: str,
    oid: str,
    request: dict[str, Any],
    cancel: threading.Event,
) -> dict[str, Any] | None:
    org = manager.store.get(project, oid)
    live = (org["runtime"].get("consentRequests") or {}).get(request["id"])
    if not live or not waits_on_rem(org, live):
        return None
    reason = conflict(org, live)
    if reason:
        # It can't apply as asked; closing it lets it be asked again on fresh data.
        return apply_decision(
            manager,
            project,
            oid,
            live,
            {
                "decision": "decline",
                "reason": reason.replace(
                    " Left waiting: decline it and ask Rem again.", ""
                ).replace(" Left waiting: decline it.", "")
                + " Closed so it can be asked again.",
                "recommendation": "",
            },
        )
    found = assess(manager, project, org, live)
    if found["userOnly"]:
        return _leave_for_user(
            manager,
            project,
            oid,
            live,
            "Only you can " + "; ".join(found["userOnly"]) + ". The organization keeps "
            "working while it waits for you.",
        )
    saved = (live.get("autoReview") or {}).get("decision")
    if saved and (live.get("autoReview") or {}).get("state") == "decided":
        # Decided before a restart: apply that decision without asking again.
        decision = dict(saved)
        if decision.get("changes"):
            decision["params"] = _patch(live.get("params") or {}, decision["changes"])
        return apply_decision(manager, project, oid, live, decision)
    retry = (live.get("autoReview") or {}).get("retryAt")
    if retry and datetime.fromisoformat(retry) > datetime.now(UTC):
        return None
    try:
        decision = ask_rem(
            manager.auto_review,
            _brief(org, live, found),
            cancel,
            lambda raw: check_decision(manager, project, org, live, found, raw),
        )
    except InterruptedError:
        return None
    except Exception as exc:  # provider failures, missing Rem
        log.info("Auto-mode review failed for %s: %s", live["id"], exc)
        return _failed(manager, project, oid, live, exc)

    def keep(current: dict[str, Any]) -> dict[str, Any]:
        item = (current["runtime"].get("consentRequests") or {}).get(live["id"])
        if item is None or item.get("status") != "pending":
            return {}
        item.setdefault("autoReview", {}).update(
            state="decided", decision={k: v for k, v in decision.items() if k != "params"}
        )
        return {}

    manager.store.mutate(project, oid, AUTO_ACTOR, "auto_review_decided", keep)
    return apply_decision(manager, project, oid, live, decision)


def review_pass(
    manager: OrganizationManager, project: str, oid: str, cancel: threading.Event | None = None
) -> list[dict[str, Any]]:
    """Decide every request waiting on Rem in one organization, in the safe order."""
    from gofer.ui.organizations import CONSENT_TTL

    cancel = cancel or threading.Event()
    org = manager.store.get(project, oid)
    if not auto_on(org) or org["runtime"].get("archived"):
        return []
    queue_ticket_approvals(manager, project, oid)
    org = manager.store.get(project, oid)
    waiting = [
        r
        for r in (org["runtime"].get("consentRequests") or {}).values()
        if waits_on_rem(org, r) and _age_ok(r, CONSENT_TTL.total_seconds())
    ]
    results = []
    for request in approval_order(waiting):
        if cancel.is_set():
            break
        result = review_one(manager, project, oid, request, cancel)
        if result:
            results.append(result)
            if result["decision"] == "halt":
                break
    return results


def needs_review(org: dict[str, Any]) -> bool:
    """Whether an organization may have something for Rem (cheap check for the loop)."""
    from gofer.ui.organization_backlog import proposal_waiting

    if not auto_on(org) or org["runtime"].get("archived"):
        return False
    if any(waits_on_rem(org, r) for r in (org["runtime"].get("consentRequests") or {}).values()):
        return True
    reviewed = org["runtime"].get("autoReviewed") or {}
    employees = {e["id"] for e in org["config"]["employees"]}
    return any(
        reviewed.get(t["id"]) != t["revision"]
        and (
            (
                t["status"] in {"todo", "backlog"}
                and proposal_waiting(org, t).startswith(("Waiting for plan", "Proposed by"))
            )
            or (
                t["status"] == "in_review"
                and not t.get("claim")
                and t.get("reviewer") not in employees
            )
        )
        for t in org["runtime"]["tasks"]
    )


# Turning Auto mode on or off -------------------------------------------------------------


def set_auto_mode(
    manager: OrganizationManager, project: str, org: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    """The user's own switch. Turning it on needs confirm: true."""
    enabled = params.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    if enabled and params.get("confirm") is not True:
        raise ValueError(
            "Turning on Auto mode lets Rem approve, change or decline this organization's "
            "requests instead of you. Confirm with confirm: true."
        )
    expected = params.get("expectedRevision", org["revision"])
    config = copy.deepcopy(org["config"])
    if bool(config.get("autoMode")) != enabled:
        config["autoMode"] = enabled
        with manager._lock:
            manager.store.configure(
                project,
                org["id"],
                config,
                expected,
                "user",
                "Turned Auto mode on" if enabled else "Turned Auto mode off",
            )

    def reset(current: dict[str, Any]) -> dict[str, Any]:
        runtime = current["runtime"]
        halted = runtime.pop("autoHalt", None)
        for request in (runtime.get("consentRequests") or {}).values():
            if (
                enabled
                and request.get("status") == "pending"
                and (request.get("autoReview") or {}).get("state") not in {"user_only"}
            ):
                request["route"] = "rem"
        return {"autoMode": enabled, **({"clearedHalt": halted} if halted else {})}

    manager.store.mutate(project, org["id"], "user", "auto_mode_set", reset)
    manager._wake.set()
    return manager.public(manager.store.get(project, org["id"]))


# Approving on the user's chat instruction ------------------------------------------------


def approve_on_instruction(
    manager: OrganizationManager,
    oid: str,
    request_ids: list[str] | None,
    *,
    message: dict[str, Any],
    reason: str = "",
) -> dict[str, Any]:
    """Approve requests because the user told Rem to in their own chat message.

    ``message`` is the user's chat message Rem is answering: its ``id``, ``body`` and
    ``created_at``. Only Rem's chat turn for a message the user typed (on the computer or
    the phone) calls this; no tool, token, worker or API action reaches it. With no IDs,
    every request asked before the message was sent is approved. The order, revision
    checks and per-item results are Approve all's.

    Whether the message approves is Rem's judgment, not a word list: the user's okay can be
    implied when the conversation makes it painfully obvious. This only checks that a real
    user message is behind it, and records Rem's ``reason`` with each request.
    """
    from gofer.ui.organizations import CONSENT_TTL

    if not str(message.get("id") or "").strip() or not str(message.get("body") or "").strip():
        raise ValueError("Only the user's own chat message can approve, so nothing was approved")
    org = manager.store.get("", oid)
    pending = {
        rid: r
        for rid, r in (org["runtime"].get("consentRequests") or {}).items()
        if r.get("status") == "pending"
    }
    sent = str(message.get("created_at") or "")

    def before_message(rid: str) -> bool:
        request = pending.get(rid)
        return request is None or not sent or str(request.get("at")) <= sent

    named = list(dict.fromkeys(request_ids or [r for r in pending]))
    # A request Rem asked for after the user's message isn't what the user approved.
    later = [rid for rid in named if not before_message(rid)]
    ids = [rid for rid in named if before_message(rid)][:20]
    if not ids and not later:
        raise ValueError("Nothing in this organization was waiting for approval")
    result: dict[str, Any] = (
        approve_all(
            manager,
            org["projectRoot"],
            oid,
            ids,
            ttl_seconds=CONSENT_TTL.total_seconds(),
            decider=INSTRUCTED_ACTOR,
            instruction={
                "messageId": str(message.get("id") or ""),
                "at": sent,
                **({"reason": reason.strip()[:300]} if reason.strip() else {}),
            },
        )
        if ids
        else {"results": [], "approved": 0, "organization": manager.public(org)}
    )
    result["results"] += [
        {
            "requestId": rid,
            "summary": pending[rid].get("summary", ""),
            "status": "skipped",
            "message": "Rem asked for it after your message, so it keeps waiting.",
        }
        for rid in later
    ]
    return result
