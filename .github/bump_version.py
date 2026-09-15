#!/usr/bin/env python3
"""Keep every ida-mcp package and plugin version declaration in sync.

Versions are calendar based: ``yyyy.mmdd.rev``, for example ``2026.915.1`` for
the first release on 2026-09-15. The day is spelled without a leading zero so
the same string is a valid PEP 440 release and a valid semantic version.

The release workflow calls this script with ``--bump``, which derives today's
date in UTC and picks the next unused revision for that day. An exact version
can also be supplied for local use.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECT_NAME = "ida-mcp"
TAG_PREFIX = "v"
VERSION_RE = re.compile(
    r"^(?P<year>[1-9]\d{3})\.(?P<date>[1-9]\d{2,3})\.(?P<rev>[1-9]\d*)$"
)
# Versions released before the calendar scheme (0.10.4 and friends) still have
# to be recognised well enough to be replaced in the managed files.
CURRENT_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")

# JSON pointers and occurrence counts are explicit so dependency versions in
# package-lock.json are never changed accidentally.
JSON_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "package.json": (("version",),),
    "package-lock.json": (("version",), ("packages", "", "version")),
    "ida-plugin.json": (("plugin", "version"),),
    ".claude-plugin/plugin.json": (("version",),),
    ".codex-plugin/plugin.json": (("version",),),
    ".github/plugin/plugin.json": (("version",),),
}
MANAGED_FILES = (
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "uv.lock",
    "ida-plugin.json",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    ".github/plugin/plugin.json",
)


class VersionError(RuntimeError):
    """Raised when version declarations are missing or inconsistent."""


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _json_value(document: Any, pointer: tuple[str, ...], path: str) -> Any:
    value = document
    try:
        for component in pointer:
            value = value[component]
    except (KeyError, TypeError) as exc:
        rendered = "/".join(pointer)
        raise VersionError(f"{path}: missing JSON field {rendered!r}") from exc
    return value


def _current_version(pyproject_text: str) -> str:
    try:
        version = tomllib.loads(pyproject_text)["project"]["version"]
    except (KeyError, tomllib.TOMLDecodeError) as exc:
        raise VersionError("pyproject.toml: missing project.version") from exc
    if not isinstance(version, str) or CURRENT_VERSION_RE.fullmatch(version) is None:
        raise VersionError(f"pyproject.toml: unsupported project.version {version!r}")
    return version


def _replace_exact(text: str, old: str, new: str, path: str) -> str:
    count = text.count(old)
    if count != 1:
        raise VersionError(f"{path}: expected one occurrence of {old!r}, found {count}")
    return text.replace(old, new)


def _replace_json_versions(path: str, text: str, old: str, new: str) -> str:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VersionError(f"{path}: invalid JSON: {exc}") from exc

    pointers = JSON_FIELDS[path]
    for pointer in pointers:
        actual = _json_value(document, pointer, path)
        if actual != old:
            rendered = "/".join(pointer)
            raise VersionError(f"{path}: {rendered} is {actual!r}, expected {old!r}")

    pattern = re.compile(rf'(?m)^(\s*"version"\s*:\s*)"{re.escape(old)}"(,?)$')
    matches = list(pattern.finditer(text))
    if len(matches) < len(pointers):
        raise VersionError(
            f"{path}: found only {len(matches)} formatted version field(s); "
            f"expected {len(pointers)}"
        )
    replacements = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal replacements
        if replacements >= len(pointers):
            return match.group(0)
        replacements += 1
        return f'{match.group(1)}"{new}"{match.group(2)}'

    return pattern.sub(replace, text)


def _updated_files(old: str, new: str) -> dict[str, str]:
    texts = {path: _read(path) for path in MANAGED_FILES}

    for path in JSON_FIELDS:
        texts[path] = _replace_json_versions(path, texts[path], old, new)

    texts["pyproject.toml"] = _replace_exact(
        texts["pyproject.toml"],
        f'version = "{old}"',
        f'version = "{new}"',
        "pyproject.toml",
    )

    uv_pattern = re.compile(
        rf'(\[\[package\]\]\nname = "{re.escape(PROJECT_NAME)}"\nversion = ")'
        rf'{re.escape(old)}("\n)'
    )
    uv_text, count = uv_pattern.subn(rf"\g<1>{new}\g<2>", texts["uv.lock"])
    if count != 1:
        raise VersionError(
            f"uv.lock: expected one {PROJECT_NAME!r} package version, found {count}"
        )
    texts["uv.lock"] = uv_text
    return texts


def _validate_version(version: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(version)
    if match is None:
        raise VersionError(f"unsupported version {version!r}; expected yyyy.mmdd.rev")
    year, date, rev = (int(match.group(name)) for name in ("year", "date", "rev"))
    try:
        datetime.date(year, date // 100, date % 100)
    except ValueError as exc:
        raise VersionError(f"{version}: {date} is not a valid mmdd date") from exc
    return year, date, rev


def _released_revisions(year: int, date: int) -> set[int]:
    """Revisions already tagged for a calendar day, so one is never reused."""
    try:
        result = subprocess.run(
            ["git", "tag", "--list", f"{TAG_PREFIX}{year}.{date}.*"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VersionError(f"unable to list existing tags: {exc}") from exc

    revisions = set()
    for tag in result.stdout.split():
        match = VERSION_RE.fullmatch(tag.removeprefix(TAG_PREFIX))
        if match is not None:
            revisions.add(int(match.group("rev")))
    return revisions


def _next_version(current: str, today: datetime.date) -> str:
    year, date = today.year, today.month * 100 + today.day
    revisions = _released_revisions(year, date)

    match = VERSION_RE.fullmatch(current)
    if match is not None:
        current_year, current_date, current_rev = _validate_version(current)
        if (current_year, current_date) > (year, date):
            raise VersionError(
                f"current version {current} is newer than today ({year}.{date}); "
                "check the clock and the checked out branch"
            )
        if (current_year, current_date) == (year, date):
            revisions.add(current_rev)

    return f"{year}.{date}.{max(revisions, default=0) + 1}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("version", nargs="?", help="exact yyyy.mmdd.rev version")
    mode.add_argument(
        "--bump",
        action="store_true",
        help="bump to the next revision for today's UTC date",
    )
    mode.add_argument(
        "--check", action="store_true", help="verify all version declarations"
    )
    args = parser.parse_args(argv)

    try:
        pyproject_text = _read("pyproject.toml")
        current = _current_version(pyproject_text)
        if args.check:
            # Replacing a version with itself exercises every declaration validator.
            _updated_files(current, current)
            print(current)
            return 0

        if args.bump:
            new = _next_version(current, datetime.datetime.now(datetime.UTC).date())
        else:
            new = args.version
            _validate_version(new)

        texts = _updated_files(current, new)
        for path, text in texts.items():
            (ROOT / path).write_text(text, encoding="utf-8")
        print(new)
        return 0
    except (OSError, VersionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
