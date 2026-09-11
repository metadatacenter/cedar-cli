import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from typer.testing import CliRunner

import cedar
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.InvocationContext import (
    InvocationContext, current_context, invocation_environment, use_context,
)
from org.metadatacenter.util.ProcessRunner import run_process
from org.metadatacenter.util.Util import Util


class InvocationContextTest(unittest.TestCase):
    def test_import_does_not_bootstrap_or_initialize_catalogs(self):
        result = subprocess.run([sys.executable, '-c', '''
import os,sys
from unittest.mock import patch
from org.metadatacenter.util import InvocationContext as contexts
from org.metadatacenter.util.ModeManager import ModeManager
before = dict(os.environ)
sys.argv = ['cedar.py', 'native', 'start', 'all']
with patch.object(ModeManager, 'bootstrap', side_effect=AssertionError('import bootstrapped')):
    import cedar
assert contexts._legacy.get() is None
assert contexts._active.get() is None
assert dict(os.environ) == before
'''], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_repeated_commands_have_fresh_settings_catalogs_and_child_environment(self):
        app = cedar.create_app()
        seen = []
        @app.command('probe')
        def probe():
            context = current_context()
            seen.append((context, GlobalContext.should_skip_tests(), GlobalContext.fail_on_error()))
            GlobalContext.mark_skip_tests(True)
            GlobalContext.mark_do_not_fail()
            invocation_environment()['CEDAR_INVOCATION_TEST'] = 'private'
            result = run_process([sys.executable, '-c',
                                  'import os; print(os.environ["CEDAR_INVOCATION_TEST"])'])
            self.assertEqual(['private'], list(result))
            self.assertEqual(0, result.returncode)
        before = dict(os.environ)
        with patch.object(cedar.ModeManager, 'bootstrap'):
            for _ in range(2):
                result = CliRunner().invoke(app, ['probe'])
                self.assertEqual(0, result.exit_code, result.output)
        first, second = seen
        self.assertEqual((False, True), first[1:])
        self.assertEqual((False, True), second[1:])
        self.assertIsNot(first[0], second[0])
        self.assertIsNot(first[0].repos, second[0].repos)
        self.assertIsNot(first[0].task_operators, second[0].task_operators)
        self.assertEqual(before, dict(os.environ))

    def test_supplied_context_is_used_and_outer_scope_restored_after_failure(self):
        outer = InvocationContext(environment={'CEDAR_HOME': '/outer'})
        inner = InvocationContext(environment={'CEDAR_HOME': '/inner'})
        app = cedar.create_app()
        @app.command('fail')
        def fail():
            self.assertIs(inner, current_context())
            self.assertEqual('/inner', Util.cedar_home)
            raise RuntimeError('expected')
        with use_context(outer), patch.object(cedar.ModeManager, 'bootstrap'):
            result = CliRunner().invoke(app, ['fail'], obj=inner)
            self.assertNotEqual(0, result.exit_code)
            self.assertIs(outer, current_context())
            self.assertEqual('/outer', Util.cedar_home)

    def test_injected_github_runner_receives_the_scoped_environment(self):
        from types import SimpleNamespace
        from org.metadatacenter.github_ci import probe_exact_commit
        context = InvocationContext(environment={'PATH': '/scoped/bin'})
        def runner(args, **kwargs):
            self.assertIs(context.environment, kwargs['env'])
            return SimpleNamespace(returncode=0, stdout='{"workflow_runs": [{"name": "CI"}]}', stderr='')
        with use_context(context):
            probe = probe_exact_commit('sample', 'a' * 40, runner=runner, environment=invocation_environment())
        self.assertEqual(1, probe.attempts)

    def test_profile_bootstrap_changes_only_the_invocation_environment(self):
        from org.metadatacenter.model.CedarMode import CedarMode
        from org.metadatacenter.util.ModeManager import ModeManager
        before = dict(os.environ)
        with tempfile.TemporaryDirectory() as root:
            context = InvocationContext(environment={'CEDAR_HOME': root})
            with use_context(context), patch.object(ModeManager, 'current', return_value=CedarMode.NATIVE), \
                    patch.object(ModeManager, 'profile_environment', return_value={'CEDAR_HOST': 'private.test'}):
                ModeManager.bootstrap(['build', 'java'])
                self.assertEqual('private.test', invocation_environment()['CEDAR_HOST'])
                result = run_process([sys.executable, '-c', 'import os; print(os.environ["CEDAR_HOST"])'])
                self.assertEqual(['private.test'], list(result))
        self.assertEqual(before, dict(os.environ))
