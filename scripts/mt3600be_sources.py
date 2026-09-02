#!/usr/bin/env python3
"""Canonical source-lock validation and change decision for MT3600BE."""

import argparse
import copy
import hashlib
import json
import re
import sys


_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-fA-F]{64}$", re.IGNORECASE)
_VERSION = re.compile(r"^25\.12\.([0-9]+)$")
_PRESENTATION_KEYS = {"display", "description", "label", "url", "name", "title"}


def validate_sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("Git SHA must be a 40-character hexadecimal value")
    return value.lower()


def validate_digest(value: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError("container digest must use sha256 and 64 hexadecimal characters")
    return value.lower()


def select_stable_25_12(tags: list[str]) -> str:
    eligible = []
    for tag in tags:
        match = _VERSION.fullmatch(tag) if isinstance(tag, str) else None
        if match:
            eligible.append((int(match.group(1)), tag))
    if not eligible:
        raise ValueError("no stable 25.12.x version found")
    return max(eligible)[1]


def _without_presentation(value):
    if isinstance(value, dict):
        normalized = {}
        for key, child in value.items():
            if key in _PRESENTATION_KEYS:
                continue
            if key in {"commit", "sha", "sha1", "sha256"}:
                child = validate_sha(child)
            elif key == "digest":
                child = validate_digest(child)
            normalized[key] = _without_presentation(child)
        return normalized
    if isinstance(value, list):
        return [_without_presentation(item) for item in value]
    return value


def canonical_bytes(state: dict) -> bytes:
    canonical = _without_presentation(copy.deepcopy(state))
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def fingerprint(state: dict) -> str:
    return hashlib.sha256(canonical_bytes(state)).hexdigest()


def _group_value(state, path):
    value = state
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _canonical_group_value(value, path):
    # Keep scalar references in the same key context as their source lock.
    if value is None:
        return canonical_bytes(None)
    if path[-1] in {"commit", "sha", "sha1", "sha256", "digest"}:
        return canonical_bytes({path[-1]: value})
    return canonical_bytes(value)


_GROUPS = {
    "immortalwrt.commit": ("immortalwrt", "commit"),
    "immortalwrt.imagebuilder": ("immortalwrt", "imagebuilder"),
    "immortalwrt.sdk": ("immortalwrt", "sdk"),
    "immortalwrt.feeds": ("immortalwrt", "feeds"),
    "nikki": ("nikki",),
    "daede.dae": ("daede", "dae"),
    "daede.daed": ("daede", "daed"),
    "daede.luci": ("daede", "luci"),
}


def _validate_known_references(value, path="state"):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"commit", "sha", "sha1", "sha256"}:
                validate_sha(child)
            elif key == "digest":
                validate_digest(child)
            else:
                _validate_known_references(child, f"{path}.{key}")
    elif isinstance(value, list):
        for child in value:
            _validate_known_references(child, path)


def compare_states(previous: dict | None, candidate: dict) -> tuple[str, list[str]]:
    if not isinstance(candidate, dict):
        raise ValueError("candidate lock must be an object")
    immortalwrt = candidate.get("immortalwrt")
    if not isinstance(immortalwrt, dict) or not isinstance(immortalwrt.get("version"), str):
        return "not-ready", ["immortalwrt"]
    if not _VERSION.fullmatch(immortalwrt["version"]):
        return "not-ready", ["immortalwrt"]
    _validate_known_references(candidate)
    if previous is None:
        return "changed", ["bootstrap"]
    if not isinstance(previous, dict):
        raise ValueError("previous lock must be an object")
    _validate_known_references(previous)
    changed = []
    for name, path in _GROUPS.items():
        if _canonical_group_value(_group_value(previous, path), path) != _canonical_group_value(_group_value(candidate, path), path):
            changed.append(name)
    if _canonical_group_value(_group_value(previous, ("immortalwrt", "version")), ("version",)) != canonical_bytes(immortalwrt["version"]):
        changed.insert(0, "immortalwrt.version")
    return ("changed" if changed else "unchanged"), changed


def _cli_compare(args):
    try:
        candidate = json.loads(open(args.candidate, encoding="utf-8").read())
        previous = None if args.previous == "-" else json.loads(open(args.previous, encoding="utf-8").read())
        decision, groups = compare_states(previous, candidate)
        output = {"decision": decision, "fingerprint": fingerprint(candidate), "changed_groups": groups}
        print(json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return {"unchanged": 0, "changed": 0, "not-ready": 2}[decision]
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
        print(json.dumps({"decision": "invalid", "fingerprint": None, "changed_groups": [], "error": str(error)}, sort_keys=True, separators=(",", ":")))
        return 3


def main(argv=None):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--previous", required=True)
    compare.add_argument("--candidate", required=True)
    args = parser.parse_args(argv)
    if args.command == "compare":
        return _cli_compare(args)
    return 3


if __name__ == "__main__":
    sys.exit(main())
