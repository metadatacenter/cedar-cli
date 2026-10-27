"""`build this` and `publish this` act on the repository that contains a directory.

The wrapper used to append `--wd="$PWD"` after the operator's arguments, so an explicit `--wd`
lost to it, and the planners then matched the directory against each checkout root exactly. A
relative path, or a Maven module inside a checkout, matched nothing, and the empty plan that
followed ran and reported success.
"""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import build, publish
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.Repos import Repos
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.planner.BuildPlanner import BuildPlanner
from org.metadatacenter.planner.Planner import PlanError, Planner
from org.metadatacenter.planner.PublishPlanner import PublishPlanner
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context
from org.metadatacenter.util.Util import Util


class RepositoryResolutionTest(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        # Left unresolved: on macOS the temporary directory is reached through the /var symlink,
        # as a CEDAR_HOME or a shell's $PWD may be.
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.environment = {'CEDAR_HOME': str(self.home)}
        context = InvocationContext(environment=self.environment)
        repos = context.__dict__['repos'] = Repos()
        stack.enter_context(use_context(context))
        stack.enter_context(patch.object(Util, 'cedar_home', str(self.home)))

        self.server = Repo('cedar-bridge-server', RepoType.JAVA, [])
        # A registered name that the server's directory name extends, to prove that containment
        # follows path components rather than a string prefix.
        self.prefix = Repo('cedar-bridge', RepoType.JAVA, [])
        self.development = Repo('cedar-development', RepoType.DEVELOPMENT, [])
        self.bridging = Repo('cedar-bridging', RepoType.MULTI, [], is_frontend=True)
        self.bridging_src = Repo('cedar-bridging-src', RepoType.ANGULAR, [], is_frontend=True)
        self.bridging.add_sub_repo(self.bridging_src)
        for repo in (self.server, self.prefix, self.development, self.bridging):
            repos.add_repo(repo)
        for directory in ('cedar-bridge-server/cedar-bridge-server-application', 'cedar-bridge',
                          'cedar-development/ops', 'cedar-bridging/cedar-bridging-src/src'):
            (self.home / directory).mkdir(parents=True)

    def called_from(self, relative):
        self.environment['CEDAR_CLI_CALLER_DIR'] = str(self.home / relative)

    def test_an_absolute_directory_overrides_the_callers(self):
        self.called_from('cedar-development')
        self.assertIs(self.server, Planner.repo_containing(str(self.home / 'cedar-bridge-server')))

    def test_a_relative_directory_is_resolved_against_the_callers(self):
        self.called_from('cedar-development/ops')
        self.assertIs(self.server, Planner.repo_containing('../../cedar-bridge-server'))

    def test_the_callers_directory_is_the_default(self):
        self.called_from('cedar-development/ops')
        self.assertIs(self.development, Planner.repo_containing(None))

    def test_a_subdirectory_belongs_to_the_checkout_that_contains_it(self):
        self.called_from('cedar-bridge-server/cedar-bridge-server-application')
        self.assertIs(self.server, Planner.repo_containing(None))
        self.assertIs(self.prefix, Planner.repo_containing(str(self.home / 'cedar-bridge')))

    def test_a_nested_repository_wins_over_its_parent(self):
        self.called_from('cedar-bridging/cedar-bridging-src/src')
        self.assertIs(self.bridging_src, Planner.repo_containing(None))
        self.assertIs(self.bridging, Planner.repo_containing('../..'))

    def test_case_is_ignored(self):
        self.called_from('cedar-development')
        self.assertIs(self.server, Planner.repo_containing(str(self.home / 'CEDAR-Bridge-Server')))

    def test_a_directory_in_no_repository_is_refused_by_name(self):
        for wd in (str(self.home), str(self.home.parent)):
            with self.subTest(wd=wd):
                self.called_from('cedar-development')
                with self.assertRaises(PlanError) as raised:
                    Planner.repo_containing(wd)
                self.assertIn(str(Path(wd).resolve()), str(raised.exception))

    def test_build_and_publish_plan_the_resolved_repository(self):
        self.called_from('cedar-development')
        build_plan = Plan('Build this')
        BuildPlanner.this(build_plan, '../cedar-bridge-server/cedar-bridge-server-application')
        self.assertEqual([self.server], [task.repo for task in build_plan.tasks])
        self.assertTrue(build_plan.tasks[0].parameters['force_frontend_build'])
        publish_plan = Plan('Publish this')
        PublishPlanner.this(publish_plan, '../cedar-bridge-server')
        self.assertEqual([self.server], [task.repo for task in publish_plan.tasks])

    def test_no_repository_fails_the_command_instead_of_running_an_empty_plan(self):
        self.called_from('cedar-development')
        runner = CliRunner()
        for command, app in (('build', build.app), ('publish', publish.app)):
            with self.subTest(command=command), \
                    patch.object(build, 'execute_java_build') as built, \
                    patch.object(publish.plan_executor, 'execute') as published:
                result = runner.invoke(app, ['this', '--wd', str(self.home)])
                self.assertEqual(1, result.exit_code, result.output)
                self.assertIn('No registered repository contains', result.output)
                built.assert_not_called()
                published.assert_not_called()


if __name__ == '__main__':
    unittest.main()
