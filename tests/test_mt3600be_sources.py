import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from scripts.mt3600be_sources import (
    canonical_bytes,
    compare_states,
    fingerprint,
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


if __name__ == "__main__":
    unittest.main()
