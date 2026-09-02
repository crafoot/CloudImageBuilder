#!/usr/bin/env python3
"""Validate the package manifest required by the MT3600BE build gate."""

import argparse
import json
import pathlib
import re
import sys


REQUIRED_PACKAGES = (
    "nikki",
    "luci-app-nikki",
    "mihomo-meta",
    "dae",
    "daed",
    "luci-app-daede",
)
_ROW = re.compile(r"^([A-Za-z0-9][A-Za-z0-9+_.-]*)\s+-\s*(\S+)$")
_EMPTY_VERSION = re.compile(r"^([A-Za-z0-9][A-Za-z0-9+_.-]*)\s+-\s*$")


def parse_manifest(text: str) -> dict[str, str]:
    """Parse NAME - VERSION rows, rejecting malformed or conflicting rows."""
    packages: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line:
            raise ValueError(f"manifest line {line_number} must be NAME - VERSION")
        empty_version = _EMPTY_VERSION.fullmatch(raw_line)
        if empty_version:
            raise ValueError(f"manifest line {line_number} has an empty version for {empty_version.group(1)}")
        match = _ROW.fullmatch(raw_line)
        if not match:
            raise ValueError(f"manifest line {line_number} must be NAME - VERSION")
        name, version = match.groups()
        previous = packages.get(name)
        if previous is not None and previous != version:
            raise ValueError(f"conflicting duplicate package {name}: {previous} vs {version}")
        packages[name] = version
    return packages


def validate_required(packages: dict[str, str]) -> None:
    """Require every package and a non-empty version, reporting all failures."""
    missing = [name for name in REQUIRED_PACKAGES if not packages.get(name)]
    if missing:
        raise ValueError("missing required packages: " + ", ".join(missing))


def _result(manifest: pathlib.Path) -> dict:
    packages = parse_manifest(manifest.read_text(encoding="utf-8"))
    validate_required(packages)
    return {"valid": True, "packages": packages}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("--json-output", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        result = _result(args.manifest)
    except (OSError, UnicodeError, ValueError) as error:
        result = {"valid": False, "error": str(error)}
        exit_code = 1
    else:
        exit_code = 0
    output = json.dumps(result, sort_keys=True)
    if args.json_output:
        args.json_output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
