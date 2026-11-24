"""What Raticode's recorded token use would have cost at API list prices.

An estimate for Settings > Usage > Tokens in Raticode, the phone's Usage page and
``Raticode usage show``. Prices come from ``api_prices.PRICES``; nothing here asks a
provider. Rules, in the order the UI explains them:

* Only tokens a provider reported count. Estimated calls and calls with no token data are
  left out, and the result says how many.
* Cache reads, cache writes and uncached ("cold") input are priced separately when a call
  recorded the split. A call without it is priced as if all its input were uncached,
  which overstates the cost when caching happened.
* A cache write whose lifetime wasn't recorded uses the provider's usual lifetime:
  one hour for Claude Code (what it writes on this kind of account), five minutes for
  everything else.
* A model or provider with no list price is "unknown", never $0. A total that leaves some
  calls out is marked as a lower bound ("at least").
"""

from __future__ import annotations

import copy
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from gofer.core.api_prices import (
    PRICES,
    PRICES_CHECKED,
    ModelPrice,
    price_for,
)
from gofer.core.usage_ledger import LOCAL_ROWS, read_ledger_mark, usage_db

# Claude Code writes one-hour cache entries (checked against its transcripts).
LONG_WRITE_PROVIDERS = frozenset({"claude_code"})
METHOD = (
    "Estimate at public API list prices. Cache reads, cache writes and uncached input are "
    "priced separately when the provider reported them; other input is priced as uncached, "
    "which can overstate the cost."
)


@dataclass
class _Group:
    provider: str
    profile: str | None
    model: str | None
    calls: int = 0
    priced_calls: int = 0  # calls with reported tokens
    no_token_calls: int = 0
    estimated_calls: int = 0
    partial_calls: int = 0
    unsplit_calls: int = 0  # reported input without a cache breakdown
    cold_input: int = 0
    unsplit_input: int = 0
    cache_read: int = 0
    cache_write_short: int = 0
    cache_write_long: int = 0
    cache_write_unknown: int = 0  # written without a recorded lifetime
    output: int = 0


def _range_filter(start: datetime | None, end: datetime) -> tuple[str, list[Any]]:
    # Only this computer's calls: a paired desktop's summary row isn't one of its accounts'.
    where = f" WHERE observed_at < ? AND {LOCAL_ROWS}"
    arguments: list[Any] = [end.isoformat()]
    if start is not None:
        where += " AND observed_at >= ?"
        arguments.append(start.isoformat())
    return where, arguments


def cost_groups(
    db: sqlite3.Connection, start: datetime | None, end: datetime
) -> list[dict[str, Any]]:
    """Token sums per provider account and model, split the way prices need them."""
    where, arguments = _range_filter(start, end)
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cache_write_1h_tokens",
    )
    inner = (
        "SELECT observed_at, provider, profile, model, estimated, partial, "
        "tokens <> '{}' AS filled, "
        + ", ".join(f"json_extract(tokens, '$.{name}') AS {name}" for name in fields)
        + f" FROM usage_invocations{where} LIMIT -1"
    )
    split = "(cache_read_tokens IS NOT NULL OR cache_write_tokens IS NOT NULL)"
    reported = "(estimated = 0 AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL))"
    query = (
        "SELECT provider, profile, model, COUNT(*) AS calls, "
        f"SUM({reported}) AS priced_calls, "
        "SUM(estimated = 0 AND NOT filled) AS no_token_calls, "
        "SUM(estimated <> 0) AS estimated_calls, "
        f"SUM({reported} AND partial <> 0) AS partial_calls, "
        f"SUM({reported} AND input_tokens IS NOT NULL AND NOT {split}) AS unsplit_calls, "
        f"SUM(CASE WHEN {reported} AND {split} THEN MAX(0, COALESCE(input_tokens, 0) "
        "- COALESCE(cache_read_tokens, 0) - COALESCE(cache_write_tokens, 0)) END) AS cold_input, "
        f"SUM(CASE WHEN {reported} AND NOT {split} THEN input_tokens END) AS unsplit_input, "
        f"SUM(CASE WHEN {reported} THEN cache_read_tokens END) AS cache_read, "
        f"SUM(CASE WHEN {reported} AND cache_write_1h_tokens IS NOT NULL THEN "
        "MAX(0, COALESCE(cache_write_tokens, 0) - cache_write_1h_tokens) END) "
        "AS cache_write_short, "
        f"SUM(CASE WHEN {reported} THEN MIN(cache_write_1h_tokens, "
        "COALESCE(cache_write_tokens, cache_write_1h_tokens)) END) AS cache_write_long, "
        f"SUM(CASE WHEN {reported} AND cache_write_1h_tokens IS NULL THEN cache_write_tokens END)"
        " AS cache_write_unknown, "
        f"SUM(CASE WHEN {reported} THEN output_tokens END) AS output "
        f"FROM ({inner}) GROUP BY provider, profile, model ORDER BY MIN(observed_at)"
    )
    cursor = db.execute(query, arguments)
    names = [column[0] for column in cursor.description]
    # A sum over no matching call is NULL; for counting it is 0.
    return [
        {
            name: value if value is not None or name in {"profile", "model"} else 0
            for name, value in zip(names, row, strict=True)
        }
        for row in cursor.fetchall()
    ]


def _usd(tokens: int, per_million: float) -> float:
    return tokens * per_million / 1_000_000


def _price_group(group: _Group, price: ModelPrice) -> dict[str, float]:
    """Dollars per kind of token for one model's sums."""
    long_default = group.provider in LONG_WRITE_PROVIDERS
    write_short = group.cache_write_short + (0 if long_default else group.cache_write_unknown)
    write_long = group.cache_write_long + (group.cache_write_unknown if long_default else 0)
    return {
        "input": _usd(group.cold_input + group.unsplit_input, price.input),
        "cache_read": _usd(group.cache_read, price.cache_read_price),
        "cache_write": _usd(write_short, price.cache_write_price)
        + _usd(write_long, price.cache_write_long_price),
        "output": _usd(group.output, price.output),
    }


def _empty_total() -> dict[str, Any]:
    return {
        "cost_usd": None,
        "lower_bound": False,
        "calls": 0,
        "priced_calls": 0,
        "unpriced_calls": 0,
        "no_token_calls": 0,
        "estimated_calls": 0,
        "partial_calls": 0,
        "unsplit_calls": 0,
        "breakdown": {"input": 0.0, "cache_read": 0.0, "cache_write": 0.0, "output": 0.0},
        "tokens": {
            "cold_input": 0,
            "unsplit_input": 0,
            "cache_read": 0,
            "cache_write": 0,
            "output": 0,
        },
    }


def _add(target: dict[str, Any], group: _Group, dollars: dict[str, float] | None) -> None:
    target["calls"] += group.calls
    target["no_token_calls"] += group.no_token_calls
    target["estimated_calls"] += group.estimated_calls
    target["partial_calls"] += group.partial_calls
    target["unsplit_calls"] += group.unsplit_calls
    tokens = target["tokens"]
    tokens["cold_input"] += group.cold_input
    tokens["unsplit_input"] += group.unsplit_input
    tokens["cache_read"] += group.cache_read
    tokens["cache_write"] += (
        group.cache_write_short + group.cache_write_long + group.cache_write_unknown
    )
    tokens["output"] += group.output
    if dollars is None:
        target["unpriced_calls"] += group.priced_calls
        return
    target["priced_calls"] += group.priced_calls
    if not group.priced_calls:
        return
    for kind, amount in dollars.items():
        target["breakdown"][kind] += amount
    target["cost_usd"] = (target["cost_usd"] or 0.0) + sum(dollars.values())


def _finish(target: dict[str, Any]) -> dict[str, Any]:
    # Anything left out makes the dollar figure a floor, not the whole cost.
    target["lower_bound"] = target["cost_usd"] is not None and bool(
        target["unpriced_calls"]
        or target["no_token_calls"]
        or target["partial_calls"]
        or target["estimated_calls"]
    )
    if target["cost_usd"] is not None:
        target["cost_usd"] = round(target["cost_usd"], 6)
        target["breakdown"] = {k: round(v, 6) for k, v in target["breakdown"].items()}
    else:
        target["breakdown"] = None
    return target


def estimate_api_cost(groups: list[dict[str, Any]]) -> dict[str, Any]:
    """The period's estimated API cost per provider account and in total."""
    accounts: dict[tuple[str, str | None], dict[str, Any]] = {}
    total = _empty_total()
    for row in groups:
        group = _Group(**{key: row[key] for key in _Group.__dataclass_fields__ if key in row})
        account = accounts.setdefault(
            (group.provider, group.profile),
            {
                "provider": group.provider,
                "profile": group.profile,
                **_empty_total(),
                "models": [],
                "unpriced_models": [],
            },
        )
        price = price_for(group.provider, group.model)
        if price is not None and not price.caches:
            # Nothing to split: all of a cacheless model's input is uncached input.
            group.unsplit_calls = 0
        dollars = _price_group(group, price) if price is not None else None
        if price is None and group.priced_calls:
            label = group.model or "model not recorded"
            if label not in account["unpriced_models"]:
                account["unpriced_models"].append(label)
        model_entry = {
            "model": group.model,
            "priced_as": price.model if price else None,
            "calls": group.calls,
            "priced_calls": group.priced_calls if price else 0,
            "unsplit_calls": group.unsplit_calls,
            "cost_usd": round(sum(dollars.values()), 6)
            if dollars is not None and group.priced_calls
            else None,
        }
        account["models"].append(model_entry)
        _add(account, group, dollars)
        _add(total, group, dollars)
    providers = [_finish(account) for account in accounts.values()]
    for account in providers:
        account["models"].sort(key=lambda entry: -(entry["cost_usd"] or 0))
    total = _finish(total)
    return {
        "method": METHOD,
        "prices_checked": PRICES_CHECKED,
        "providers": providers,
        "total": total,
        "prices": [price.payload() for price in PRICES],
    }


@dataclass
class _KeptGroups:
    mark: tuple[int, int]
    start: datetime | None
    end: datetime
    groups: list[dict[str, Any]]


_kept: dict[tuple[Any, ...], _KeptGroups] = {}
_kept_lock = threading.Lock()
_KEEP_LIMIT = 32


def period_cost_groups(
    root: Path, key: tuple[Any, ...], start: datetime | None, end: datetime
) -> list[dict[str, Any]]:
    """``cost_groups`` for a period, reused while the ledger and the period's calls match.

    A preset period's ends move with the clock; the kept sums stay right as long as no
    call was recorded since (the ledger mark) and none lies between the old and new ends.
    """
    with usage_db(root) as db:
        mark = read_ledger_mark(db)
        with _kept_lock:
            kept = _kept.get(key)
        if kept is not None and kept.mark == mark and end >= kept.end:
            gaps = [(kept.end, end)]
            same_kind = (kept.start is None) == (start is None)
            if start is not None and kept.start is not None:
                gaps.append((kept.start, start) if start >= kept.start else (start, kept.start))
            moved = not same_kind or any(
                db.execute(
                    "SELECT 1 FROM usage_invocations WHERE observed_at >= ? AND observed_at < ? "
                    "LIMIT 1",
                    (begin.isoformat(), finish.isoformat()),
                ).fetchone()
                for begin, finish in gaps
                if begin < finish
            )
            if not moved:
                return copy.deepcopy(kept.groups)
        groups = cost_groups(db, start, end)
    with _kept_lock:
        _kept.pop(key, None)
        while len(_kept) >= _KEEP_LIMIT:
            del _kept[next(iter(_kept))]
        _kept[key] = _KeptGroups(mark, start, end, groups)
    return copy.deepcopy(groups)
