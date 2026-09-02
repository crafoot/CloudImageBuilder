import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from scripts.mt3600be_sources import (
    FixtureTransport,
    Transport,
    canonical_bytes,
    compare_states,
    fingerprint,
    resolve_candidate,
    select_stable_25_12,
    validate_digest,
    validate_sha,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "mt3600be"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


class Mt3600beSourcesTests(unittest.TestCase):
    def test_selects_highest_stable_25_12(self):
        tags = ["25.12.1", "25.12.3", "25.12.2", "25.12-SNAPSHOT", "25.12.4-rc1", "26.01.0"]
        self.assertEqual(select_stable_25_12(tags), "25.12.3")

    def test_rejects_abbreviated_sha(self):
        with self.assertRaisesRegex(ValueError, "40-character"):
            validate_sha("3799926")

    def test_rejects_abbreviated_tree_or_pin_commit_in_canonical_state(self):
        for state in ({"tree_sha": "short"}, {"DAE_COMMIT": "short"}):
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
                "imagebuilder": {"repository": "immortalwrt/imagebuilder", "tag": "25.12.1", "digest": "sha256:" + "a" * 64},
                "sdk": {"repository": "immortalwrt/sdk", "tag": "25.12.1", "digest": "sha256:" + "b" * 64},
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
        self.assertEqual(values["IMAGEBUILDER_REFERENCE"], "immortalwrt/imagebuilder:25.12.1@sha256:" + "a" * 64)
        self.assertEqual(values["SDK_ARCH_REFERENCE"], "immortalwrt/sdk:25.12.1@sha256:" + "b" * 64)
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


class ResolverTests(unittest.TestCase):
    def _transport(self, *names):
        return FixtureTransport.from_files(*(FIXTURES / name for name in names))

    def test_transport_is_an_interface(self):
        self.assertTrue(hasattr(Transport, "get_json"))
        self.assertTrue(hasattr(Transport, "get_bytes"))

    def test_follows_bearer_challenge_and_records_exact_manifest_digest(self):
        candidate = resolve_candidate(self._transport("registry-responses.json", "feed-indexes.json", "github-responses.json", "daede-ready.json"), None)
        imagebuilder = candidate["immortalwrt"]["imagebuilder"]
        self.assertEqual(imagebuilder["tag"], "25.12.1")
        self.assertEqual(imagebuilder["digest"], "sha256:" + "a" * 64)

    def test_requires_both_imagebuilder_and_sdk_tags(self):
        fixture = load_fixture("registry-responses.json")
        fixture["responses"] = [entry for entry in fixture["responses"] if "sdk/tags/list" not in entry["url"]]
        transport = FixtureTransport(fixture)
        with self.assertRaisesRegex(ValueError, "SDK"):
            resolve_candidate(transport, None)

    def test_changed_feed_payload_changes_combined_digest_without_version_change(self):
        ready = self._transport("registry-responses.json", "feed-indexes.json", "github-responses.json", "daede-ready.json")
        changed = self._transport("registry-responses.json", "feed-indexes.json", "github-responses.json", "daede-ready.json")
        for response in changed.responses.values():
            for item in response:
                if item["url"].endswith("/luci/Packages.adb"):
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


class GitHubResolverTests(unittest.TestCase):
    def _candidate(self, daede="daede-ready.json"):
        transport = FixtureTransport.from_files(
            FIXTURES / "registry-responses.json", FIXTURES / "feed-indexes.json", FIXTURES / "github-responses.json", FIXTURES / daede
        )
        return resolve_candidate(transport, None)

    def test_nikki_monitored_tree_changes_change_nikki_group(self):
        baseline = self._candidate()
        for path in ("nikki/Makefile", "luci-app-nikki/Makefile", "mihomo-meta/Makefile"):
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
        for path in ("dae/Makefile", "daed/Makefile", "luci-app-daede/Makefile", "ci/pins.env"):
            fixture = load_fixture("daede-ready.json")
            for response in fixture["responses"]:
                if "/git/trees/" in response["url"]:
                    for entry in response["json"]["tree"]:
                        if entry["path"] == path:
                            entry["sha"] = "a" * 40
                if path == "ci/pins.env" and response["url"].endswith(path):
                    response["bytes"] = response["bytes"].replace("DAE_SOURCE_HASH=" + "a" * 64, "DAE_SOURCE_HASH=" + "b" * 64)
            candidate = resolve_candidate(FixtureTransport.merge(
                FixtureTransport(load_fixture("registry-responses.json")), FixtureTransport(load_fixture("feed-indexes.json")),
                FixtureTransport(load_fixture("github-responses.json")), FixtureTransport(fixture)), None)
            self.assertNotEqual(fingerprint(baseline["daede"]), fingerprint(candidate["daede"]), path)

    def test_official_daede_heads_ahead_of_pins_are_not_ready(self):
        candidate = self._candidate("daede-not-ready.json")
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


if __name__ == "__main__":
    unittest.main()
