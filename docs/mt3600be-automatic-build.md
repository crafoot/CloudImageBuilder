# GL-MT3600BE automatic upstream builds

This guide covers the automated updater for the `glinet_gl-mt3600be` profile
on the `dev` branch. It builds a release candidate only when its complete,
resolved source set has changed. It never flashes, reboots, or otherwise
changes a router.

## What the updater watches

The resolver selects the highest stable numeric `25.12.x` shared by the
required ImageBuilder and A53 SDK tag families, then resolves both to immutable
digests and records a canonical source lock. Its fingerprint covers the
selected ImmortalWrt release and commit, the ImageBuilder and SDK
tag-plus-digest references, the three ImageBuilder feed indexes and their
hashes, and the package-relevant Nikki and daede source trees. For daede, it
also records the dae, daed, and dae-wing heads and the packaging repository's
corresponding pins.

The daily workflow runs at `20:19 UTC` (`03:20` Asia/Singapore on the next
calendar day). It runs only from the current `dev` head, never targets
`master`, and permits only one update promotion at a time.

The resolver prints one JSON outcome:

- `unchanged`: the valid candidate fingerprint matches the last successful
  lock. No staging branch or child build is created.
- `changed`: a valid candidate differs from the successful lock. Only this
  outcome may enter the build gate.
- `not-ready`: an official dae-family head is newer than the pin promoted by
  `kenzok8/openwrt-daede`. Wait for that packaging upstream to complete its
  build gate; do not label the previous package as the newer source.
- `invalid`: metadata, a digest/SHA, a required feed, or another consistency
  check could not be verified. The workflow fails closed and keeps the prior
  lock and release.

## Successful lock and initial bootstrap

`config/mt3600be-sources.lock.json` is the successful source identity. It is
created on an isolated staging branch and reaches `dev` only after the child
build, package gate, firmware-manifest gate, draft-asset gate, and exact-SHA
promotion all succeed.

The absence of this file is expected only during the first bootstrap. In that
case the updater treats the candidate as `changed` and creates the first
complete lock through the normal gated flow. A **manual** MT3600BE build can
use the compatibility values from verified Run #7 only while the lock path is
absent. A present but malformed lock is an error and never falls back. An
automatic child build always requires a present lock and its exact source
fingerprint.

Once the lock exists, use it as the source of truth for manual MT3600BE
builds; do not edit it to force an update.

## Automated lifecycle and release identity

For a `changed` decision, the updater:

1. creates `auto-update-staging-<run-id>-<attempt>` from the observed `dev`
   SHA and commits the candidate lock there;
2. confirms the remote staging SHA and dispatches the existing 25.12 wireless
   router workflow on that exact branch with `automatic_update=true` and the
   full source fingerprint;
3. accepts the child only when its completed run, branch, and `headSha` all
   equal the expected values;
4. verifies the seven pinned proxy APKs, the MT3600BE firmware manifest, the
   checksums, profiles JSON, SBOM, and the complete artifact set;
5. creates or reconciles a hidden draft Release, fast-forwards `dev` from its
   original SHA to the tested staging SHA using a lease, confirms the remote
   SHA, then publishes the complete draft; and
6. deletes only the exact current-run staging branch.

Automatic releases use:

```
glinet_gl-mt3600be-<25.12.x>-auto-<YYYYMMDD>-<fingerprint-prefix>
```

Their notes contain the full source fingerprint, locked input identifiers,
changed input groups, and the tested staging commit. Required public assets
are a sysupgrade `.bin`, `.manifest`, `.bom.cdx.json`, `profiles.json`, and
`sha256sums`. A matching complete published release is reused; a matching
complete draft is eligible for recovery. An incomplete release or draft is
never published or overwritten silently.

## Manual operation

Use the **Check MT3600BE upstream updates** workflow on `dev` to request the
same gated resolution flow outside its daily schedule:

```bash
gh workflow run check-mt3600be-updates.yml --ref dev
```

Use **Build 25.12.x Wireless Router** when an ordinary manual firmware build
is required. Select `glinet_gl-mt3600be`, keep `automatic_update` set to
`false`, and leave `source_fingerprint` empty. That is a normal run-numbered
manual release; it does not advance the automatic successful lock. Supply the
other router-build inputs through the Actions form as for any manual router
build.

Do not manually set `automatic_update=true`. It is reserved for the updater's
staging branch and requires both a regular lock file and a fingerprint that
matches it exactly. In automatic mode, the child workflow uploads only a
workflow Artifact; it cannot create a public Release directly.

## Local checks

Run the fixture suite before changing the workflow or the resolver:

```bash
python3 -m unittest discover -s tests -v
```

It covers same-version ImageBuilder/SDK/feed changes, stable-version handling,
Nikki and daede-family input groups, unchanged comparison, the bootstrap
fallback boundary, manifest rejection for each required package, and the
automatic workflow contracts.

The resolver can be exercised without a network by using repository fixtures:

```bash
python3 scripts/mt3600be_sources.py resolve \
  --fixture tests/fixtures/mt3600be/github-responses.json \
  --previous tests/fixtures/mt3600be/previous-lock.json \
  --output /tmp/mt3600be-fixture-candidate.json
python3 scripts/mt3600be_sources.py build-env \
  --lock /tmp/mt3600be-fixture-candidate.json
```

For a read-only live resolution, obtain an authenticated GitHub CLI session
first, then write the candidate only outside the repository:

```bash
GH_TOKEN="$(gh auth token)" python3 scripts/mt3600be_sources.py resolve \
  --previous config/mt3600be-sources.lock.json \
  --output /tmp/mt3600be-candidate.json \
  --github-token-env GH_TOKEN
```

During initial bootstrap, replace the missing `--previous` path with `-`:

```bash
GH_TOKEN="$(gh auth token)" python3 scripts/mt3600be_sources.py resolve \
  --previous - \
  --output /tmp/mt3600be-candidate.json \
  --github-token-env GH_TOKEN
```

The live command writes only `/tmp/mt3600be-candidate.json`; it does not
commit, dispatch, publish, or flash anything.

## Recovery and safety boundaries

- For `not-ready`, wait for `kenzok8/openwrt-daede` to catch up with the
  official dae-family heads, then rerun the resolver. Do not downgrade an
  upstream head or modify a pin to bypass the gate.
- For `invalid`, retain the existing lock and release. Read the workflow
  summary/error, correct the upstream availability or verified configuration
  problem, then retry. Do not substitute an unverified digest, abbreviated
  SHA, missing feed, or older input.
- If a child build or artifact gate fails, the tested lock is not promoted to
  `dev`. The updater cleans only its own current-run staging branch.
- If `dev` was promoted but a transport failure prevented publication, the
  next updater run first finds the complete matching hidden draft and publishes
  it against the confirmed `dev` SHA. Do not delete or publish that draft by
  hand.
- If a successful lock has no matching complete release, reconciliation fails
  closed; investigate the missing release rather than creating a substitute.
- Before installing a release yourself, download its assets, verify
  `sha256sums`, inspect the `.manifest`, confirm the intended device/profile,
  and follow the router's manual upgrade procedure. This project deliberately
  contains no unattended flashing or reboot path.
