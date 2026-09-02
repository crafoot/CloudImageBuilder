import base64
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.request

from scripts import mt3600be_sources as sources

from scripts.mt3600be_sources import (
    FixtureTransport,
    Transport,
    automatic_tag,
    canonical_bytes,
    compare_states,
    fingerprint,
    resolve_candidate,
    select_stable_25_12,
    staging_branch,
    validate_staging_branch,
    validate_digest,
    validate_sha,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "mt3600be"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


class ReleaseIdentityTests(unittest.TestCase):
    def test_builds_automatic_release_tag_from_strict_inputs(self):
        self.assertEqual(
            automatic_tag("25.12.1", "2026-09-02", "a" * 64),
            "glinet_gl-mt3600be-25.12.1-auto-20260902-aaaaaaaaaaaa",
        )

    def test_rejects_invalid_release_versions(self):
        for version in ("25.12", "25.13.1", "25.12.1-rc1", 251201):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "25.12.x"):
                automatic_tag(version, "2026-09-02", "a" * 64)

    def test_rejects_invalid_or_impossible_release_dates(self):
        for release_date in ("20260902", "2026-9-02", "2026-02-30", 20260902):
            with self.subTest(release_date=release_date), self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                automatic_tag("25.12.1", release_date, "a" * 64)

    def test_rejects_noncanonical_release_fingerprints(self):
        for source_fingerprint in ("a" * 63, "A" * 64, "g" * 64, None):
            with self.subTest(source_fingerprint=source_fingerprint), self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
                automatic_tag("25.12.1", "2026-09-02", source_fingerprint)

    def test_builds_staging_branch_from_positive_integer_run_identity(self):
        self.assertEqual(staging_branch(run_id=123, attempt=2), "auto-update-staging-123-2")

    def test_rejects_invalid_run_identity(self):
        for run_id, attempt in ((0, 1), (1, 0), (-1, 1), (1, -1), ("123", 2), (123, "2"), (True, 1)):
            with self.subTest(run_id=run_id, attempt=attempt), self.assertRaisesRegex(ValueError, "positive integer"):
                staging_branch(run_id=run_id, attempt=attempt)

    def test_validates_only_strict_automation_staging_branch_names(self):
        self.assertEqual(validate_staging_branch("auto-update-staging-123-2"), "auto-update-staging-123-2")
        for branch in (
            "auto-update-staging-123",
            "auto-update-staging-123-0",
            "auto-update-staging-0123-2",
            "auto-update-staging-123-2/extra",
            "refs/heads/auto-update-staging-123-2",
            123,
        ):
            with self.subTest(branch=branch), self.assertRaisesRegex(ValueError, "staging branch"):
                validate_staging_branch(branch)


class Mt3600beSourcesTests(unittest.TestCase):
    def test_selects_highest_stable_25_12(self):
        tags = ["25.12.1", "25.12.3", "25.12.2", "25.12-SNAPSHOT", "25.12.4-rc1", "26.01.0"]
        self.assertEqual(select_stable_25_12(tags), "25.12.3")

    def test_rejects_abbreviated_sha(self):
        with self.assertRaisesRegex(ValueError, "40-character"):
            validate_sha("3799926")

    def test_rejects_abbreviated_tree_or_pin_commit_in_canonical_state(self):
        for state in ({"tree_sha": "short"}, {"DAE_COMMIT": "short"}, {"QUICGO_PERF_TIP": "short"}):
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, "40-character"):
                fingerprint(state)

    def test_rejects_non_sha256_container_digest(self):
        with self.assertRaisesRegex(ValueError, "sha256"):
            validate_digest("sha1:" + "a" * 40)

    def test_canonicalization_ignores_object_key_order(self):
        self.assertEqual(fingerprint({"b": 2, "a": 1}), fingerprint({"a": 1, "b": 2}))
        self.assertEqual(canonical_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}')

    def test_presentation_fields_do_not_change_fingerprint(self):
        self.assertEqual(
            fingerprint({"schema": 1, "version": "25.12.1", "display": "first"}),
            fingerprint({"schema": 1, "version": "25.12.1", "display": "second"}),
        )

    def test_missing_previous_lock_is_changed(self):
        decision, groups = compare_states(None, {"schema": 1, "immortalwrt": {"version": "25.12.1"}})
        self.assertEqual(decision, "changed")
        self.assertIn("bootstrap", groups)

    def test_same_version_new_image_digest_is_changed(self):
        decision, groups = compare_states(load_fixture("previous-lock.json"), load_fixture("candidate-imagebuilder-changed.json"))
        self.assertEqual(decision, "changed")
        self.assertIn("immortalwrt.imagebuilder", groups)

    def test_changed_immortalwrt_commit_is_changed(self):
        previous = {"schema": 1, "immortalwrt": {"version": "25.12.1", "commit": "a" * 40}}
        candidate = {"schema": 1, "immortalwrt": {"version": "25.12.1", "commit": "b" * 40}}
        decision, groups = compare_states(previous, candidate)
        self.assertEqual(decision, "changed")
        self.assertIn("immortalwrt.commit", groups)

    def test_uppercase_references_compare_as_equal(self):
        previous = {"schema": 1, "immortalwrt": {"version": "25.12.1", "commit": "a" * 40, "imagebuilder": {"digest": "sha256:" + "b" * 64}}}
        candidate = {"schema": 1, "immortalwrt": {"version": "25.12.1", "commit": "A" * 40, "imagebuilder": {"digest": "SHA256:" + "B" * 64}}}
        self.assertEqual(compare_states(previous, candidate), ("unchanged", []))

    def test_same_state_is_unchanged(self):
        decision, groups = compare_states(load_fixture("previous-lock.json"), load_fixture("candidate-same.json"))
        self.assertEqual(decision, "unchanged")
        self.assertEqual(groups, [])

    def test_presentation_change_inside_group_is_unchanged(self):
        previous = {"schema": 1, "immortalwrt": {"version": "25.12.1", "imagebuilder": {"digest": "sha256:" + "a" * 64, "display": "old"}}}
        candidate = {"schema": 1, "immortalwrt": {"version": "25.12.1", "imagebuilder": {"digest": "sha256:" + "a" * 64, "display": "new"}}}
        self.assertEqual(compare_states(previous, candidate), ("unchanged", []))

    def test_cli_compare_returns_json_and_success(self):
        result = subprocess.run(
            [sys.executable, "scripts/mt3600be_sources.py", "compare", "--previous", str(FIXTURES / "previous-lock.json"), "--candidate", str(FIXTURES / "candidate-same.json")],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "unchanged")
        self.assertIn("fingerprint", output)
        self.assertEqual(output["changed_groups"], [])

    def test_cli_not_ready_returns_status_schema_and_exit_2(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as candidate:
            json.dump({"schema": 1, "immortalwrt": {"version": "26.01.0"}}, candidate)
            candidate.flush()
            result = subprocess.run([sys.executable, "scripts/mt3600be_sources.py", "compare", "--previous", "-", "--candidate", candidate.name], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "not-ready")
        self.assertIn("fingerprint", output)
        self.assertIn("changed_groups", output)

    def test_cli_invalid_reference_or_json_returns_status_schema_and_exit_3(self):
        cases = [("reference", '{"schema":1,"immortalwrt":{"version":"25.12.1","commit":"short"}}'), ("json", "{")]
        for _, contents in cases:
            with self.subTest(contents=contents), tempfile.NamedTemporaryFile(mode="w", suffix=".json") as candidate:
                candidate.write(contents)
                candidate.flush()
                result = subprocess.run([sys.executable, "scripts/mt3600be_sources.py", "compare", "--previous", "-", "--candidate", candidate.name], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 3)
            output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "invalid")
        self.assertIn("fingerprint", output)
        self.assertIn("changed_groups", output)


class BuildEnvironmentTests(unittest.TestCase):
    def _lock(self):
        return {
            "schema": 1,
            "immortalwrt": {
                "version": "25.12.1",
                "imagebuilder": {"repository": "immortalwrt/imagebuilder", "tag": "mediatek-filogic-openwrt-25.12.1", "digest": "sha256:" + "a" * 64},
                "sdk": {"repository": "immortalwrt/sdk", "tag": "aarch64_cortex-a53-openwrt-25.12.1", "digest": "sha256:" + "b" * 64},
            },
            "nikki": {"commit": "c" * 40},
            "daede": {"commit": "d" * 40},
        }

    def _build_env(self, lock, *extra_args):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as output:
            json.dump(lock, output)
            output.flush()
            return subprocess.run(
                [sys.executable, "scripts/mt3600be_sources.py", "build-env", "--lock", output.name, *extra_args],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

    def _assert_rejected_without_environment_lines(self, result):
        self.assertEqual(result.returncode, 3)
        self.assertFalse(any("=" in line and line.split("=", 1)[0].isupper() for line in result.stdout.splitlines()))

    def test_build_env_rejects_wrong_image_repository(self):
        lock = self._lock()
        lock["immortalwrt"]["imagebuilder"]["repository"] = "example.invalid/imagebuilder"
        self._assert_rejected_without_environment_lines(self._build_env(lock))

    def test_build_env_rejects_newline_repository_injection(self):
        lock = self._lock()
        lock["immortalwrt"]["sdk"]["repository"] = "immortalwrt/sdk\nINJECTED=1"
        self._assert_rejected_without_environment_lines(self._build_env(lock))

    def test_build_env_emits_every_locked_reference_and_digest(self):
        result = self._build_env(self._lock())
        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual(values["IMMORTAL_VERSION"], "25.12.1")
        self.assertEqual(values["IMAGEBUILDER_REFERENCE"], "immortalwrt/imagebuilder:mediatek-filogic-openwrt-25.12.1@sha256:" + "a" * 64)
        self.assertEqual(values["SDK_ARCH_REFERENCE"], "immortalwrt/sdk:aarch64_cortex-a53-openwrt-25.12.1@sha256:" + "b" * 64)
        self.assertEqual(values["NIKKI_REF"], "c" * 40)
        self.assertEqual(values["DAEDE_REF"], "d" * 40)

    def test_compatibility_run7_is_used_only_when_the_lock_path_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            absent_lock = pathlib.Path(directory) / "candidate-lock.json"
            result = subprocess.run(
                [sys.executable, "scripts/mt3600be_sources.py", "build-env", "--lock", str(absent_lock), "--compat-run7"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual(values["IMMORTAL_VERSION"], "25.12.1")
        self.assertEqual(values["IMAGEBUILDER_REFERENCE"], "immortalwrt/imagebuilder:mediatek-filogic-openwrt-25.12.1")
        self.assertEqual(
            values["SDK_ARCH_REFERENCE"],
            "immortalwrt/sdk:aarch64_cortex-a53-openwrt-25.12.1@sha256:441f8093008b41301881af4a3ba52e470c3ae579d423445274cf7051048a8eb6",
        )
        self.assertEqual(values["NIKKI_REF"], "3799926b147d7065ac98508f16951f8714e53659")
        self.assertEqual(values["DAEDE_REF"], "a6c3ced3c7e095630368de96fbf9f2ba03760672")

    def test_compatibility_run7_does_not_mask_a_malformed_present_lock(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as lock:
            lock.write("{")
            lock.flush()
            result = subprocess.run(
                [sys.executable, "scripts/mt3600be_sources.py", "build-env", "--lock", lock.name, "--compat-run7"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        self._assert_rejected_without_environment_lines(result)

    def test_build_env_rejects_generic_container_tags_for_mt3600be(self):
        lock = self._lock()
        lock["immortalwrt"]["imagebuilder"]["tag"] = "25.12.1"
        self._assert_rejected_without_environment_lines(self._build_env(lock))


class WorkflowContractTests(unittest.TestCase):
    WORKFLOW = ROOT / ".github" / "workflows" / "build-wireless-router25.12.yml"

    @classmethod
    def setUpClass(cls):
        cls.workflow = cls.WORKFLOW.read_text(encoding="utf-8")

    def test_automatic_update_requires_mt3600be_and_a_present_lock(self):
        self.assertIn(
            'if [[ "$AUTOMATIC_UPDATE" == \'true\' && "$PROFILE" != \'glinet_gl-mt3600be\' ]]; then',
            self.workflow,
        )
        self.assertIn(
            'if [[ "$AUTOMATIC_UPDATE" == \'true\' && ! -f "$lock_path" ]]; then',
            self.workflow,
        )

    def test_automatic_update_never_enables_compatibility_fallback(self):
        self.assertIn(
            'if [[ "$AUTOMATIC_UPDATE" != \'true\' && ! -e "$lock_path" ]]; then',
            self.workflow,
        )

    def test_package_job_checks_automatic_fingerprint_before_building(self):
        package_job = self.workflow.split("  build:\n", 1)[0]
        self.assertIn('REQUESTED_SOURCE_FINGERPRINT: ${{ github.event.inputs.source_fingerprint }}', package_job)
        self.assertIn('Automatic build source fingerprint does not match the validated lock', package_job)

    def test_sdk_action_receives_locked_tag_and_digest_suffix_unchanged(self):
        self.assertIn('sdk_action_arch="${sdk_reference#immortalwrt/sdk:}"', self.workflow)
        self.assertNotIn('sdk_action_arch="aarch64_cortex-a53-openwrt-${sdk_version}@${sdk_digest}"', self.workflow)


class OrchestratorWorkflowContractTests(unittest.TestCase):
    WORKFLOW = ROOT / ".github" / "workflows" / "check-mt3600be-updates.yml"

    @classmethod
    def setUpClass(cls):
        cls.workflow = cls.WORKFLOW.read_text(encoding="utf-8")
        cls.step_names = re.findall(r"^      - name: (.+)$", cls.workflow, re.MULTILINE)

    def _step(self, name):
        marker = f"      - name: {name}\n"
        start = self.workflow.index(marker)
        next_step = self.workflow.find("\n      - name: ", start + len(marker))
        return self.workflow[start:] if next_step == -1 else self.workflow[start:next_step]

    def test_has_daily_manual_trigger_and_non_cancelling_concurrency(self):
        self.assertRegex(self.workflow, r"(?m)^on:\n  schedule:\n    - cron: '20 19 \* \* \*'\n  workflow_dispatch:\s*$")
        self.assertRegex(
            self.workflow,
            r"(?m)^concurrency:\n  group: mt3600be-automatic-upstream-build\n  cancel-in-progress: false$",
        )

    def test_orchestration_steps_preserve_required_order(self):
        required = [
            "Require the dev branch and capture its initial remote SHA",
            "Reconcile the current successful lock release",
            "Resolve and summarize the candidate",
            "Create and push the candidate staging branch",
            "Dispatch the exact staging build",
            "Locate and watch the exact child run",
            "Download and verify the firmware artifact",
            "Create or reconcile the complete draft release",
            "Promote the exact tested commit to dev",
            "Publish the complete matching draft release",
            "Clean the exact current-run staging branch",
        ]
        positions = [self.step_names.index(name) for name in required]
        self.assertEqual(positions, sorted(positions))

    def test_only_changed_can_enter_staging_and_fail_closed_outcomes_are_named(self):
        resolve = self._step("Resolve and summarize the candidate")
        stage = self._step("Create and push the candidate staging branch")
        self.assertIn("not-ready", resolve)
        self.assertIn("invalid", resolve)
        self.assertIn("exit 1", resolve)
        self.assertIn("steps.resolve.outputs.decision == 'changed'", stage)

    def test_existing_lock_without_matching_release_fails_closed(self):
        reconcile = self._step("Reconcile the current successful lock release")
        self.assertIn("has no matching release", reconcile)
        no_match = reconcile.split('if [[ "$match_count" -eq 0 ]]', 1)[1].split("\n          fi\n", 1)[0]
        self.assertIn("exit 1", no_match)
        self.assertNotIn("stop=false", no_match)

    def test_child_run_identity_binds_workflow_branch_event_and_exact_sha(self):
        dispatch = self._step("Dispatch the exact staging build")
        locate = self._step("Locate and watch the exact child run")
        self.assertIn("build-wireless-router25.12.yml", dispatch)
        self.assertIn("profile=glinet_gl-mt3600be", dispatch)
        self.assertIn("automatic_update=true", dispatch)
        self.assertIn("source_fingerprint=", dispatch)
        self.assertIn('--workflow "$CHILD_WORKFLOW"', locate)
        self.assertIn('--branch "$STAGING_BRANCH"', locate)
        self.assertIn("--event workflow_dispatch", locate)
        self.assertIn("--commit \"$STAGING_SHA\"", locate)
        self.assertIn("gh run watch", locate)
        self.assertIn(".headSha == $sha", locate)

    def test_artifact_and_release_gates_cover_every_required_asset(self):
        verify = self._step("Download and verify the firmware artifact")
        draft = self._step("Create or reconcile the complete draft release")
        publish = self._step("Publish the complete matching draft release")
        for suffix in (".bin", ".manifest", ".bom.cdx.json", "profiles.json", "sha256sums"):
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, verify)
                self.assertIn(suffix, draft)
                self.assertIn(suffix, publish)
        self.assertIn("validate_mt3600be_manifest.py", verify)
        self.assertIn("--draft", draft)
        self.assertIn("--draft=false", publish)

    def test_existing_draft_requires_exact_current_asset_set_then_clobbers_from_current_artifact(self):
        draft = self._step("Create or reconcile the complete draft release")
        compare_assets = 'asset_set_delta="$(comm -3 "$current_asset_names_path" "$release_asset_names_path")"'
        upload_current = 'gh release upload "$release_tag" "${asset_files[@]}" --clobber'
        self.assertIn('current_asset_names_path="$RUNNER_TEMP/current-asset-basenames.txt"', draft)
        self.assertIn('release_asset_names_path="$RUNNER_TEMP/release-asset-basenames.txt"', draft)
        self.assertIn(compare_assets, draft)
        self.assertIn('missing_on_release="$(comm -23 "$current_asset_names_path" "$release_asset_names_path")"', draft)
        self.assertIn('extra_on_release="$(comm -13 "$current_asset_names_path" "$release_asset_names_path")"', draft)
        self.assertIn("Existing draft asset set differs from current verified Artifact", draft)
        self.assertIn(upload_current, draft)
        self.assertLess(draft.index(compare_assets), draft.index(upload_current))
        self.assertLess(draft.index(upload_current), draft.index('release="$(gh release view'))
        self.assertIn('final_release_asset_names_path="$RUNNER_TEMP/final-release-asset-basenames.txt"', draft)
        self.assertIn('comm -3 "$current_asset_names_path" "$final_release_asset_names_path"', draft)

    def test_release_must_still_be_a_draft_immediately_before_promotion(self):
        draft = self._step("Create or reconcile the complete draft release")
        self.assertIn("must remain a draft until dev promotion", draft)
        self.assertIn('[[ "$(jq -r \'.isDraft\' <<< "$release")" != \'true\' ]]', draft)
        self.assertNotIn("!= 'true' &&", draft)

    def test_promotion_is_fast_forward_exact_sha_with_initial_dev_lease(self):
        promote = self._step("Promote the exact tested commit to dev")
        self.assertIn('merge-base --is-ancestor "$INITIAL_DEV_SHA" "$STAGING_SHA"', promote)
        self.assertIn('--force-with-lease="refs/heads/dev:$INITIAL_DEV_SHA"', promote)
        self.assertIn('"$STAGING_SHA:refs/heads/dev"', promote)
        self.assertIn('"$actual_dev_sha" != "$STAGING_SHA"', promote)
        self.assertNotIn("master", self.workflow)

    def test_cleanup_deletes_only_the_current_run_branch(self):
        cleanup = self._step("Clean the exact current-run staging branch")
        self.assertIn("always()", cleanup)
        self.assertIn('expected_branch="auto-update-staging-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"', cleanup)
        self.assertIn('"$STAGING_BRANCH" != "$expected_branch"', cleanup)
        self.assertIn('^auto-update-staging-[1-9][0-9]*-[1-9][0-9]*$', cleanup)
        self.assertIn('refs/heads/$STAGING_BRANCH', cleanup)
        self.assertNotRegex(self.workflow, r"(?m)^\s*(sysupgrade|mtd|firstboot|reboot|ssh|scp)\b")


class ResolverTests(unittest.TestCase):
    def _transport(self, *names):
        return FixtureTransport.from_files(*(FIXTURES / name for name in names))

    def test_transport_is_an_interface(self):
        self.assertTrue(hasattr(Transport, "get_json"))
        self.assertTrue(hasattr(Transport, "get_bytes"))

    def test_follows_bearer_challenge_and_records_exact_manifest_digest(self):
        candidate = resolve_candidate(self._transport("registry-responses.json", "feed-indexes.json", "github-responses.json", "daede-ready.json"), None)
        imagebuilder = candidate["immortalwrt"]["imagebuilder"]
        self.assertEqual(imagebuilder["tag"], "mediatek-filogic-openwrt-25.12.1")
        self.assertEqual(imagebuilder["digest"], "sha256:" + "a" * 64)
        self.assertEqual(candidate["immortalwrt"]["sdk"]["tag"], "aarch64_cortex-a53-openwrt-25.12.1")

    def test_requires_both_imagebuilder_and_sdk_tags(self):
        fixture = load_fixture("registry-responses.json")
        fixture["responses"] = [entry for entry in fixture["responses"] if "sdk/tags/list" not in entry["url"]]
        transport = FixtureTransport(fixture)
        with self.assertRaisesRegex(ValueError, "SDK"):
            resolve_candidate(transport, None)

    def test_selects_highest_shared_tag_from_second_registry_page(self):
        candidate = resolve_candidate(self._transport("registry-pagination.json", "feed-indexes.json", "github-responses.json", "daede-ready.json"), None)
        self.assertEqual(candidate["immortalwrt"]["version"], "25.12.2")
        self.assertEqual(candidate["immortalwrt"]["imagebuilder"]["tag"], "mediatek-filogic-openwrt-25.12.2")
        self.assertEqual(candidate["immortalwrt"]["sdk"]["tag"], "aarch64_cortex-a53-openwrt-25.12.2")

    def test_rejects_invalid_imagebuilder_pagination_with_precise_reason(self):
        url = "https://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list?n=100"
        transport = FixtureTransport({"responses": [{
            "url": url,
            "json": {"tags": ["mediatek-filogic-openwrt-25.12.1"]},
            "headers": {"link": "<https://example.invalid/v2/immortalwrt/imagebuilder/tags/list?n=100&last=tag>; rel=\"next\""},
        }]})
        with self.assertRaisesRegex(ValueError, "ImageBuilder.*pagination"):
            sources._resolve_registry(transport)

    def test_selects_highest_version_with_both_exact_target_and_a53_sdk_tags(self):
        candidate = resolve_candidate(self._transport("registry-shared-tags.json", "feed-indexes.json", "github-responses.json", "daede-ready.json"), None)
        self.assertEqual(candidate["immortalwrt"]["version"], "25.12.2")
        self.assertEqual(candidate["immortalwrt"]["imagebuilder"]["tag"], "mediatek-filogic-openwrt-25.12.2")
        self.assertEqual(candidate["immortalwrt"]["sdk"]["tag"], "aarch64_cortex-a53-openwrt-25.12.2")

    def test_falls_back_when_highest_target_tag_has_no_matching_a53_sdk_tag(self):
        fixture = load_fixture("registry-shared-tags.json")
        for response in fixture["responses"]:
            if "/sdk/tags/list?" in response["url"]:
                response["json"]["tags"].remove("aarch64_cortex-a53-openwrt-25.12.2")
        candidate = resolve_candidate(FixtureTransport.merge(
            FixtureTransport(fixture), FixtureTransport(load_fixture("feed-indexes.json")),
            FixtureTransport(load_fixture("github-responses.json")), FixtureTransport(load_fixture("daede-ready.json")),
        ), None)
        self.assertEqual(candidate["immortalwrt"]["version"], "25.12.1")
        self.assertEqual(candidate["immortalwrt"]["imagebuilder"]["tag"], "mediatek-filogic-openwrt-25.12.1")
        self.assertEqual(candidate["immortalwrt"]["sdk"]["tag"], "aarch64_cortex-a53-openwrt-25.12.1")

    def test_changed_feed_payload_changes_combined_digest_without_version_change(self):
        ready = self._transport("registry-responses.json", "feed-indexes.json", "github-responses.json", "daede-ready.json")
        changed = self._transport("registry-responses.json", "feed-indexes.json", "github-responses.json", "daede-ready.json")
        for response in changed.responses.values():
            for item in response:
                if item["url"].endswith("/luci/packages.adb"):
                    item["bytes"] = "changed luci feed\n"
        first = resolve_candidate(ready, None)
        second = resolve_candidate(changed, None)
        self.assertEqual(first["immortalwrt"]["version"], second["immortalwrt"]["version"])
        self.assertNotEqual(first["immortalwrt"]["feeds"]["combined_digest"], second["immortalwrt"]["feeds"]["combined_digest"])

    def test_sorts_feed_urls_before_combining(self):
        candidate = resolve_candidate(self._transport("registry-responses.json", "feed-indexes.json", "github-responses.json", "daede-ready.json"), None)
        feeds = candidate["immortalwrt"]["feeds"]
        self.assertEqual([record["url"] for record in feeds["records"]], sorted(record["url"] for record in feeds["records"]))

    def test_missing_feed_is_invalid_not_unchanged(self):
        fixture = load_fixture("feed-indexes.json")
        fixture["responses"] = fixture["responses"][:-1]
        transport = FixtureTransport.merge(
            FixtureTransport(load_fixture("registry-responses.json")),
            FixtureTransport(fixture),
            FixtureTransport(load_fixture("github-responses.json")),
            FixtureTransport(load_fixture("daede-ready.json")),
        )
        with self.assertRaisesRegex(ValueError, "feed"):
            resolve_candidate(transport, load_fixture("previous-lock.json"))


class RegistryPaginationTests(unittest.TestCase):
    def _first_page_transport(self, next_url):
        url = "https://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list?n=100"
        return FixtureTransport({"responses": [{"url": url, "json": {"tags": ["mediatek-filogic-openwrt-25.12.1"]}, "headers": {"link": f"<{next_url}>; rel=\"next\""}}]})

    def test_rejects_cross_host_pagination_link(self):
        transport = self._first_page_transport("https://example.invalid/v2/immortalwrt/imagebuilder/tags/list?n=100&last=tag")
        with self.assertRaisesRegex(ValueError, "pagination"):
            sources._list_registry_tags(transport, "immortalwrt/imagebuilder")

    def test_rejects_cyclic_pagination_link(self):
        url = "https://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list?n=100"
        next_url = f"{url}&last=first"
        transport = FixtureTransport({"responses": [
            {"url": url, "json": {"tags": ["first"]}, "headers": {"link": f"<{next_url}>; rel=\"next\""}},
            {"url": next_url, "json": {"tags": ["second"]}, "headers": {"link": f"<{url}>; rel=\"next\""}},
        ]})
        with self.assertRaisesRegex(ValueError, "pagination"):
            sources._list_registry_tags(transport, "immortalwrt/imagebuilder")

    def test_rejects_invalid_pagination_link_shape(self):
        transport = self._first_page_transport("https://registry-1.docker.io/v2/immortalwrt/sdk/tags/list?n=100&last=tag")
        with self.assertRaisesRegex(ValueError, "pagination"):
            sources._list_registry_tags(transport, "immortalwrt/imagebuilder")

    def test_rejects_unsafe_pagination_queries(self):
        for query in (
            "n=99&last=tag",
            "n=100",
            "n=100&last=",
            "n=100&last=tag&extra=value",
        ):
            with self.subTest(query=query):
                transport = self._first_page_transport(
                    f"https://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list?{query}"
                )
                with self.assertRaisesRegex(ValueError, "pagination"):
                    sources._list_registry_tags(transport, "immortalwrt/imagebuilder")

    def test_rejects_non_next_link_relation(self):
        transport = self._first_page_transport(
            "https://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list?n=100&last=tag"
        )
        response = transport.responses[next(iter(transport.responses))][0]
        response["headers"]["link"] = response["headers"]["link"].replace('rel="next"', 'rel="last"')
        with self.assertRaisesRegex(ValueError, "pagination"):
            sources._list_registry_tags(transport, "immortalwrt/imagebuilder")

    def test_follows_relative_link_to_page_two(self):
        first_url = "https://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list?n=100"
        relative_url = "/v2/immortalwrt/imagebuilder/tags/list?n=100&last=mediatek-filogic-openwrt-25.12.1"
        absolute_url = "https://registry-1.docker.io" + relative_url
        transport = FixtureTransport({"responses": [
            {"url": first_url, "json": {"tags": ["mediatek-filogic-openwrt-25.12.1"]}, "headers": {"link": f"<{relative_url}>; rel=\"next\""}},
            {"url": absolute_url, "json": {"tags": ["mediatek-filogic-openwrt-25.12.2"]}, "headers": {}},
        ]})
        self.assertEqual(
            sources._list_registry_tags(transport, "immortalwrt/imagebuilder"),
            ["mediatek-filogic-openwrt-25.12.1", "mediatek-filogic-openwrt-25.12.2"],
        )

    def test_follows_bearer_challenge_before_reading_tags(self):
        tags_url = "https://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list?n=100"
        token_url = (
            "https://auth.docker.io/token?service=registry.docker.io"
            "&scope=repository%3Aimmortalwrt%2Fimagebuilder%3Apull"
        )

        class ChallengedTagsTransport:
            def get_json(self, url, headers=None):
                if url == token_url:
                    return {"token": "tag-list-token"}, {}
                if url != tags_url:
                    raise ValueError(f"unexpected URL {url}")
                if headers == {"Authorization": "Bearer tag-list-token"}:
                    return {"name": "immortalwrt/imagebuilder", "tags": ["mediatek-filogic-openwrt-25.12.1"]}, {}
                return {
                    "errors": [{"code": "UNAUTHORIZED"}],
                }, {
                    "www-authenticate": (
                        'Bearer realm="https://auth.docker.io/token",'
                        'service="registry.docker.io",'
                        'scope="repository:immortalwrt/imagebuilder:pull"'
                    )
                }

        self.assertEqual(
            sources._list_registry_tags(ChallengedTagsTransport(), "immortalwrt/imagebuilder"),
            ["mediatek-filogic-openwrt-25.12.1"],
        )


class RedirectPolicyTests(unittest.TestCase):
    def _redirect(self, destination):
        handler = sources._AllowlistedRedirectHandler()
        request = urllib.request.Request("https://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list?n=100", headers={"Authorization": "Bearer secret"})
        return handler.redirect_request(request, None, 302, "Found", {}, destination)

    def test_allows_same_host_redirect_and_keeps_authorization(self):
        redirected = self._redirect("https://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list?n=100&last=tag")
        self.assertEqual(redirected.full_url, "https://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list?n=100&last=tag")
        self.assertEqual(redirected.get_header("Authorization"), "Bearer secret")

    def test_strips_authorization_on_allowed_cross_host_redirect(self):
        redirected = self._redirect("https://auth.docker.io/token?service=registry.docker.io")
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_rejects_forbidden_redirect_destinations(self):
        for destination in (
            "http://registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list",
            "https://example.invalid/v2/immortalwrt/imagebuilder/tags/list",
            "https://user:pass@registry-1.docker.io/v2/immortalwrt/imagebuilder/tags/list",
            "https://registry-1.docker.io:444/v2/immortalwrt/imagebuilder/tags/list",
        ):
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, "allowlisted"):
                self._redirect(destination)


class ParserAndFixtureTests(unittest.TestCase):
    def test_rejects_duplicate_makefile_assignments(self):
        raw = ("PKG_VERSION:=1\nPKG_VERSION:=2\nPKG_RELEASE:=1\nPKG_SOURCE:=source.tar.gz\nPKG_HASH:=" + "a" * 64 + "\n").encode()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            sources._parse_makefile(raw, "package")

    def test_rejects_duplicate_pin_assignments(self):
        raw = (
            "DAE_VERSION=2026.08.28\nDAED_VERSION=2026.08.28\n"
            "DAED_COMMIT=" + "b" * 40 + "\nWING_COMMIT=" + "c" * 40 + "\n"
            "CORE_COMMIT=" + "d" * 40 + "\nCORE_UPSTREAM_COMMIT=" + "a" * 40 + "\n"
            "OUTBOUND_COMMIT=" + "e" * 40 + "\nQUICGO_BASE_COMMIT=" + "f" * 40 + "\n"
            "QUICGO_PERF_TIP=" + "1" * 40 + "\nQUICGO_PERF_TIP=" + "2" * 40 + "\n"
        ).encode()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            sources._parse_pins(raw)

    def test_hashes_non_utf8_nul_feed_bytes_from_base64_fixture_field(self):
        values = [b"base\x00\xff", b"luci\x80", b"packages\x00\xfe"]
        responses = [
            {"url": template.format(version="25.12.1"), "bytes_base64": base64.b64encode(value).decode("ascii"), "headers": {}}
            for template, value in zip(sources._FEED_URLS, values)
        ]
        feeds = sources._resolve_feeds(FixtureTransport({"responses": responses}), "25.12.1")
        self.assertEqual([record["sha256"] for record in feeds["records"]], [sources._sha256(value) for _, value in sorted(zip(sources._FEED_URLS, values))])


class GitHubResolverTests(unittest.TestCase):
    def _candidate(self, daede="daede-ready.json"):
        transport = FixtureTransport.from_files(
            FIXTURES / "registry-responses.json", FIXTURES / "feed-indexes.json", FIXTURES / "github-responses.json", FIXTURES / daede
        )
        return resolve_candidate(transport, None)

    def test_nikki_monitored_tree_changes_change_nikki_group(self):
        baseline = self._candidate()
        for path in ("nikki", "luci-app-nikki", "mihomo-meta"):
            fixture = load_fixture("github-responses.json")
            for response in fixture["responses"]:
                if "/git/trees/" in response["url"]:
                    for entry in response["json"]["tree"]:
                        if entry["path"] == path:
                            entry["sha"] = "f" * 40
            candidate = resolve_candidate(FixtureTransport.merge(
                FixtureTransport(load_fixture("registry-responses.json")), FixtureTransport(load_fixture("feed-indexes.json")),
                FixtureTransport(fixture), FixtureTransport(load_fixture("daede-ready.json"))), None)
            self.assertNotEqual(fingerprint(baseline["nikki"]), fingerprint(candidate["nikki"]), path)

    def test_daede_monitored_tree_and_pins_changes_change_daede_group(self):
        baseline = self._candidate()
        for path in ("dae", "daed", "luci-app-daede", "ci/pins.env"):
            fixture = load_fixture("daede-ready.json")
            for response in fixture["responses"]:
                if "/git/trees/" in response["url"]:
                    for entry in response["json"]["tree"]:
                        if entry["path"] == path:
                            entry["sha"] = "a" * 40
                if path == "ci/pins.env" and response["url"].endswith(path):
                    response["bytes"] = response["bytes"].replace("CORE_COMMIT=" + "7" * 40, "CORE_COMMIT=" + "b" * 40)
            candidate = resolve_candidate(FixtureTransport.merge(
                FixtureTransport(load_fixture("registry-responses.json")), FixtureTransport(load_fixture("feed-indexes.json")),
                FixtureTransport(load_fixture("github-responses.json")), FixtureTransport(fixture)), None)
            self.assertNotEqual(fingerprint(baseline["daede"]), fingerprint(candidate["daede"]), path)

    def test_official_daede_heads_ahead_of_pins_are_not_ready(self):
        candidate = self._candidate("daede-not-ready.json")
        self.assertEqual(candidate["resolution"], "not-ready")
        self.assertEqual(candidate["not_ready_repositories"], ["dae", "daed", "dae-wing"])

    def test_any_official_daede_head_mismatch_is_not_ready(self):
        fixture = load_fixture("daede-not-ready.json")
        for response in fixture["responses"]:
            if "/compare/" in response["url"]:
                response["json"]["status"] = "diverged"
        candidate = resolve_candidate(FixtureTransport.merge(
            FixtureTransport(load_fixture("registry-responses.json")), FixtureTransport(load_fixture("feed-indexes.json")),
            FixtureTransport(load_fixture("github-responses.json")), FixtureTransport(fixture)), None)
        self.assertEqual(candidate["resolution"], "not-ready")
        self.assertEqual(candidate["not_ready_repositories"], ["dae", "daed", "dae-wing"])

    def test_documentation_only_tree_change_does_not_change_candidate_fingerprint(self):
        first = self._candidate()
        fixture = load_fixture("github-responses.json")
        for response in fixture["responses"]:
            if response["url"].endswith("/git/trees/" + "2" * 40 + "?recursive=1"):
                response["json"]["tree"].append({"path": "README.md", "type": "blob", "sha": "f" * 40})
        second = resolve_candidate(FixtureTransport.merge(
            FixtureTransport(load_fixture("registry-responses.json")), FixtureTransport(load_fixture("feed-indexes.json")),
            FixtureTransport(fixture), FixtureTransport(load_fixture("daede-ready.json"))), None)
        self.assertEqual(fingerprint(first), fingerprint(second))

    def test_rejects_truncated_github_tree(self):
        fixture = load_fixture("github-responses.json")
        for response in fixture["responses"]:
            if "/git/trees/" in response["url"]:
                response["json"]["truncated"] = True
                break
        with self.assertRaisesRegex(ValueError, "truncated"):
            self._candidate_from_github_fixture(fixture)

    def test_rejects_missing_or_wrong_type_required_tree_entry(self):
        for mutation in ("missing", "blob"):
            with self.subTest(mutation=mutation):
                fixture = load_fixture("github-responses.json")
                for response in fixture["responses"]:
                    if "/git/trees/" in response["url"]:
                        for entry in response["json"]["tree"]:
                            if entry["path"] == "nikki":
                                if mutation == "missing":
                                    response["json"]["tree"].remove(entry)
                                else:
                                    entry["type"] = "blob"
                                break
                        break
                with self.assertRaisesRegex(ValueError, "tree"):
                    self._candidate_from_github_fixture(fixture)

    def test_rejects_missing_or_wrong_type_required_makefile_entry(self):
        for mutation in ("missing", "tree"):
            with self.subTest(mutation=mutation):
                fixture = load_fixture("github-responses.json")
                for response in fixture["responses"]:
                    if "/git/trees/" in response["url"]:
                        for entry in response["json"]["tree"]:
                            if entry["path"] == "nikki/Makefile":
                                if mutation == "missing":
                                    response["json"]["tree"].remove(entry)
                                else:
                                    entry["type"] = "tree"
                                break
                        break
                with self.assertRaisesRegex(ValueError, "tree"):
                    self._candidate_from_github_fixture(fixture)

    def _candidate_from_github_fixture(self, github_fixture):
        return resolve_candidate(FixtureTransport.merge(
            FixtureTransport(load_fixture("registry-responses.json")),
            FixtureTransport(load_fixture("feed-indexes.json")),
            FixtureTransport(github_fixture),
            FixtureTransport(load_fixture("daede-ready.json")),
        ), None)


if __name__ == "__main__":
    unittest.main()
