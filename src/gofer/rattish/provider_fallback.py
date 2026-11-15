"""Provider availability errors and the fallback chain of a model step.

An Agent or Common LLM Task node may list fallback providers. After its retries, the
step moves to the next entry only when the current one failed because the provider
itself could not serve the call:

- ``rate_limit``: a rate limit or an exhausted plan allowance or quota;
- ``overloaded``: the service is overloaded or temporarily unavailable;
- ``signed_out``: the provider's sign-in expired or its credentials were rejected;
- ``not_installed``: the provider's CLI is not installed on this computer.

Task failures, structured-output repair failures and usage-limit (budget) stops never
fall back. Handlers record the reason in ``details["availability"]`` of a ``provider``
error; :func:`fallback_reason` is the only place that decides whether a failure may
switch providers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from gofer.rattish.runtime import RuntimeErrorInfo

AvailabilityReason = Literal["rate_limit", "overloaded", "signed_out", "not_installed"]
AVAILABILITY_REASONS: tuple[AvailabilityReason, ...] = (
    "rate_limit",
    "overloaded",
    "signed_out",
    "not_installed",
)
# Short labels the Runs view, the CLI and the phone show for each reason.
REASON_LABELS: dict[str, str] = {
    "rate_limit": "rate limit or allowance used up",
    "overloaded": "service overloaded",
    "signed_out": "sign-in expired",
    "not_installed": "CLI not installed",
}

# Only the start and end of a failed call's text are read: provider errors are short
# and sit at either end, and the middle of a long transcript is the task's own text.
_SCAN_CHARS = 4000

_SIGNED_OUT = re.compile(
    r"not (?:logged|signed) in|please (?:run )?/?log ?in|log ?in (?:again|required)|"
    r"sign ?in (?:again|required)|re-?authenticate|authentication (?:failed|required|error)|"
    r"(?:oauth |access |auth )?token (?:has )?(?:expired|been revoked)|session (?:has )?expired|"
    r"invalid (?:api[ _-]?key|x-api-key|credentials|bearer token)|\bunauthori[sz]ed\b|"
    r"\b401\b|credentials? (?:have |has )?(?:expired|are invalid|is invalid)",
    re.IGNORECASE,
)
_RATE_LIMIT = re.compile(
    r"rate.?limit|too many requests|\b429\b|usage limit|quota (?:exceeded|exhausted)|"
    r"exceeded your (?:current )?quota|insufficient_quota|credit balance is too low|"
    r"out of credits|(?:hit|reached) (?:your |the )?(?:usage |weekly |daily |session )?limit|"
    r"limit (?:reached|exceeded)|allowance (?:is )?(?:used up|exhausted|exceeded)",
    re.IGNORECASE,
)
_OVERLOADED = re.compile(
    r"overloaded|at capacity|\b(?:502|503|504|529)\b|service unavailable|bad gateway|"
    r"gateway time-?out|temporarily unavailable|high demand|server is busy",
    re.IGNORECASE,
)


def availability_reason(
    text: str | None, exit_code: int | None = None
) -> AvailabilityReason | None:
    """The availability reason a failed provider call's text shows, or None.

    Call this only for a provider call that failed. A successful call's output is the
    task's answer, which may mention rate limits or sign-in for its own reasons.
    """
    if exit_code == 127:
        return "not_installed"
    if not text:
        return None
    sample = text if len(text) <= 2 * _SCAN_CHARS else text[:_SCAN_CHARS] + text[-_SCAN_CHARS:]
    if _SIGNED_OUT.search(sample):
        return "signed_out"
    if _RATE_LIMIT.search(sample):
        return "rate_limit"
    if _OVERLOADED.search(sample):
        return "overloaded"
    return None


def fallback_reason(error: RuntimeErrorInfo | None) -> AvailabilityReason | None:
    """Why a failed step may move to its next fallback provider, or None when it may not."""
    if error is None or error.kind != "provider":
        return None
    reason = error.details.get("availability")
    return reason if reason in AVAILABILITY_REASONS else None


def provider_choices(node: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The step's primary provider followed by its fallback entries, in order.

    Each choice is a provider resolution without a nested ``fallback`` list. A node with
    no provider resolution has no choices.
    """
    resolution = node["resolutions"].get("provider")
    if resolution is None:
        return []
    primary = {key: value for key, value in resolution.items() if key != "fallback"}
    return [primary, *(dict(item) for item in resolution.get("fallback", ()))]


def choice_summary(choice: Mapping[str, Any]) -> dict[str, Any]:
    """The provider, profile, model and effort a run record shows for one choice."""
    return {
        "provider": choice["provider_id"],
        "profile": choice.get("profile"),
        "model": choice["model"],
        "effort": choice["effort"],
    }


def provider_label(summary: Mapping[str, Any]) -> str:
    """``provider (model)`` with the profile when one is set, for messages."""
    profile = summary.get("profile")
    name = f"{summary['provider']} profile {profile}" if profile else str(summary["provider"])
    return f"{name} ({summary['model']})"


def switch_message(switch: Mapping[str, Any]) -> str:
    """One line saying which provider a step left, which it moved to, and why."""
    reason = REASON_LABELS.get(str(switch["reason"]), str(switch["reason"]))
    return (
        f"Switched from {provider_label(switch['from'])} to {provider_label(switch['to'])}: "
        f"{reason}."
    )
