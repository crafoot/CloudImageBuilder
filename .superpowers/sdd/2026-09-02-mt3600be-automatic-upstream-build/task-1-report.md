# Task 1 report: canonical lock model and change decision

## Implementation

Implemented `scripts/mt3600be_sources.py` with:

- anchored validation for 40-character Git SHAs and immutable `sha256:` container digests;
- numeric selection of the highest stable `25.12.x` tag;
- deterministic UTF-8 canonical JSON and SHA-256 fingerprints, excluding presentation-only fields;
- named source-group comparison for ImmortalWrt ImageBuilder/SDK/feeds, Nikki, and daede components;
- `compare` CLI with `unchanged`/`changed` exit 0, `not-ready` exit 2, and `invalid` exit 3.

Added table-driven/unit coverage and the three required lock fixtures under `tests/fixtures/mt3600be/`.

## TDD evidence

RED (before production implementation):

```text
$ python3 -m unittest tests.test_mt3600be_sources -v
ImportError: Failed to import test module: test_mt3600be_sources
ModuleNotFoundError: No module named 'scripts'
```

This was the expected missing-module failure.

GREEN (after the minimal implementation):

```text
$ python3 -m unittest tests.test_mt3600be_sources -v
Ran 9 tests in 0.030s
OK
```

After the presentation-field regression test was added:

```text
$ python3 -m unittest tests.test_mt3600be_sources -v
Ran 10 tests in 0.032s
OK
```

## Final verification

All passed:

```text
python3 -m unittest tests.test_mt3600be_sources -v
python3 -m py_compile scripts/mt3600be_sources.py tests/test_mt3600be_sources.py
git diff --check
```

The compare CLI fixture check returned exit 0 and JSON with `"decision":"unchanged"`, a fingerprint, and an empty `changed_groups` list.

## Files

- `scripts/mt3600be_sources.py`
- `tests/test_mt3600be_sources.py`
- `tests/fixtures/mt3600be/previous-lock.json`
- `tests/fixtures/mt3600be/candidate-same.json`
- `tests/fixtures/mt3600be/candidate-imagebuilder-changed.json`

## Self-review and concerns

The implementation is limited to the requested files and preserves unrelated repository behavior. Canonicalization is recursive, so presentation metadata does not alter either full-state or group fingerprints. Missing or unstable candidate versions return `not-ready`; malformed references return `invalid` through the CLI. No known concerns remain for Task 1. The later resolver task should keep its lock schema field names aligned with the validators (`commit`/`digest`) or extend the validator deliberately.
