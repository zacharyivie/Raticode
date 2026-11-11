#!/usr/bin/env python3
"""Check that messages from the last desktop release still fit the current protocol.

A phone or desktop that hasn't updated keeps sending what its release's fixtures show.
Reads the fixtures from the newest v* tag with git and needs no network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "src/gofer/devices/protocol/v2"
NAMED_SCHEMAS = ("invitation", "frame", "session")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], text=True, stdout=subprocess.PIPE, check=True
    ).stdout


def last_release_tag() -> str:
    tags = git("tag", "--list", "v[0-9]*", "--sort=-v:refname").split()
    if not tags:
        raise SystemExit("No v* tags found. Fetch the repository's tags first.")
    return tags[0]


def main() -> None:
    validators = {
        name: Draft202012Validator(
            json.loads((ROOT / PROTOCOL / f"{name}.schema.json").read_text())
        )
        for name in (*NAMED_SCHEMAS, "event")
    }
    tag = last_release_tag()
    names = git("ls-tree", "-r", "--name-only", tag, f"{PROTOCOL}/fixtures").split()
    failures = []
    for name in names:
        stem = Path(name).stem
        kind = stem if stem in NAMED_SCHEMAS else "event"
        record = json.loads(git("show", f"{tag}:{name}"))
        error = next(iter(validators[kind].iter_errors(record)), None)
        if error is not None:
            failures.append(f"{stem}: {error.message}")
    if failures:
        raise SystemExit(f"{tag} messages the protocol now rejects:\n" + "\n".join(failures))
    print(f"{len(names)} fixtures from {tag} still fit the current protocol.")


if __name__ == "__main__":
    main()
