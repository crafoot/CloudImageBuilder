import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from scripts.validate_mt3600be_manifest import parse_manifest, validate_required


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "mt3600be"
REQUIRED = ("nikki", "luci-app-nikki", "mihomo-meta", "dae", "daed", "luci-app-daede")


class ManifestTests(unittest.TestCase):
    def test_parses_complete_manifest_with_exact_versions(self):
        text = (FIXTURES / "manifest-complete.txt").read_text(encoding="utf-8")
        self.assertEqual(
            parse_manifest(text),
            {
                "nikki": "1.7.2-r1",
                "luci-app-nikki": "1.7.2-r1",
                "mihomo-meta": "1.19.12-r1",
                "dae": "1.0.0-r1",
                "daed": "1.0.0-r1",
                "luci-app-daede": "1.0.0-r1",
            },
        )

    def test_reports_all_missing_required_packages_together(self):
        packages = parse_manifest((FIXTURES / "manifest-missing-daed.txt").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, r"daed"):
            validate_required(packages)

    def test_reports_multiple_missing_packages_in_one_error(self):
        packages = {"nikki": "1.7.2-r1"}
        with self.assertRaisesRegex(ValueError, r"luci-app-nikki.*mihomo-meta.*dae.*daed.*luci-app-daede"):
            validate_required(packages)

    def test_reports_each_required_package_when_removed(self):
        complete = parse_manifest((FIXTURES / "manifest-complete.txt").read_text(encoding="utf-8"))
        for name in REQUIRED:
            with self.subTest(name=name):
                packages = {key: value for key, value in complete.items() if key != name}
                with self.assertRaisesRegex(ValueError, name):
                    validate_required(packages)

    def test_rejects_conflicting_duplicate_versions(self):
        with self.assertRaisesRegex(ValueError, r"duplicate.*nikki"):
            parse_manifest("nikki - 1.0\nnikki - 2.0\n")

    def test_rejects_empty_version(self):
        with self.assertRaisesRegex(ValueError, "version"):
            parse_manifest("nikki - \n")

    def test_rejects_blank_rows(self):
        with self.assertRaisesRegex(ValueError, r"line 2.*NAME - VERSION"):
            parse_manifest("nikki - 1.0\n\ndaed - 1.0\n")

    def test_rejects_surrounding_whitespace(self):
        for text in (" nikki - 1.0\n", "nikki - 1.0 \n"):
            with self.subTest(text=repr(text)), self.assertRaisesRegex(ValueError, "NAME - VERSION"):
                parse_manifest(text)

    def test_cli_valid_manifest_succeeds(self):
        result = subprocess.run(
            [sys.executable, "scripts/validate_mt3600be_manifest.py", str(FIXTURES / "manifest-complete.txt")],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(json.loads(result.stdout)["valid"])

    def test_cli_invalid_manifest_writes_invalid_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as output:
            result = subprocess.run(
                [sys.executable, "scripts/validate_mt3600be_manifest.py", str(FIXTURES / "manifest-missing-daed.txt"), "--json-output", output.name],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(json.loads(output.read().decode("utf-8"))["valid"])


if __name__ == "__main__":
    unittest.main()
