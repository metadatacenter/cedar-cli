"""A surface's package phase: offered to consumers once it passes, before the checks that follow."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from org.metadatacenter import frontend_inventory
from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
from org.metadatacenter.util.GlobalContext import GlobalContext

MODULE = "org.metadatacenter.taskexecutor.ShellTaskExecutor"


class ExecutorPackagePhaseTest(unittest.TestCase):
    def run_phases(self, codes, ready_after=2, fail_on_error=True):
        """Run three commands, the first `ready_after` of them the package phase."""
        executor = ShellTaskExecutor()
        task = SimpleNamespace(node_id=1, repo=SimpleNamespace(name="cedar-embeddable-editor"))
        codes = iter(codes)
        ran = []

        def execute(_task, _repo, commands, _cwd, _progress, _environment):
            ran.append(list(commands))
            return next(codes)

        with patch.object(executor, "_execute_commands", side_effect=execute), \
                patch(MODULE + ".reactor.publish", return_value="cedar-embeddable-editor") as publish, \
                patch(MODULE + ".package_ready") as ready, \
                patch.object(GlobalContext, "fail_on_error", return_value=fail_on_error):
            code = executor._execute_packaged_commands(
                task, task.repo, ["npm ci", "npm run build", "npm run test:ci"], Path("/build/cee"),
                Mock(), {}, ready_after, "/home")
        return code, ran, publish, ready

    def test_the_package_is_offered_between_the_phases(self):
        code, ran, publish, ready = self.run_phases([0, 0])
        self.assertEqual(0, code)
        self.assertEqual([["npm ci", "npm run build"], ["npm run test:ci"]], ran)
        publish.assert_called_once()
        self.assertTrue(publish.call_args.kwargs["provisional"])
        ready.assert_called_once()

    def test_failed_checks_fail_the_task_after_the_package_was_offered(self):
        code, _ran, _publish, ready = self.run_phases([0, 5])
        self.assertEqual(5, code)
        ready.assert_called_once()

    def test_a_failed_package_phase_offers_nothing_and_stops(self):
        code, ran, publish, ready = self.run_phases([3])
        self.assertEqual(3, code)
        self.assertEqual([["npm ci", "npm run build"]], ran)
        publish.assert_not_called()
        ready.assert_not_called()

    def test_without_fail_fast_the_checks_still_run_but_nothing_is_offered(self):
        code, ran, publish, ready = self.run_phases([3, 0], fail_on_error=False)
        self.assertEqual(3, code)
        self.assertEqual(2, len(ran))
        publish.assert_not_called()
        ready.assert_not_called()

    def test_a_surface_without_a_package_phase_runs_one_list(self):
        code, ran, publish, ready = self.run_phases([0], ready_after=None)
        self.assertEqual(0, code)
        self.assertEqual([["npm ci", "npm run build", "npm run test:ci"]], ran)
        publish.assert_not_called()
        ready.assert_not_called()


class ReactorChecksTest(unittest.TestCase):
    def checks(self, *rows):
        module = SimpleNamespace(surfaces=lambda _config: list(rows))
        with patch.object(frontend_inventory, "inventory", return_value=(module, {})):
            return frontend_inventory.reactor_checks()

    def test_the_package_phase_runs_between_setup_and_verification(self):
        check = self.checks({"reactorName": "cee", "setup": [["npm", "ci"]],
                             "package": [["npm", "run", "build"]],
                             "verify": [["npm", "run", "test:ci:prebuilt"]], "build": []})["cee"]
        self.assertEqual(["npm ci", "npm run build", "npm run test:ci:prebuilt"], check.commands)
        self.assertEqual(2, check.package_ready_after)

    def test_a_surface_without_one_offers_its_package_only_when_done(self):
        check = self.checks({"reactorName": "app", "setup": [["npm", "ci"]],
                             "verify": [["npm", "test"]], "build": ["npm", "run", "build"]})["app"]
        self.assertEqual(["npm ci", "npm test", "npm run build"], check.commands)
        self.assertIsNone(check.package_ready_after)


if __name__ == "__main__":
    unittest.main()
