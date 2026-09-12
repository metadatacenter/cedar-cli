"""The CI environment drift check, as a command and as a train advisory."""
import unittest
from unittest.mock import patch

from org.metadatacenter import ci_env
from org.metadatacenter.train_support import preflight


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


DRIFTED = (
    "ci-env-block.yml: 136 entries, and the code asks for nothing it lacks\n"
    "  OK      cedar-artifact-server\n"
    "  DRIFTED cedar-project: 135 entries, 1 missing, 0 extra\n"
    "  DRIFTED cedar-user-server: 135 entries, 1 missing, 0 extra\n"
    "\n2 copy(ies) have drifted from ci-env-block.yml. Re-run with --apply to rewrite them.\n"
)


class CiEnvCommandTest(unittest.TestCase):
    def test_drift_is_reported_and_fails(self):
        runner = lambda *_args, **_kwargs: FakeResult(1, DRIFTED)
        with patch.object(ci_env, "_script", return_value=_present()):
            code = ci_env.check_ci_env(cedar_home="/cedar", runner=runner)
        self.assertEqual(1, code)

    def test_agreement_passes(self):
        runner = lambda *_args, **_kwargs: FakeResult(0, "  OK      cedar-artifact-server\n")
        with patch.object(ci_env, "_script", return_value=_present()):
            self.assertEqual(0, ci_env.check_ci_env(cedar_home="/cedar", runner=runner))

    def test_a_missing_script_is_reported_rather_than_read_as_agreement(self):
        with patch.object(ci_env, "_script", return_value=_absent()):
            self.assertEqual(1, ci_env.check_ci_env(cedar_home="/cedar"))


class CiEnvPreflightTest(unittest.TestCase):
    """Drift is not evidence this train would fail, so it advises and never refuses."""

    def test_drift_advises_and_names_the_repositories(self):
        printed = []
        with patch.object(ci_env, "ci_env_report", return_value=(1, DRIFTED)), \
                patch.object(preflight._output_component.console, "print", printed.append):
            preflight._ci_env_preflight()
        joined = " ".join(printed)
        self.assertIn("CI environment advisory", joined)
        self.assertIn("cedar-project", joined)
        self.assertIn("cedar-user-server", joined)
        self.assertIn("cedarcli check ci-env --apply", joined)

    def test_a_check_that_cannot_run_is_said_plainly(self):
        printed = []
        with patch.object(ci_env, "ci_env_report", side_effect=ValueError("no jars")), \
                patch.object(preflight._output_component.console, "print", printed.append):
            preflight._ci_env_preflight()
        self.assertIn("not checked", " ".join(printed))

    def test_agreement_says_nothing(self):
        printed = []
        with patch.object(ci_env, "ci_env_report", return_value=(0, "")), \
                patch.object(preflight._output_component.console, "print", printed.append):
            preflight._ci_env_preflight()
        self.assertEqual([], printed)


def _present():
    class Present:
        def is_file(self):
            return True

        def __str__(self):
            return "/cedar/cedar-development/ops/check_ci_env.py"
    return Present()


def _absent():
    class Absent:
        def is_file(self):
            return False

        def __str__(self):
            return "/cedar/cedar-development/ops/check_ci_env.py"
    return Absent()


if __name__ == "__main__":
    unittest.main()
