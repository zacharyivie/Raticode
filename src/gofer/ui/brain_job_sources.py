"""Validated, composable source rules for recurring Second Brain jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from gofer.ui.brain_documents import safe_relative
from gofer.ui.brain_queries import saved_matches


def source_rules(
    job: dict[str, Any], collections: list[dict[str, Any]], searches: list[dict[str, Any]]
) -> dict[str, Any]:
    raw = job.get("sourceRules")
    if raw is None:
        scope = job.get("scope", "Inbox folder")
        location: dict[str, Any] = {"kind": "inbox"}
        time: dict[str, Any] = {"mode": "all"}
        if scope == "Documents added in the last 7 days":
            location = {"kind": "all"}
            time = {"mode": "window", "field": "added", "count": 7, "unit": "days"}
        elif scope == "All documents":
            location = {"kind": "all"}
        elif scope.startswith(("Collection: ", "Saved search: ")):
            is_collection = scope.startswith("Collection: ")
            name = scope.split(": ", 1)[1]
            item = next(
                (
                    item
                    for item in (collections if is_collection else searches)
                    if item["name"] == name
                ),
                None,
            )
            if not item:
                raise ValueError("Job collection or saved search no longer exists")
            location = {"kind": "collection" if is_collection else "saved-search", "id": item["id"]}
        elif scope != "Inbox folder":
            raise ValueError("Unknown job scope. Edit this job's source rules.")
        raw = {"location": location, "time": time}
    if not isinstance(raw, dict) or raw.get("version", 1) != 1:
        raise ValueError("Invalid job source rules")
    location = raw.get("location", {"kind": "all"})
    time = raw.get("time", {"mode": "all"})
    if not isinstance(location, dict) or not isinstance(time, dict):
        raise ValueError("Invalid source location or time rule")
    kind = location.get("kind", "all")
    if kind not in {"all", "inbox", "folder", "collection", "saved-search"}:
        raise ValueError("Choose a supported source location")
    path = str(location.get("path", "")).strip().rstrip("/")
    item_id = str(location.get("id", ""))
    if kind == "folder":
        path = safe_relative(path)
    if kind in {"collection", "saved-search"}:
        if not any(
            item["id"] == item_id for item in (collections if kind == "collection" else searches)
        ):
            raise ValueError("Job collection or saved search no longer exists")
    mode = time.get("mode", "all")
    field = time.get("field", "activity")
    count = time.get("count", 7)
    unit = time.get("unit", "days")
    if mode not in {"all", "since-success", "window"}:
        raise ValueError("Choose all dates, since last successful run, or a time window")
    if field not in {"activity", "added", "modified", "published", "captured"}:
        raise ValueError("Choose a supported source date")
    if (
        unit not in {"hours", "days"}
        or isinstance(count, bool)
        or not isinstance(count, int)
        or not 1 <= count <= 87600
    ):
        raise ValueError("Time windows need 1–87600 whole hours or days")
    if raw.get("match", "all") not in {"all", "any", "phrase"}:
        raise ValueError("Choose all words, any words, or an exact phrase")
    if raw.get("order", "oldest") not in {"oldest", "newest"}:
        raise ValueError("Choose oldest or newest sources first")
    rules = {
        "version": 1,
        "location": {
            "kind": kind,
            "id": item_id,
            "path": path,
            "recursive": location.get("recursive", True),
        },
        "time": {"mode": mode, "field": field, "count": count, "unit": unit},
        "query": str(raw.get("query", "")).strip(),
        "match": raw.get("match", "all"),
        "project": str(raw.get("project", "")).strip(),
        "order": raw.get("order", "oldest"),
    }
    if len(rules["query"]) > 2000:
        raise ValueError("Topic rules may contain at most 2000 characters")
    # Reuse search syntax validation without reading a document or loading an embedding model.
    saved_matches({"excluded": True}, {"text": rules["query"]}, {})
    for key in ("tags", "excludeTags", "types"):
        values = raw.get(key, [])
        if (
            not isinstance(values, list)
            or len(values) > 100
            or any(not isinstance(value, str) for value in values)
        ):
            raise ValueError("Tags and source types must be lists of text values")
        rules[key] = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if set(rules["types"]) - {"note", "html", "text", "pdf", "report", "reference", "capture"}:
        raise ValueError("Choose supported source types")
    for key in ("requireAllTags", "unreadOnly", "starredOnly", "includeOutputs", "onlyUnprocessed"):
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise ValueError("Source rule switches must be true or false")
        rules[key] = value
    if not isinstance(rules["location"]["recursive"], bool):
        raise ValueError("Include subfolders must be true or false")
    return rules


def timestamp(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    except ValueError:
        return None


def source_date(doc: dict[str, Any], field: str) -> datetime | None:
    dates = doc.get("dates", {})
    values = (
        [timestamp(dates.get(key)) for key in ("added", "modified")]
        if field == "activity"
        else [timestamp(dates.get(field))]
    )
    return max((value for value in values if value is not None), default=None)


def matches_source_rules(
    doc: dict[str, Any],
    rules: dict[str, Any],
    job: dict[str, Any],
    library: dict[str, Any],
    moment: datetime,
) -> bool:
    if doc.get("missing") or doc.get("excluded") or doc["extraction"] != "ready":
        return False
    if doc["origin"] == "generated" and not rules["includeOutputs"]:
        return False
    if rules["unreadOnly"] and doc.get("reading", {}).get("read"):
        return False
    if rules["starredOnly"] and not doc.get("reading", {}).get("starred"):
        return False
    if set(tag.casefold() for tag in rules["excludeTags"]) & set(
        tag.casefold() for tag in doc.get("tags", [])
    ):
        return False
    if not saved_matches(
        doc,
        {
            "text": rules["query"],
            "match": rules["match"],
            "filters": {
                "tags": rules["tags"],
                "requireAllTags": rules["requireAllTags"],
                "types": rules["types"],
                "projects": [rules["project"]] if rules["project"] else [],
            },
        },
        library,
    ):
        return False
    mode = rules["time"]["mode"]
    seen = job.get("seenVersions", {})
    if (
        rules["onlyUnprocessed"] or mode == "since-success" or job["recipe"] == "summarize-new"
    ) and seen.get(doc["id"]) == doc["version"]:
        return False
    cutoff = timestamp(job.get("lastSuccessfulRunAt")) if mode == "since-success" else None
    if mode == "window":
        cutoff = moment - timedelta(**{rules["time"]["unit"]: rules["time"]["count"]})
    if cutoff:
        date = source_date(doc, rules["time"]["field"])
        # An edited version remains new even if its filesystem timestamp was backdated.
        changed = (
            rules["time"]["field"] == "activity"
            and doc["id"] in seen
            and seen[doc["id"]] != doc["version"]
        )
        if not (mode == "since-success" and changed):
            if date is None:
                return False
            before_cutoff = date <= cutoff if mode == "since-success" else date < cutoff
            if before_cutoff or date > moment:
                return False
    return True
