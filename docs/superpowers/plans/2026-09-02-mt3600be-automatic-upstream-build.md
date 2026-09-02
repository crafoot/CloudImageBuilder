# MT3600BE Automatic Upstream Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a new GL-MT3600BE firmware once per day only when a reproducibly locked ImmortalWrt, Nikki, or daede/dae/daed input changes.

**Architecture:** A deterministic Python resolver converts GitHub, Docker Registry, and ImmortalWrt feed metadata into a canonical candidate lock. A scheduled workflow builds candidates on an isolated staging branch, validates package and firmware artifacts, prepares a draft Release, and fast-forwards `dev` only from the exact tested SHA before publishing the draft.

**Tech Stack:** Python 3 standard library, `unittest`, Bash, GitHub Actions, GitHub CLI, Docker Registry HTTP API, ImmortalWrt ImageBuilder and SDK containers.

**Spec:** `docs/superpowers/specs/2026-09-02-mt3600be-automatic-upstream-build-design.md`

## Global Constraints

- Apply automation only to `glinet_gl-mt3600be` on `dev`; never promote automation commits to `master`.
- Check daily near 03:20 Asia/Singapore, corresponding to `20 19 * * *` UTC.
- Track stable numeric `25.12.x` only; reject Snapshot, RC, prerelease, and unversioned rolling tags.
- A candidate uses full 40-character Git SHAs and immutable `sha256:<64 hex>` container digests.
- Any monitored source or repository-index change triggers a candidate build.
- Official dae/daed/dae-wing heads ahead of `kenzok8/openwrt-daede` pins produce `not-ready`, never a stale build labeled latest.
- Do not change the successful lock, `dev`, or a public Release before the candidate passes package, firmware, manifest, and draft-asset gates.
- Keep manual builds and every non-MT3600BE profile working as before.
- Never add unattended router flashing.
- Use test-first development for production scripts and fresh verification before every commit or completion claim.

## File Structure

- Create `scripts/mt3600be_sources.py`: validation, canonicalization, upstream resolution, lock comparison, and build-environment output.
- Create `scripts/validate_mt3600be_manifest.py`: parse a firmware manifest and enforce the required runtime packages.
- Create `tests/test_mt3600be_sources.py`: deterministic resolver and lock behavior using fixture transports.
- Create `tests/test_validate_mt3600be_manifest.py`: manifest acceptance and per-package failure cases.
- Create `tests/fixtures/mt3600be/`: literal GitHub, registry, feed, lock, and manifest responses.
- Create `.github/workflows/check-mt3600be-updates.yml`: daily resolver, staging build, draft, promotion, publication, and cleanup orchestration.
- Modify `.github/workflows/build-wireless-router25.12.yml`: accept automatic-build metadata, consume a candidate lock, expose artifacts, and skip direct public Release creation for automatic child runs.
- Modify `mediatek-filogic/build25.sh`: accept a locked ImageBuilder/package context without changing non-MT3600BE behavior, and keep the seven-package checksum gate.
- Create `docs/mt3600be-automatic-build.md`: operator-facing state, retry, and manual-dispatch instructions.

---

### Task 1: Canonical lock model and change decision

**Files:**
- Create: `scripts/mt3600be_sources.py`
- Create: `tests/test_mt3600be_sources.py`
- Create: `tests/fixtures/mt3600be/previous-lock.json`
- Create: `tests/fixtures/mt3600be/candidate-same.json`
- Create: `tests/fixtures/mt3600be/candidate-imagebuilder-changed.json`

**Interfaces:**
- Produces: `validate_sha(value: str) -> str`
- Produces: `validate_digest(value: str) -> str`
- Produces: `select_stable_25_12(tags: list[str]) -> str`
- Produces: `canonical_bytes(state: dict) -> bytes`
- Produces: `fingerprint(state: dict) -> str`
- Produces: `compare_states(previous: dict | None, candidate: dict) -> tuple[str, list[str]]`
- Produces CLI: `python3 scripts/mt3600be_sources.py compare --previous PATH --candidate PATH`

- [ ] **Step 1: Write failing lock validation and version-selection tests**

Add table-driven tests with literal expected values:

```python
def test_selects_highest_stable_25_12(self):
    tags = ["25.12.1", "25.12.3", "25.12.2", "25.12-SNAPSHOT", "25.12.4-rc1", "26.01.0"]
    self.assertEqual(select_stable_25_12(tags), "25.12.3")

def test_rejects_abbreviated_sha(self):
    with self.assertRaisesRegex(ValueError, "40-character"):
        validate_sha("3799926")

def test_rejects_non_sha256_container_digest(self):
    with self.assertRaisesRegex(ValueError, "sha256"):
        validate_digest("sha1:" + "a" * 40)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_mt3600be_sources -v`

Expected: import failure because `scripts.mt3600be_sources` does not exist.

- [ ] **Step 3: Implement minimal validators and stable-version selection**

Use anchored regular expressions and numeric tuple ordering. `select_stable_25_12()` accepts only `^25\.12\.[0-9]+$` and raises `ValueError` when no eligible version exists.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m unittest tests.test_mt3600be_sources -v`

Expected: all Task 1 validator and selection tests pass.

- [ ] **Step 5: Write failing canonicalization and comparison tests**

Required assertions:

```python
def test_canonicalization_ignores_object_key_order(self):
    self.assertEqual(fingerprint({"b": 2, "a": 1}), fingerprint({"a": 1, "b": 2}))

def test_missing_previous_lock_is_changed(self):
    decision, groups = compare_states(None, {"schema": 1, "immortalwrt": {"version": "25.12.1"}})
    self.assertEqual(decision, "changed")
    self.assertIn("bootstrap", groups)

def test_same_version_new_image_digest_is_changed(self):
    decision, groups = compare_states(load_fixture("previous-lock.json"), load_fixture("candidate-imagebuilder-changed.json"))
    self.assertEqual(decision, "changed")
    self.assertIn("immortalwrt.imagebuilder", groups)
```

- [ ] **Step 6: Run the comparison tests and verify RED**

Run: `python3 -m unittest tests.test_mt3600be_sources -v`

Expected: failures for undefined canonicalization and comparison functions.

- [ ] **Step 7: Implement canonical JSON, fingerprinting, and named group comparison**

Canonical JSON uses `json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`. Exclude presentation-only fields before hashing. Return stable group names such as `immortalwrt.imagebuilder`, `immortalwrt.sdk`, `immortalwrt.feeds`, `nikki`, `daede.dae`, `daede.daed`, and `daede.luci`.

- [ ] **Step 8: Add and verify the compare CLI**

The CLI prints one JSON object containing `decision`, `fingerprint`, and `changed_groups`, then exits `0` for `unchanged` or `changed`, `2` for `not-ready`, and `3` for `invalid`.

Run: `python3 scripts/mt3600be_sources.py compare --previous tests/fixtures/mt3600be/previous-lock.json --candidate tests/fixtures/mt3600be/candidate-same.json`

Expected: JSON with `"decision":"unchanged"` and exit `0`.

- [ ] **Step 9: Verify and commit Task 1**

Run:

```bash
python3 -m unittest tests.test_mt3600be_sources -v
python3 -m py_compile scripts/mt3600be_sources.py tests/test_mt3600be_sources.py
git diff --check
```

Expected: all tests pass and all commands exit `0`.

Commit:

```bash
git add scripts/mt3600be_sources.py tests/test_mt3600be_sources.py tests/fixtures/mt3600be
git commit -m "Add deterministic MT3600BE source lock model"
```

### Task 2: Resolve GitHub, container, and feed inputs

**Files:**
- Modify: `scripts/mt3600be_sources.py`
- Modify: `tests/test_mt3600be_sources.py`
- Create: `tests/fixtures/mt3600be/github-responses.json`
- Create: `tests/fixtures/mt3600be/registry-responses.json`
- Create: `tests/fixtures/mt3600be/feed-indexes.json`
- Create: `tests/fixtures/mt3600be/daede-ready.json`
- Create: `tests/fixtures/mt3600be/daede-not-ready.json`

**Interfaces:**
- Consumes: validators, canonicalization, and fingerprinting from Task 1.
- Produces: `Transport.get_json(url: str, headers: dict[str, str] | None = None) -> tuple[dict, dict[str, str]]`
- Produces: `Transport.get_bytes(url: str) -> bytes`
- Produces: `resolve_candidate(transport: Transport, previous: dict | None) -> dict`
- Produces CLI: `resolve --previous PATH --output PATH --github-token-env GH_TOKEN`
- Produces CLI: `build-env --lock PATH`, emitting validated `KEY=VALUE` lines for GitHub output files.

- [ ] **Step 1: Write failing registry and feed resolution tests**

Cover these real boundary contracts with fixture responses:

- Docker bearer-token challenge is followed and the manifest `Docker-Content-Digest` is recorded.
- both the ImageBuilder and SDK exact version tags must exist.
- a changed feed payload changes `combined_digest` even when `version` stays `25.12.1`.
- feed URLs are sorted before combination.
- a missing feed returns `invalid`, not `unchanged`.

The fixture transport returns complete response headers and bodies captured in the fixture files; assertions target the resolved candidate, not calls made to the fixture transport.

- [ ] **Step 2: Run resolver tests and verify RED**

Run: `python3 -m unittest tests.test_mt3600be_sources.ResolverTests -v`

Expected: failure because `resolve_candidate` and `Transport` are undefined.

- [ ] **Step 3: Implement allowlisted registry and feed resolution**

Allow only:

```text
registry-1.docker.io
auth.docker.io
downloads.immortalwrt.org
api.github.com
raw.githubusercontent.com
```

Resolve exact manifest digests; never store bearer tokens. Hash raw `.adb` response bytes individually and compute the combined digest from sorted `URL<TAB>SHA256` records.

- [ ] **Step 4: Write failing GitHub tree and daede-readiness tests**

Use literal fixture SHAs to prove:

- changing only `nikki`, `luci-app-nikki`, or `mihomo-meta` changes the corresponding group.
- changing `dae`, `daed`, `luci-app-daede`, `ci/pins.env`, or a declared source hash changes the corresponding daede group.
- official dae, daed, or dae-wing head ahead of its declared pin returns `not-ready` with the mismatched repository names.
- documentation-only changes outside monitored subtrees do not change the candidate fingerprint.

- [ ] **Step 5: Run GitHub/daede tests and verify RED**

Run: `python3 -m unittest tests.test_mt3600be_sources.GitHubResolverTests -v`

Expected: failures for missing GitHub and Makefile resolution behavior.

- [ ] **Step 6: Implement GitHub tree, Makefile, and pin parsing**

Parse only anchored assignments for `PKG_VERSION`, `PKG_RELEASE`, `PKG_SOURCE`, and `PKG_HASH`. Parse `ci/pins.env` with an allowlist of required uppercase names. Reject command substitutions, shell expansions, abbreviated SHAs, missing package trees, and unexpected repository identities.

- [ ] **Step 7: Implement the resolve and build-env CLIs**

`resolve` writes candidate JSON atomically to the requested path and prints the decision JSON. `build-env` emits exactly these fields after validation:

```text
IMMORTAL_VERSION
IMAGEBUILDER_REFERENCE
SDK_ARCH_REFERENCE
NIKKI_REF
DAEDE_REF
SOURCE_FINGERPRINT
```

`IMAGEBUILDER_REFERENCE` and `SDK_ARCH_REFERENCE` must contain tag plus digest, for example `...25.12.1@sha256:<64 hex>`.

- [ ] **Step 8: Run the entire resolver suite and a fixture CLI dry run**

Run:

```bash
python3 -m unittest tests.test_mt3600be_sources -v
python3 scripts/mt3600be_sources.py resolve --fixture tests/fixtures/mt3600be/github-responses.json --previous tests/fixtures/mt3600be/previous-lock.json --output /tmp/mt3600be-candidate.json
python3 scripts/mt3600be_sources.py build-env --lock /tmp/mt3600be-candidate.json
```

Expected: tests pass; both CLI commands exit `0`; build output contains full refs and digests.

- [ ] **Step 9: Verify and commit Task 2**

Run:

```bash
python3 -m unittest tests.test_mt3600be_sources -v
python3 -m py_compile scripts/mt3600be_sources.py tests/test_mt3600be_sources.py
git diff --check
```

Commit:

```bash
git add scripts/mt3600be_sources.py tests/test_mt3600be_sources.py tests/fixtures/mt3600be
git commit -m "Resolve locked MT3600BE upstream inputs"
```

### Task 3: Firmware manifest gate

**Files:**
- Create: `scripts/validate_mt3600be_manifest.py`
- Create: `tests/test_validate_mt3600be_manifest.py`
- Create: `tests/fixtures/mt3600be/manifest-complete.txt`
- Create: `tests/fixtures/mt3600be/manifest-missing-daed.txt`

**Interfaces:**
- Produces: `parse_manifest(text: str) -> dict[str, str]`
- Produces: `validate_required(packages: dict[str, str]) -> None`
- Produces CLI: `python3 scripts/validate_mt3600be_manifest.py MANIFEST [--json-output PATH]`

- [ ] **Step 1: Write the failing complete-manifest test**

The complete fixture must include literal non-empty versions for `nikki`, `luci-app-nikki`, `mihomo-meta`, `dae`, `daed`, and `luci-app-daede`. Assert that the parser returns those exact versions.

- [ ] **Step 2: Run the test and verify RED**

Run: `python3 -m unittest tests.test_validate_mt3600be_manifest -v`

Expected: import failure because the validator does not exist.

- [ ] **Step 3: Implement the minimal parser and validator**

Accept only manifest rows shaped as `NAME - VERSION`, reject duplicate names with different versions, and report every missing required package in one error.

- [ ] **Step 4: Add one failing subtest per required package**

Remove each required package independently from the complete literal fixture and assert that `validate_required()` names that package. Include a duplicate-version conflict test and an empty-version test.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python3 -m unittest tests.test_validate_mt3600be_manifest -v`

Expected: all manifest tests pass.

- [ ] **Step 6: Verify the CLI and commit Task 3**

Run:

```bash
python3 scripts/validate_mt3600be_manifest.py tests/fixtures/mt3600be/manifest-complete.txt
python3 -m unittest tests.test_validate_mt3600be_manifest -v
python3 -m py_compile scripts/validate_mt3600be_manifest.py
git diff --check
```

Commit:

```bash
git add scripts/validate_mt3600be_manifest.py tests/test_validate_mt3600be_manifest.py tests/fixtures/mt3600be
git commit -m "Gate MT3600BE firmware manifests"
```

### Task 4: Make the existing build consume a candidate lock

**Files:**
- Modify: `.github/workflows/build-wireless-router25.12.yml`
- Modify: `mediatek-filogic/build25.sh`
- Modify: `scripts/mt3600be_sources.py`
- Modify: `tests/test_mt3600be_sources.py`

**Interfaces:**
- Consumes: `build-env --lock PATH` from Task 2.
- Consumes: manifest CLI from Task 3.
- Adds workflow-dispatch inputs: `automatic_update` (`false` by default) and `source_fingerprint` (empty by default).
- Produces automatic child Artifact with firmware, manifest, SBOM, profiles, and checksums.

- [ ] **Step 1: Write failing build-env fallback tests**

Test two behaviors through the production CLI:

- with a complete lock, every ref/digest comes from the lock;
- with no lock and `--compat-run7`, output exactly the existing verified Nikki SHA `3799926b147d7065ac98508f16951f8714e53659`, daede SHA `a6c3ced3c7e095630368de96fbf9f2ba03760672`, SDK reference, and 25.12.1 ImageBuilder tag.

Reject `--compat-run7` when a lock file exists but is malformed; never hide a corrupt lock by falling back.

- [ ] **Step 2: Run fallback tests and verify RED**

Run: `python3 -m unittest tests.test_mt3600be_sources.BuildEnvironmentTests -v`

Expected: failure because fallback behavior is undefined.

- [ ] **Step 3: Implement fallback and locked build environment behavior**

Keep fallback values in one immutable Python mapping with a comment linking to successful GitHub run `33479866793`. The fallback is used only when the lock path is absent.

- [ ] **Step 4: Modify the proxy-package job**

Before replacing the workspace with upstream package sources:

1. check out the current CloudImageBuilder ref into a temporary path;
2. run `build-env` and store validated outputs;
3. check out Nikki and daede at those output SHAs;
4. pass the locked SDK reference to `openwrt/gh-action-sdk`;
5. keep the existing exactly-seven-APK and checksum assertions.

The job name and summary must print refs and versions but never tokens.

- [ ] **Step 5: Modify the firmware job**

For MT3600BE, derive the Docker ImageBuilder reference from the lock or compatibility output and pass it to `docker run`. Keep the existing hard-coded per-platform tags for every other profile.

Insert `Validate MT3600BE firmware manifest` immediately after ImageBuilder completion and before Artifact or Release steps. Locate exactly one MT3600BE `.manifest` file and run the Task 3 validator.

- [ ] **Step 6: Separate manual and automatic publication**

For `automatic_update == 'true'`:

- upload the complete firmware Artifact;
- skip `softprops/action-gh-release`;
- expose the Artifact name and source fingerprint in the job summary.

For ordinary manual dispatch, retain the current run-numbered Release behavior.

- [ ] **Step 7: Verify workflow and shell changes**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/mt3600be_sources.py scripts/validate_mt3600be_manifest.py
bash -n mediatek-filogic/build25.sh
git diff --check
```

If `actionlint` is available, also run `actionlint .github/workflows/build-wireless-router25.12.yml`. Otherwise validate the YAML with the repository's available parser and record that GitHub-side dispatch remains the authoritative workflow validation.

- [ ] **Step 8: Commit Task 4**

```bash
git add .github/workflows/build-wireless-router25.12.yml mediatek-filogic/build25.sh scripts/mt3600be_sources.py tests/test_mt3600be_sources.py
git commit -m "Build MT3600BE from locked upstream inputs"
```

### Task 5: Add the daily staging orchestrator

**Files:**
- Create: `.github/workflows/check-mt3600be-updates.yml`
- Modify: `scripts/mt3600be_sources.py`
- Modify: `tests/test_mt3600be_sources.py`

**Interfaces:**
- Consumes: resolver and fingerprint CLI from Tasks 1 and 2.
- Consumes: child build workflow from Task 4.
- Produces: staged lock commit, verified child run, complete draft Release, fast-forwarded `dev`, and published automatic Release.

- [ ] **Step 1: Write failing release identity tests**

Add functions and tests for:

```python
automatic_tag("25.12.1", "2026-09-02", "a" * 64)
# => glinet_gl-mt3600be-25.12.1-auto-20260902-aaaaaaaaaaaa

staging_branch(run_id=123, attempt=2)
# => auto-update-staging-123-2
```

Reject fingerprints, dates, run IDs, and branch names that do not match strict formats.

- [ ] **Step 2: Run identity tests and verify RED**

Run: `python3 -m unittest tests.test_mt3600be_sources.ReleaseIdentityTests -v`

Expected: failure because release and branch identity helpers are missing.

- [ ] **Step 3: Implement identity helpers and verify GREEN**

Run the same test command and confirm all cases pass.

- [ ] **Step 4: Create the scheduled/manual workflow**

Use:

```yaml
on:
  schedule:
    - cron: '20 19 * * *'
  workflow_dispatch:

concurrency:
  group: mt3600be-automatic-upstream-build
  cancel-in-progress: false
```

Jobs must implement this exact order:

1. resolve candidate and write a job summary;
2. stop successfully on `unchanged` only after release reconciliation;
3. fail on `not-ready` or `invalid` with exact mismatches;
4. create and push a strict staging branch containing the candidate lock;
5. dispatch `build-wireless-router25.12.yml` on the expected staging SHA with `profile=glinet_gl-mt3600be`, `automatic_update=true`, and the fingerprint;
6. locate the child run by workflow, branch, event, and `headSha`, then `gh run watch --exit-status`;
7. download its firmware Artifact and rerun manifest validation;
8. create or reconcile a matching draft Release and upload the complete asset set;
9. fast-forward `dev` only if its remote SHA still equals the initially observed SHA;
10. publish the complete draft;
11. clean the staging branch in an `always()` step using a strict name check.

- [ ] **Step 5: Add idempotent release reconciliation**

Before resolving a new update, inspect the current lock fingerprint:

- published complete release: continue normally;
- complete draft with the same fingerprint and `dev` already at its lock commit: publish the draft and exit successfully;
- incomplete release or draft: fail and name missing assets;
- no lock during bootstrap: continue to candidate resolution.

Required asset suffixes are `.bin`, `.manifest`, `.bom.cdx.json`, `profiles.json`, and `sha256sums`.

- [ ] **Step 6: Add guarded cleanup and promotion**

Never force-push `dev`. Promotion uses the tested full SHA and a lease against the initially observed remote `dev` SHA. Delete only `refs/heads/auto-update-staging-<current-run-id>-<current-attempt>`.

- [ ] **Step 7: Verify the orchestrator locally**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/mt3600be_sources.py
git diff --check
```

Run `actionlint .github/workflows/check-mt3600be-updates.yml .github/workflows/build-wireless-router25.12.yml` when available. Inspect the rendered YAML event keys with a YAML 1.2 parser to ensure `on` remains a mapping.

- [ ] **Step 8: Commit Task 5**

```bash
git add .github/workflows/check-mt3600be-updates.yml scripts/mt3600be_sources.py tests/test_mt3600be_sources.py
git commit -m "Automate daily MT3600BE upstream builds"
```

### Task 6: Operator documentation and full local verification

**Files:**
- Create: `docs/mt3600be-automatic-build.md`
- Modify: `docs/superpowers/specs/2026-09-02-mt3600be-automatic-upstream-build-design.md` only if implementation uncovered a real design correction.

**Interfaces:**
- Documents the resolver outcomes, source lock, Release identity, compatibility bootstrap, manual dispatch, recovery, and the boundary that firmware is not automatically flashed.

- [ ] **Step 1: Write operator documentation**

Include exact commands for a local fixture test and live read-only resolution:

```bash
python3 -m unittest discover -s tests -v
GH_TOKEN="$(gh auth token)" python3 scripts/mt3600be_sources.py resolve \
  --previous config/mt3600be-sources.lock.json \
  --output /tmp/mt3600be-candidate.json \
  --github-token-env GH_TOKEN
```

Explain that absence of the previous lock is expected only for initial bootstrap, and that `not-ready` means official dae-family changes have not yet passed the packaging upstream's gate.

- [ ] **Step 2: Run the complete fresh verification suite**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/mt3600be_sources.py scripts/validate_mt3600be_manifest.py tests/test_mt3600be_sources.py tests/test_validate_mt3600be_manifest.py
bash -n mediatek-filogic/build25.sh
git diff --check
```

Also run actionlint when available and a live resolver dry run that writes only to `/tmp`.

- [ ] **Step 3: Check requirements line by line**

Confirm from fresh outputs:

- same-version container or feed changes produce `changed`;
- stable 25.12.x upgrade selection works;
- Nikki and all daede-family inputs are covered;
- no-change exits without staging;
- missing required manifest package prevents publication;
- compatibility fallback is limited to pre-lock bootstrap;
- automatic child run cannot publish directly;
- no code path targets `master`.

- [ ] **Step 4: Commit Task 6**

```bash
git add docs/mt3600be-automatic-build.md docs/superpowers/specs/2026-09-02-mt3600be-automatic-upstream-build-design.md
git commit -m "Document MT3600BE automatic builds"
```

### Task 7: Publish to dev and perform GitHub-side verification

**Files:**
- No new source files expected.
- GitHub repository setting: confirm default branch is `dev`.

**Interfaces:**
- Consumes all locally verified commits.
- Produces remote `dev`, one manual updater run, and either a fully gated bootstrap promotion or a precise failure report.

- [ ] **Step 1: Verify repository and branch state**

Run:

```bash
git status --short --branch
git log --oneline --decorate -8
git diff --check origin/dev...HEAD
gh api repos/crafoot/CloudImageBuilder --jq '.default_branch'
```

Expected: clean worktree, local `dev` ahead only by reviewed commits, diff check clean, default branch `dev`.

- [ ] **Step 2: Push dev and confirm the exact remote SHA**

Run:

```bash
git push origin dev
git rev-parse HEAD
git ls-remote origin refs/heads/dev
```

Expected: the local and remote full SHAs match.

- [ ] **Step 3: Dispatch the daily updater manually**

Run:

```bash
gh workflow run check-mt3600be-updates.yml --ref dev
```

Find the run by workflow, branch, event, and pushed `headSha`; do not select merely the newest run.

- [ ] **Step 4: Wait for and inspect the authoritative result**

Use `gh run watch <run-id> --exit-status`, then inspect jobs, summaries, artifacts, and releases with `gh api`.

For bootstrap, expected result is a real candidate build, validated Artifact, draft-to-public Release, and fast-forwarded lock commit on `dev`. If external source readiness blocks it, report the exact upstream/pin mismatch and leave the automation active for the next daily retry.

- [ ] **Step 5: Verify publication contents**

Download the published manifest and assert the six required runtime packages with the production manifest validator. Confirm the Release body contains full source identity and the workflow Artifact contains the complete firmware directory.

- [ ] **Step 6: Confirm schedule and branch invariants**

Run:

```bash
gh api repos/crafoot/CloudImageBuilder --jq '.default_branch'
git ls-remote origin refs/heads/master refs/heads/dev
```

Confirm default is `dev`, `master` was not modified by automation work, no stale automation staging branch remains, and the next scheduled run is enabled.

- [ ] **Step 7: Final evidence report**

Report the pushed commits, resolver decision, child build run, exact locked versions/digests, Release URL, firmware Artifact URL, manifest package versions, and any remaining external readiness issue. Do not claim success unless the fresh GitHub outputs prove every required gate.
