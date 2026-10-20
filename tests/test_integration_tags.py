import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import typer

from org.metadatacenter import build
from org.metadatacenter.util.IntegrationTags import scan
from org.metadatacenter.worker import IntegrationTagWorker as worker_module
from org.metadatacenter.worker.IntegrationTagWorker import IntegrationTagWorker

PACKAGE = 'src/test/java/org/example'


def write(root, name, body, directory=PACKAGE):
    path = Path(root) / directory / f'{name}.java'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'package org.example;\n\n{body}\n')
    return path


SERVER_SUITE = '''import io.dropwizard.testing.junit5.DropwizardAppExtension;
{tag}public class {name} {{
  static final DropwizardAppExtension<Object> APP = null;
  @Test void answers() {{}}
}}'''

UNIT_SUITE = '''public class {name} {{
  @Test void adds() {{}}
}}'''


class IntegrationTagAnalysisTest(unittest.TestCase):
    """Which classes the check names, read from test sources laid out as a repository holds them."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = self.directory.name

    def tearDown(self):
        self.directory.cleanup()

    def names(self):
        return [finding.name for finding in scan(self.root).findings]

    def test_untagged_server_suite_is_named_with_what_it_starts(self):
        write(self.root, 'RoutesTest', SERVER_SUITE.format(tag='', name='RoutesTest'))
        report = scan(self.root)
        self.assertEqual(['RoutesTest'], [finding.name for finding in report.findings])
        self.assertEqual('starts a Dropwizard application', report.findings[0].reason)

    def test_tagged_server_suite_passes(self):
        write(self.root, 'RoutesTest',
              SERVER_SUITE.format(tag='@Tag("integration")\n', name='RoutesTest'))
        self.assertEqual([], self.names())

    def test_unit_suite_is_not_an_integration_class(self):
        write(self.root, 'MathTest', UNIT_SUITE.format(name='MathTest'))
        report = scan(self.root)
        self.assertEqual((1, 0, []), (report.classes, report.integration, report.findings))

    def test_tag_on_a_base_class_covers_its_subclasses(self):
        write(self.root, 'AbstractServerTest', '''@Tag("integration")
public abstract class AbstractServerTest {
  static final DropwizardAppExtension<Object> APP = null;
}''')
        write(self.root, 'ReadTest', 'public class ReadTest extends AbstractServerTest {\n  @Test void reads() {}\n}')
        report = scan(self.root)
        self.assertEqual((2, []), (report.integration, report.findings))

    def test_untagged_base_is_named_once_for_itself_and_its_subclasses(self):
        write(self.root, 'AbstractServerTest', '''public abstract class AbstractServerTest {
  static final DropwizardAppExtension<Object> APP = null;
}''')
        write(self.root, 'ReadTest', 'public class ReadTest extends AbstractServerTest {\n  @Test void reads() {}\n}')
        write(self.root, 'WriteTest', 'public class WriteTest extends AbstractServerTest {\n  @Test void writes() {}\n}')
        findings = scan(self.root).findings
        self.assertEqual([('AbstractServerTest', 2)], [(f.name, f.subclasses) for f in findings])

    def test_suite_using_a_helper_that_starts_a_store_is_named(self):
        write(self.root, 'ScratchNeo4j', '''public final class ScratchNeo4j {
  static Object start() { return Neo4jBuilders.newInProcessBuilder().build(); }
}''')
        write(self.root, 'QueryTest', '''public class QueryTest {
  @Test void queries() { ScratchNeo4j.start(); }
}''')
        findings = scan(self.root).findings
        self.assertEqual(['QueryTest'], [finding.name for finding in findings])
        self.assertEqual('uses ScratchNeo4j, which starts an embedded Neo4j', findings[0].reason)

    def test_helper_that_is_not_a_suite_is_not_named(self):
        write(self.root, 'ScratchNeo4j', '''public final class ScratchNeo4j {
  static Object start() { return Neo4jBuilders.newInProcessBuilder().build(); }
}''')
        self.assertEqual([], self.names())

    def test_a_mention_in_a_comment_starts_nothing(self):
        write(self.root, 'ContractTest', '''/** The routes {@link RouteMatrixTest} boots with DropwizardAppExtension. */
public class ContractTest {
  // A RedisServer is not needed here.
  @Test void reads() {}
}''')
        write(self.root, 'RouteMatrixTest',
              SERVER_SUITE.format(tag='@Tag("integration")\n', name='RouteMatrixTest'))
        report = scan(self.root)
        self.assertEqual((1, []), (report.integration, report.findings))

    def test_mocked_search_client_is_a_unit_test(self):
        write(self.root, 'IndexingTest', '''import org.opensearch.client.RestHighLevelClient;
public class IndexingTest {
  RestHighLevelClient client = mock(RestHighLevelClient.class);
  @Test void indexes() {}
}''')
        self.assertEqual([], self.names())

    def test_constructed_search_client_needs_the_tag(self):
        write(self.root, 'SearchIT', '''import org.opensearch.client.RestHighLevelClient;
public class SearchIT {
  RestHighLevelClient client = new RestHighLevelClient(null);
  @Test void searches() {}
}''')
        self.assertEqual(['SearchIT'], self.names())

    def test_main_sources_and_build_output_are_not_read(self):
        write(self.root, 'Server', SERVER_SUITE.format(tag='', name='Server'),
              directory='src/main/java/org/example')
        write(self.root, 'Generated', SERVER_SUITE.format(tag='', name='Generated'),
              directory='target/generated-test-sources/src/test/java/org/example')
        self.assertEqual(0, scan(self.root).classes)

    def test_each_module_of_a_repository_is_read(self):
        write(self.root, 'RoutesTest', SERVER_SUITE.format(tag='', name='RoutesTest'),
              directory='server-application/src/test/java/org/example')
        write(self.root, 'MathTest', UNIT_SUITE.format(name='MathTest'),
              directory='server-core/src/test/java/org/example')
        report = scan(self.root)
        self.assertEqual((2, ['RoutesTest']), (report.classes, [f.name for f in report.findings]))
        self.assertEqual(Path('server-application/src/test/java/org/example/RoutesTest.java'),
                         report.findings[0].path)


class IntegrationTagWorkerTest(unittest.TestCase):
    """The verdict the command, the Java build and the train advisory act on."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.tagged = self.root / 'cedar-tagged-server'
        self.untagged = self.root / 'cedar-untagged-server'
        write(self.tagged, 'RoutesTest',
              SERVER_SUITE.format(tag='@Tag("integration")\n', name='RoutesTest'))
        write(self.untagged, 'RoutesTest', SERVER_SUITE.format(tag='', name='RoutesTest'))

    def run_against(self, *roots):
        reports = [(root.name, scan(root)) for root in roots]
        with patch.object(IntegrationTagWorker, 'reports', return_value=reports), \
                patch.object(worker_module, 'console'):
            return IntegrationTagWorker.check_test_tags(), IntegrationTagWorker.findings()

    def test_untagged_class_fails_and_is_named_with_its_repository(self):
        code, findings = self.run_against(self.tagged, self.untagged)
        self.assertEqual(1, code)
        self.assertEqual(['cedar-untagged-server/src/test/java/org/example/RoutesTest.java: '
                          'RoutesTest starts a Dropwizard application'], findings)

    def test_fully_tagged_estate_passes(self):
        self.assertEqual((0, []), self.run_against(self.tagged))

    def test_no_test_classes_at_all_fails_rather_than_passing_vacuously(self):
        with patch.object(IntegrationTagWorker, 'reports', return_value=[]), \
                patch.object(worker_module, 'console'):
            self.assertEqual(1, IntegrationTagWorker.check_test_tags())


class JavaBuildIntegrationTagTest(unittest.TestCase):
    """`cedarcli build java` refuses before any Maven work, and only when it would run the tests."""

    FINDING = ['cedar-x/src/test/java/RoutesTest.java: RoutesTest starts a Dropwizard application']

    def build(self, findings, **options):
        options = {'dry_run': False, 'dump_plan': False, 'tests': True, **options}
        with patch.object(IntegrationTagWorker, 'findings', return_value=findings) as asked, \
                patch.object(build, 'execute_build') as executed, \
                patch.object(build, 'configure_java_tests'), \
                patch.object(build, 'GlobalContext'), \
                patch.object(build, 'BuildPlanner'), \
                patch.object(build, 'console'):
            try:
                build.java(**options)
                code = 0
            except typer.Exit as exit_:
                code = exit_.exit_code
        return code, asked.called, executed.called

    def test_untagged_class_refuses_before_maven(self):
        self.assertEqual((1, True, False), self.build(self.FINDING))

    def test_tagged_estate_builds(self):
        self.assertEqual((0, True, True), self.build([]))

    def test_build_without_tests_does_not_ask(self):
        self.assertEqual((0, False, True), self.build(self.FINDING, tests=False))

    def test_dry_run_does_not_ask(self):
        self.assertEqual((0, False, True), self.build(self.FINDING, dry_run=True))


if __name__ == '__main__':
    unittest.main()
