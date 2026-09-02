# MT3600BE automatic upstream build design

Date: 2026-09-02

## Objective

Automatically check once per day whether any build input for the GL.iNet
GL-MT3600BE firmware has changed. Build and publish a new firmware only when
the complete candidate input set differs from the last successful build.

The detector must notice both visible version upgrades, such as ImmortalWrt
25.12.1 to 25.12.2, and changes that retain the same displayed version, such
as a replaced container image, an updated package index, or a new upstream
source commit.

The system must never advance the recorded state or replace a working release
until the candidate packages and firmware have passed all gates.

## Scope

This automation applies only to the `glinet_gl-mt3600be` profile on the `dev`
branch. The `master` branch remains an upstream-sync branch and receives no
automation-specific files or commits.

The repository default branch will be changed from `master` to `dev` after the
locally verified workflow has been pushed and its remote `dev` SHA confirmed.
GitHub scheduled workflows execute from the default branch, so this setting is
required for the daily workflow to remain isolated from `master`.

The work does not add unattended flashing to the router. It produces and
publishes a candidate firmware for manual installation.

## Definitions

### Latest stable ImmortalWrt 25.12.x

The resolver selects the highest numeric `25.12.x` release for which both of
these build inputs exist for the required architecture:

- `immortalwrt/imagebuilder:mediatek-filogic-openwrt-<version>`
- `immortalwrt/sdk:aarch64_cortex-a53-openwrt-<version>`

Snapshot, release-candidate, prerelease, and unversioned rolling tags are not
eligible. A version is not ready until both images resolve to immutable image
digests.

### Ready daede source

`kenzok8/openwrt-daede` already tracks `daeuniverse/dae`,
`daeuniverse/daed`, and `daeuniverse/dae-wing`, assembles pinned source
archives, and promotes its `main` branch only after a real SDK build passes.

The resolver records the official upstream heads as well as the pins and
package tree hashes in `openwrt-daede`. If an official upstream head is newer
than the corresponding pin, the candidate is reported as not ready. The
automation must not publish a build containing the old package while claiming
to contain the latest source. A later daily run retries after the packaging
upstream has completed its gate.

Changes to the daede packaging itself, its performance dependencies, or its
source archives are detected through the relevant Git tree and source hashes,
even when the user-facing package version remains unchanged.

### Source fingerprint

The source fingerprint is the SHA-256 of the canonical candidate lock JSON.
Canonical form uses sorted object keys, stable array ordering, UTF-8, and no
insignificant whitespace. The fingerprint changes if any monitored build
input changes.

## Monitored inputs

The candidate lock contains these groups.

### ImmortalWrt

- selected stable `25.12.x` version
- release/source revision when published by the selected build input
- ImageBuilder tag and immutable digest
- SDK tag and immutable digest
- target, architecture, and device profile
- the exact repository index URLs used by the ImageBuilder
- SHA-256 for every repository index
- a combined, deterministically ordered repository-index digest

For an existing version, the resolver refreshes the URLs recorded in the last
successful lock. For a new version it derives the release URLs, then the build
gate verifies that the selected ImageBuilder uses the same repository set.
An unavailable, malformed, or mismatched repository index makes the candidate
invalid instead of being treated as "no change".

### Nikki

- `nikkinikki-org/OpenWrt-nikki` resolved commit
- Git tree hashes for `nikki`, `luci-app-nikki`, and `mihomo-meta`
- package versions parsed from the corresponding Makefiles
- source hashes or source revisions declared by those Makefiles

Only package-relevant tree changes trigger a build; documentation-only changes
outside these trees do not.

### daede, dae, and daed

- `kenzok8/openwrt-daede` resolved commit
- Git tree hashes for `dae`, `daed`, and `luci-app-daede`
- hash of `ci/pins.env`
- package versions, releases, source archive names, and source hashes
- official heads for `daeuniverse/dae`, `daeuniverse/daed`, and
  `daeuniverse/dae-wing`
- the corresponding upstream pins declared by `ci/pins.env`

The official heads and declared pins must agree before the source is considered
ready. Full 40-character commit SHAs and full container digests are required.

## Repository artifacts

The implementation introduces these conceptual units.

### Successful lock

`config/mt3600be-sources.lock.json` records only the last candidate that passed
the complete package, firmware, manifest, and draft-asset gates. It is
committed to `dev` and is the default source set for manual MT3600BE builds.
The daily workflow reconciles the matching release before treating this lock
as fully settled, which permits recovery if GitHub fails between branch
promotion and publishing an already complete draft.

The file contains source identity only. It contains no credentials, generated
firmware, transient run IDs, timestamps used solely for presentation, or
mutable `latest` references without their resolved digest or SHA.

### Initial bootstrap

Before the first automatic promotion, `dev` has no complete successful lock
because the historical Run #7 did not record every repository-index digest.
During this short transition, an ordinary manual MT3600BE run continues to use
the already verified Run #7 pins. The first daily/manual updater run treats the
missing lock as `changed`, writes a complete candidate lock only on its staging
branch, and performs the full build gate. The lock reaches `dev` only through
that successful promotion. Once the complete lock exists, MT3600BE builds no
longer use the compatibility fallback.

### Resolver

A standalone script resolves upstream metadata, validates formats and source
relationships, emits canonical candidate JSON, and compares it with the
successful lock. Network access and JSON inputs are separated from comparison
logic so deterministic fixture tests can exercise the selection behavior.

Resolver outcomes are:

- `unchanged`: candidate is valid and has the same fingerprint
- `changed`: candidate is valid and differs from the successful lock
- `not-ready`: a newer upstream exists but matching tested packaging or build
  inputs are not yet available
- `invalid`: metadata is missing, malformed, inconsistent, or unverifiable

Only `changed` may enter the build gate. `not-ready` and `invalid` must not
silently fall back to the old source set.

### Daily orchestrator

`.github/workflows/check-mt3600be-updates.yml` runs once per day at an
off-peak time close to 03:20 Asia/Singapore, plus manual dispatch for testing.
Its concurrency group allows only one MT3600BE update promotion at a time and
does not cancel an in-progress build.

The workflow uses the minimum token permissions required by each job. It
validates all upstream identifiers before using them in Git, container, or API
operations.

### Existing firmware workflow

The existing 25.12 wireless-router workflow continues to support all current
manual profiles. For MT3600BE it reads source refs and image digests from the
lock file instead of hard-coded values. Other profiles retain their current
behavior.

An automatic run is dispatched from the exact staging commit and carries an
explicit automatic-build marker. The workflow verifies that its actual
`headSha` is the expected staging SHA before the result is eligible for
promotion.

## Candidate-to-release flow

1. Check out the current `dev` head with full history.
2. Resolve and validate the complete candidate input set.
3. If unchanged, write a concise job summary and exit successfully.
4. If not ready or invalid, record the exact mismatch and fail without
   modifying repository state.
5. If changed, create a uniquely named `auto-update-staging-*` branch.
6. Write the candidate lock on the staging branch and commit it with the list
   of changed input groups.
7. Push the staging branch and verify its remote SHA.
8. Dispatch the existing wireless-router workflow on that exact branch for
   `glinet_gl-mt3600be`.
9. Find the dispatched run by both branch and expected `headSha`, then wait for
   completion.
10. Verify that the run used the expected SHA and completed successfully.
11. Download and verify package artifacts and the firmware manifest.
12. Create or reconcile a hidden draft Release for the candidate fingerprint,
    and upload the complete, verified firmware asset set to that draft.
13. Fast-forward `dev` from its previously observed head to the exact tested
    staging SHA. If `dev` moved concurrently, abort promotion and rebuild from
    the new head rather than force-pushing.
14. Confirm the remote `dev` SHA, publish the complete draft Release, and then
    delete the temporary staging branch.

If execution stops after `dev` promotion but before draft publication, the next
daily run detects the matching complete draft and publishes it before applying
the normal unchanged decision. If execution stops before `dev` promotion, the
draft remains hidden and is either reused after revalidation or removed by the
strictly scoped cleanup path. A partially populated draft is never published.

Cleanup runs on success and failure. It deletes only a branch whose name
matches the strict automation prefix and current run identity.

## Build and publication gates

The proxy package job must produce exactly one matching APK for each required
package:

- `nikki`
- `luci-app-nikki`
- `luci-i18n-nikki-zh-cn`
- `mihomo-meta`
- `dae`
- `daed`
- `luci-app-daede`

Their checksums are generated and rechecked before they are staged into the
ImageBuilder.

The resulting MT3600BE manifest must contain all runtime packages below with
non-empty versions:

- `nikki`
- `luci-app-nikki`
- `mihomo-meta`
- `dae`
- `daed`
- `luci-app-daede`

The automatic child build uploads a workflow Artifact only after this manifest
gate and skips its normal public Release step. The daily orchestrator downloads
that Artifact, rechecks it, and prepares the draft Release. Manual builds keep
their existing direct Release behavior.

Publication must contain the sysupgrade image, manifest, SBOM, profiles, and
checksums already generated by the ImageBuilder.

## Release identity and idempotency

Automatic releases use a tag shaped like:

`glinet_gl-mt3600be-<25.12.x>-auto-<YYYYMMDD>-<fingerprint-prefix>`

The release title and body list the complete locked versions and identifiers
and describe which input groups changed from the preceding successful lock.
The full fingerprint is included in release metadata.

Before publishing, the workflow checks for an existing release or draft
carrying the same full fingerprint. If a published release exists and its
required assets are complete, the run reuses that result rather than creating
a duplicate. A complete matching draft is eligible for the recovery flow. An
incomplete release or draft is never silently overwritten or published.

Manual builds retain their run-numbered naming and do not advance the automatic
successful lock unless they were dispatched by the daily orchestrator from an
automation staging branch.

## Failure behavior

Resolver, build, artifact, draft-asset, or concurrent-promotion failures occur
before `dev` is advanced and therefore preserve the previous successful lock,
`dev` head, and published releases. A transport failure after `dev` promotion
can only leave a complete matching draft unpublished; the next run reconciles
that recoverable state before doing new update work. Every failure summary
states:

- resolver outcome
- old and candidate fingerprints when available
- changed or mismatched inputs
- expected and actual staging/run SHAs
- failed package or manifest requirement
- whether any temporary branch remains

The next daily run resolves upstream state again and retries. The design does
not force-push `dev`, overwrite an existing release, choose an older build
input as a fallback, or publish a partially verified firmware.

## Testing strategy

Resolver and comparison behavior is developed test-first with local fixtures.
Tests exercise the real scripts with controlled JSON and file inputs rather
than asserting that source files contain particular strings.

Required cases include:

- highest stable `25.12.x` wins
- Snapshot, RC, prerelease, and unrelated versions are rejected
- the same displayed version with a different ImageBuilder digest triggers
- the same displayed version with a different SDK digest triggers
- a repository-index digest change triggers
- each Nikki package subtree can independently trigger
- each daede package subtree, source archive, or upstream pin can independently
  trigger
- unchanged canonical inputs do not trigger
- official dae/daed/dae-wing ahead of packaging produces `not-ready`
- missing, abbreviated, or malformed SHA/digest produces `invalid`
- canonicalization is stable across JSON key ordering
- manifest validation fails independently for every required package
- a failed build cannot update the successful lock
- a concurrent `dev` movement prevents promotion
- repeated handling of an existing complete fingerprint is idempotent

Static verification includes shell syntax checks, workflow YAML validation,
`git diff --check`, and an action linter when available. A live resolver dry
run validates current external metadata without mutating the repository.

The repository default branch was changed to `dev` by the repository owner
before implementation. No scheduled workflow exists at that point, so the
early setting change cannot start an incomplete run. After local tests pass,
the changes are pushed to `dev` and their remote commit is confirmed. The daily
workflow is then manually dispatched once. The GitHub-side validation must
confirm a fully gated bootstrap build and promotion; subsequent validation may
also produce a clean `unchanged` result. If GitHub rejects the workflow
definition itself, the default branch can be restored to `master` while the
definition is corrected; `master` content remains untouched.

## Security and operational constraints

- No upstream-provided shell content executes in the resolver job.
- Repository names, branches, paths, and registry locations are allowlisted.
- Source checkouts use resolved full SHAs.
- Container use records and verifies immutable digests.
- Secrets and router credentials never enter locks, artifacts, summaries, or
  release metadata.
- `master` is never an automation promotion target.
- Automatic publication does not authorize automatic router flashing.

## Non-goals

- tracking ImmortalWrt Snapshot builds
- upgrading outside the stable 25.12.x series
- automatically flashing or rebooting the router
- changing package selections for non-MT3600BE profiles
- maintaining a private fork of dae, daed, or Nikki when their packaging
  upstream has not produced a buildable candidate
