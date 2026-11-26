"""Reusable post templates: the user's own and a few starters.

A template is a title and one text per platform, with blanks written in braces, like
``{version}`` or ``{what changed}``. Filling a template only makes a draft or a proposal;
it goes through review and the user's approval like any other post. Only the user saves,
changes or deletes templates. Rem, tasks and workflows can read them and propose from one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from gofer.social.models import MAX_TEMPLATE_NAME, MAX_TEXT, MAX_TITLE, MAX_VARIANTS, PLATFORMS

# A blank: a letter, then up to 39 letters, digits, spaces, '_' or '-'. "{}" and "{ x}"
# are left alone, so code samples keep their braces.
BLANK = re.compile(r"\{([A-Za-z][\w \-]{0,39})\}")

STARTERS: tuple[dict[str, Any], ...] = (
    {
        "id": "starter-release",
        "name": "New release",
        "description": "Announce a new version and what's in it.",
        "title": "{product} {version} is out",
        "variants": [
            {
                "platform": "x",
                "text": "{product} {version} is out. {headline}\n\n{link}",
            },
            {
                "platform": "linkedin",
                "text": "{product} {version} is out.\n\n{headline}\n\nWhat's new:\n"
                "- {change one}\n- {change two}\n\nRead more: {link}",
            },
            {
                "platform": "discord",
                "text": "**{product} {version} is out**\n{headline}\n\n"
                "- {change one}\n- {change two}\n\n{link}",
            },
        ],
    },
    {
        "id": "starter-how-to",
        "name": "How-to or blog post",
        "description": "Share a guide, tutorial or article you wrote.",
        "title": "New post: {topic}",
        "variants": [
            {"platform": "x", "text": "New post: {topic}. {one line takeaway}\n\n{link}"},
            {
                "platform": "linkedin",
                "text": "{question your reader has}\n\nI wrote up {topic}: "
                "{one line takeaway}\n\n{link}",
            },
            {"platform": "facebook", "text": "New on the blog: {topic}\n\n{link}"},
        ],
    },
    {
        "id": "starter-weekly",
        "name": "Weekly update",
        "description": "A short build-in-public update on what you shipped.",
        "title": "This week: {highlight}",
        "variants": [
            {"platform": "x", "text": "This week: {highlight}. Next up: {next}."},
            {
                "platform": "linkedin",
                "text": "This week I shipped {highlight}.\n\nWhat I learned: {lesson}\n\n"
                "Next up: {next}.",
            },
            {
                "platform": "discord",
                "text": "**This week:** {highlight}\n**Next up:** {next}",
            },
        ],
    },
)


class TemplateError(ValueError):
    pass


def blanks(texts: Iterable[str]) -> list[str]:
    """The blank names in these texts, in order of first use."""
    found: list[str] = []
    for text in texts:
        for match in BLANK.finditer(text or ""):
            if match.group(1) not in found:
                found.append(match.group(1))
    return found


def fill(text: str, values: Mapping[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return str(value) if isinstance(value, str | int | float) else match.group(0)

    return BLANK.sub(replace, text)


def view(template: Mapping[str, Any], *, builtin: bool = False) -> dict[str, Any]:
    texts = [template.get("title") or ""] + [
        variant.get("text") or "" for variant in template.get("variants") or []
    ]
    return {**dict(template), "builtin": builtin, "blanks": blanks(texts)}


def clean(body: Mapping[str, Any]) -> dict[str, Any]:
    """A template from the user's request, checked."""
    name = str(body.get("name") or "").strip()[:MAX_TEMPLATE_NAME]
    if not name:
        raise TemplateError("Give the template a name.")
    variants = body.get("variants")
    if not isinstance(variants, list) or not variants:
        raise TemplateError("A template needs at least one platform.")
    if len(variants) > MAX_VARIANTS:
        raise TemplateError(f"A template can have at most {MAX_VARIANTS} platforms.")
    cleaned = []
    seen: set[str] = set()
    for item in variants:
        if not isinstance(item, Mapping):
            raise TemplateError("Each platform entry is an object.")
        platform = str(item.get("platform") or "")
        account_id = str(item.get("account_id") or "")[:100]
        if platform not in PLATFORMS:
            raise TemplateError("Unknown platform.")
        key = account_id or platform
        if key in seen:
            raise TemplateError("Each platform or account appears once per template.")
        seen.add(key)
        text = str(item.get("text") or "")
        if len(text) > MAX_TEXT:
            raise TemplateError("The text is too long.")
        link = str(item.get("link") or "").strip()[:2000]
        entry: dict[str, Any] = {"platform": platform, "text": text}
        if account_id:
            entry["account_id"] = account_id
        if link:
            entry["link"] = link
        cleaned.append(entry)
    return {
        "name": name,
        "description": str(body.get("description") or "").strip()[:300],
        "title": str(body.get("title") or "").strip()[:MAX_TITLE],
        "variants": cleaned,
    }


def to_proposal(
    template: Mapping[str, Any],
    values: Mapping[str, Any],
    accounts: Sequence[Mapping[str, Any]],
    *,
    proposer: bool,
) -> tuple[str, list[dict[str, Any]]]:
    """The title and variants for a proposal from ``template`` with its blanks filled in.

    Platforms with no usable account are left out. Every blank must have a value.
    """
    missing = [
        name
        for name in view(template)["blanks"]
        if not isinstance(values.get(name), str | int | float) or str(values.get(name)) == ""
    ]
    if missing:
        raise TemplateError("Fill in: " + ", ".join(missing) + ".")
    usable = [
        account
        for account in accounts
        if account.get("enabled") and (not proposer or not account.get("quiet"))
    ]
    variants = []
    for item in template.get("variants") or []:
        platform = item["platform"]
        entry: dict[str, Any] = {
            "text": fill(item.get("text") or "", values),
            "link": fill(item.get("link") or "", values),
        }
        if item.get("account_id"):
            if not any(a["id"] == item["account_id"] for a in usable):
                continue
            entry["account_id"] = item["account_id"]
        elif PLATFORMS[platform].api:
            matches = [a for a in usable if a["platform"] == platform]
            if not matches:
                continue
            entry["account_id"] = matches[0]["id"]
        else:
            entry["platform"] = platform
        variants.append(entry)
    if not variants:
        raise TemplateError("None of this template's platforms has a connected account yet.")
    return fill(template.get("title") or "", values), variants
