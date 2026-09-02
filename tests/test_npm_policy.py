import tempfile
from pathlib import Path
import unittest

from org.metadatacenter.npm_policy import (
    npm_user_config_findings,
    unreviewed_install_scripts,
)


class NpmPolicyTest(unittest.TestCase):
    def test_exact_true_and_false_decisions_are_reviewed(self):
        package = {"allowScripts": {
            "native@1.2.3": True,
            "telemetry@2.0.0": False,
        }}
        lock = {"packages": {
            "node_modules/native": {"version": "1.2.3", "hasInstallScript": True},
            "node_modules/telemetry": {"version": "2.0.0", "hasInstallScript": True},
        }}
        self.assertEqual([], unreviewed_install_scripts(package, lock, "repo:."))

    def test_unreviewed_script_names_exact_locked_version(self):
        lock = {"packages": {
            "node_modules/native": {"version": "1.2.4", "hasInstallScript": True},
        }}
        self.assertEqual(
            ["native@1.2.4"],
            unreviewed_install_scripts(
                {"allowScripts": {"native@1.2.3": True}}, lock, "repo:."),
        )

    def test_missing_policy_is_not_silently_treated_as_empty(self):
        with self.assertRaisesRegex(ValueError, "has no allowScripts policy"):
            unreviewed_install_scripts({}, {"packages": {}}, "repo:.")

    def test_npmrc_reports_names_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".npmrc"
            path.write_text(
                "always-auth=true\nemail=person@example.org\n"
                "//registry.example/:_authToken=secret-value\n",
                encoding="utf-8",
            )
            findings = npm_user_config_findings(path)

        self.assertEqual(["fail", "warn"], [item.severity for item in findings])
        rendered = repr(findings)
        self.assertNotIn("secret-value", rendered)
        self.assertNotIn("person@example.org", rendered)


if __name__ == "__main__":
    unittest.main()
