import unittest
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import native
from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.util.ModeManager import ModeManager
from org.metadatacenter.worker.NativeWorker import NativeWorker


class RestartTargetsTest(unittest.TestCase):
    def invoke(self, args, expected, mode=CedarMode.NATIVE, code=0):
        with patch.object(ModeManager, 'require_surface', return_value=mode), \
                patch.object(NativeWorker, 'restart', return_value=SimpleNamespace(returncode=code)) as worker:
            result = CliRunner().invoke(native.app, ['restart', *args])
            if expected is None:
                self.assertNotEqual(0, result.exit_code, result.output)
                worker.assert_not_called()
            else:
                self.assertEqual(code, result.exit_code, result.output)
                self.assertEqual(tuple(expected), tuple(worker.call_args.args[0]))
                worker.assert_called_once()

    def test_each_service_has_the_same_grouped_name_as_start(self):
        for name in NativeWorker.MICROSERVICES:
            self.invoke(['microservice', name], [name])
        for name in NativeWorker.FRONTENDS:
            self.invoke(['frontend', name.removeprefix('ui-')], [name])
        self.invoke(['microservice', 'open'], ['openview'])

    def test_group_targets_and_no_argument_compatibility(self):
        for args in ([], ['all']):
            self.invoke(args, [])
        for args in (['microservices'], ['microservice', 'all']):
            self.invoke(args, NativeWorker.MICROSERVICES)
        for args in (['frontends'], ['frontend', 'all']):
            self.invoke(args, NativeWorker.FRONTENDS)
        self.invoke(['frontend', 'split-frontends'], ['ui-workspace', 'ui-designer'])
        self.invoke(['repo', 'ui-openview'], ['repo', 'ui-openview'])

    def test_hybrid_allows_only_frontends(self):
        for args in ([], ['all'], ['microservices'], ['microservice', 'repo'], ['repo'],
                     ['ui-openview', 'repo']):
            self.invoke(args, None, CedarMode.HYBRID)
        self.invoke(['frontend', 'openview'], ['ui-openview'], CedarMode.HYBRID)
        self.invoke(['frontends'], NativeWorker.FRONTENDS, CedarMode.HYBRID)
        self.invoke(['ui-workspace'], ['ui-workspace'], CedarMode.HYBRID)

    def test_bad_targets_and_trailing_arguments_never_restart_anything(self):
        for args in (['invalid'], ['repo', 'invalid'], ['frontend', 'repo'],
                     ['microservice', 'ui-openview'], ['microservice', 'repo', 'invalid'],
                     ['infra'], ['backends'], ['frontend'], ['microservice']):
            self.invoke(args, None)

    def test_failure_status_propagates(self):
        self.invoke(['microservice', 'repo'], ['repo'], code=7)

    def test_help_exposes_groups_without_executing_a_restart(self):
        with patch.object(ModeManager, 'require_surface', return_value=CedarMode.NATIVE), \
                patch.object(NativeWorker, 'restart') as worker:
            for args in (['--help'], ['microservice', '--help'], ['frontend', '--help']):
                result = CliRunner().invoke(native.app, ['restart', *args])
                self.assertEqual(0, result.exit_code, result.output)
                self.assertNotIn('legacy', result.output)
            worker.assert_not_called()
