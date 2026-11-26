#!/usr/bin/env python3
"""Check that messages from the last desktop release still fit the current protocol.

A phone or desktop that hasn't updated keeps sending what its release's fixtures show.
Reads the fixtures from the newest v* tag with git and needs no network.

Without a v* tag it fails, because CI fetches the tags and a missing tag there is a
setup error. ``--skip-without-tags`` prints that it skipped and why instead: a local
clone may have no tags, and scripts/check-dev.py runs this on every landing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "src/gofer/devices/protocol/v2"
NAMED_SCHEMAS = ("invitation", "frame", "session")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], text=True, stdout=subprocess.PIPE, check=True
    ).stdout


NO_TAGS = "No v* tags found, so there is no last release to compare with."


def last_release_tag() -> str | None:
    tags = git("tag", "--list", "v[0-9]*", "--sort=-v:refname").split()
    return tags[0] if tags else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--skip-without-tags",
        action="store_true",
        help="pass with a note instead of failing when the clone has no v* tag",
    )
    args = parser.parse_args(argv)
    tag = last_release_tag()
    if tag is None:
        if args.skip_without_tags:
            print(f"Skipped: {NO_TAGS} Fetch the tags with git fetch --tags to run it.")
            return 0
        print(f"{NO_TAGS} Fetch the repository's tags first.", file=sys.stderr)
        return 1
    validators = {
        name: Draft202012Validator(
            json.loads((ROOT / PROTOCOL / f"{name}.schema.json").read_text())
        )
        for name in (*NAMED_SCHEMAS, "event")
    }
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
        print(f"{tag} messages the protocol now rejects:", *failures, sep="\n", file=sys.stderr)
        return 1
    print(f"{len(names)} fixtures from {tag} still fit the current protocol.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
