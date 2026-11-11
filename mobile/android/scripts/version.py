#!/usr/bin/env python3
"""Show, check or change the app version in version.properties.

Edits only version.properties. It never commits, tags, builds or publishes;
docs/versioning.md lists the release steps that follow a version change.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG_PREFIX = 'mobile-v'
MAX_CODE = 2_100_000_000  # Google Play's versionCode ceiling
NAME = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?')
LINE = re.compile(r'^(versionName|versionCode)=(.*)$', re.M)


class VersionError(Exception):
    pass


def parse_name(name):
    match = NAME.fullmatch(name)
    if not match:
        raise VersionError(f'"{name}" is not MAJOR.MINOR.PATCH with an optional -prerelease, such as 1.2.0 or 1.2.0-rc.1.')
    pre = match.group(4)
    return tuple(int(part) for part in match.group(1, 2, 3)), pre.split('.') if pre else []


def precedence(name):
    """A sort key that follows SemVer 2.0.0 precedence (section 11)."""
    core, pre = parse_name(name)
    # A release sorts after its prereleases; numeric identifiers sort before text ones.
    return core, not pre, [(0, int(p), '') if p.isdigit() else (1, 0, p) for p in pre]


def read(root=ROOT):
    path = root / 'version.properties'
    lines = LINE.findall(path.read_text(encoding='utf-8'))
    values = dict(lines)
    if len(lines) != 2 or set(values) != {'versionName', 'versionCode'}:
        raise VersionError(f'{path} must contain exactly one versionName and one versionCode line.')
    name, code = values['versionName'].strip(), values['versionCode'].strip()
    parse_name(name)
    if not code.isdigit() or not 1 <= int(code) <= MAX_CODE:
        raise VersionError(f'versionCode "{code}" must be a whole number from 1 to {MAX_CODE}.')
    return name, int(code)


def write(name, code, root=ROOT):
    path = root / 'version.properties'
    text = path.read_text(encoding='utf-8')
    text = LINE.sub(lambda m: f'{m.group(1)}={name if m.group(1) == "versionName" else code}', text)
    path.write_text(text, encoding='utf-8')


def next_name(current, part):
    (major, minor, patch), _ = parse_name(current)
    if part == 'major':
        return f'{major + 1}.0.0'
    if part == 'minor':
        return f'{major}.{minor + 1}.0'
    return f'{major}.{minor}.{patch + 1}'


def bump(target, root=ROOT, code=None):
    """Move to a higher version name and the next version code. Returns (old, new)."""
    old_name, old_code = read(root)
    name = next_name(old_name, target) if target in ('major', 'minor', 'patch') else target
    if precedence(name) <= precedence(old_name):
        raise VersionError(f'{name} is not newer than the current {old_name}.')
    code = old_code + 1 if code is None else code
    if not old_code < code <= MAX_CODE:
        raise VersionError(f'versionCode must go up from {old_code} and stay at most {MAX_CODE}; got {code}.')
    write(name, code, root)
    return (old_name, old_code), (name, code)


def check(root=ROOT, tag=None):
    name, code = read(root)
    if tag is not None:
        tag = tag.removeprefix('refs/tags/')
        if tag != TAG_PREFIX + name:
            raise VersionError(f'Tag {tag} does not match version.properties; expected {TAG_PREFIX}{name}.')
    return name, code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    show = commands.add_parser('show', help='print the version')
    show.add_argument('--field', choices=('name', 'code', 'tag'), help='print one value only')
    verify = commands.add_parser('check', help='validate version.properties, and a release tag if given')
    verify.add_argument('--tag', help=f'release tag that must equal {TAG_PREFIX}<versionName>')
    change = commands.add_parser('bump', help='raise the version name and add one to the version code')
    change.add_argument('target', help='major, minor, patch, or an exact newer version such as 1.0.0-rc.1')
    change.add_argument('--code', type=int, help='use this version code instead of the next one (must be higher)')
    args = parser.parse_args(argv)
    try:
        if args.command == 'show':
            name, code = read()
            print({'name': name, 'code': code, 'tag': TAG_PREFIX + name}.get(args.field, f'{name} ({code})'))
        elif args.command == 'check':
            name, code = check(tag=args.tag)
            print(f'version.properties is valid: {name} ({code})' + (f', matching tag {args.tag}' if args.tag else ''))
        else:
            (old_name, old_code), (name, code) = bump(args.target, code=args.code)
            print(f'{old_name} ({old_code}) -> {name} ({code})')
            print(f'Next: commit version.properties, then after review tag the commit {TAG_PREFIX}{name}. See docs/versioning.md.')
    except VersionError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
