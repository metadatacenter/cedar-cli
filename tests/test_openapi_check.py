import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util import OpenApiContract
from org.metadatacenter.util.OpenApiContract import (
    EMPTY_RESPONSES,
    RULE_EMPTY_RESPONSE,
    RULE_BARE_STRING,
    RULE_NO_SUCCESS,
    RULE_CROSS_SERVICE,
    RULE_REQUEST_BODY,
    RULE_RESPONSE_CONTENT,
    RULE_SCHEMA_REFERENCE,
    RULE_STUB_SCHEMA,
    analyze_document,
    cross_document_findings,
)
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.OpenApiWorker import OpenApiWorker

USER = {'type': 'object', 'properties': {'id': {'type': 'string'}}}
USER_RESPONSE = {'description': 'A user',
                 'content': {'application/json':
                             {'schema': {'$ref': '#/components/schemas/User'}}}}


def document(paths=None, schemas=None):
    """A document with as much of the envelope as the rules need and nothing else."""
    return {
        'openapi': '3.0.1',
        'info': {'title': 'test', 'version': '1.0'},
        'paths': paths or {},
        'components': {'schemas': schemas if schemas is not None else {'User': USER}},
    }


def rules(report):
    return [finding.rule for finding in report.failures]


class OpenApiRuleTest(unittest.TestCase):
    """The five rules, each failing on a document that breaks it and passing on one that does not."""

    def test_success_response_must_describe_its_content(self):
        described = analyze_document('cedar-user-server', '/doc', document(
            {'/users': {'get': {'responses': {'200': USER_RESPONSE}}}}))
        undescribed = analyze_document('cedar-user-server', '/doc', document(
            {'/users': {'get': {'responses': {'200': {'description': 'Successful operation'}}}}}))

        self.assertEqual([], rules(described))
        self.assertEqual('1/1', described.described)
        self.assertEqual([RULE_RESPONSE_CONTENT], rules(undescribed))
        self.assertEqual('0/1', undescribed.described)

    def test_a_204_describes_itself_and_an_error_response_is_not_checked(self):
        report = analyze_document('cedar-user-server', '/doc', document(
            {'/users/{id}': {'delete': {'responses': {'204': {'description': 'Deleted'},
                                                      '404': {'description': 'Not found'}}}}}))

        self.assertEqual([], rules(report))
        self.assertEqual(0, report.success_responses)

    def test_a_response_reference_is_followed_to_the_content_it_names(self):
        referenced = analyze_document('cedar-resource-server', '/doc', {
            'paths': {'/users': {'get': {'responses': {
                '200': {'$ref': '#/components/responses/User'}}}}},
            'components': {'responses': {'User': USER_RESPONSE}, 'schemas': {'User': USER}},
        })
        dangling = analyze_document('cedar-resource-server', '/doc', {
            'paths': {'/users': {'get': {'responses': {
                '200': {'$ref': '#/components/responses/Missing'}}}}},
            'components': {'responses': {'User': USER_RESPONSE}, 'schemas': {'User': USER}},
        })

        self.assertEqual([], rules(referenced))
        self.assertEqual([RULE_RESPONSE_CONTENT], rules(dangling))
        self.assertIn('does not resolve', dangling.failures[0].detail)

    def test_a_schema_reference_must_name_a_schema_the_document_defines(self):
        resolving = analyze_document('cedar-user-server', '/doc', document(
            {'/users': {'get': {'responses': {'200': USER_RESPONSE}}}}))
        dangling = analyze_document('cedar-user-server', '/doc', document(
            {'/users': {'get': {'responses': {'200': USER_RESPONSE}}}},
            schemas={'Other': USER}))

        self.assertEqual([], rules(resolving))
        self.assertEqual([RULE_SCHEMA_REFERENCE], rules(dangling))
        self.assertIn('#/components/schemas/User', dangling.failures[0].detail)

    def test_a_named_schema_must_carry_a_definition(self):
        defined = analyze_document('cedar-user-server', '/doc', document(schemas={'User': USER}))
        stub = analyze_document('cedar-user-server', '/doc', document(
            schemas={'Stub': {'type': 'object'}, 'Empty': {}}))
        composed = analyze_document('cedar-user-server', '/doc', document(schemas={
            'Alias': {'$ref': '#/components/schemas/User'},
            'Choice': {'oneOf': [{'$ref': '#/components/schemas/User'}]},
            'Free': {'type': 'object', 'additionalProperties': True},
            'Name': {'type': 'string'},
            'User': USER,
        }))

        self.assertEqual([], rules(defined))
        self.assertEqual([RULE_STUB_SCHEMA, RULE_STUB_SCHEMA], rules(stub))
        self.assertEqual([], rules(composed))

    def test_a_write_must_declare_the_body_it_reads(self):
        declared = analyze_document('cedar-user-server', '/doc', document(
            {'/users': {'post': {'requestBody': {'content': {'application/json': {}}},
                                 'responses': {'200': USER_RESPONSE}}}}))
        silent = analyze_document('cedar-user-server', '/doc', document(
            {'/users': {'post': {'responses': {'200': USER_RESPONSE}}}}))

        self.assertEqual([], rules(declared))
        self.assertEqual(0, declared.writes_without_body)
        self.assertEqual([RULE_REQUEST_BODY], rules(silent))
        self.assertEqual(1, silent.writes_without_body)

    def test_the_allowlist_covers_a_write_whose_handler_reads_no_body(self):
        report = analyze_document('cedar-messaging-server', '/doc', document(
            {'/command/mark-all-as-read': {'post': {'responses': {'200': USER_RESPONSE}}}}))

        self.assertEqual([], rules(report))
        self.assertEqual(0, report.writes_without_body)

    def test_an_allowlisted_write_that_gained_a_body_is_reported_without_failing(self):
        report = analyze_document('cedar-messaging-server', '/doc', document(
            {'/command/mark-all-as-read': {'post': {
                'requestBody': {'content': {'application/json': {}}},
                'responses': {'200': USER_RESPONSE}}}}))

        self.assertEqual([], rules(report))
        self.assertEqual([RULE_REQUEST_BODY], [finding.rule for finding in report.warnings])
        self.assertIn('stale', report.warnings[0].detail)

    def test_a_schema_name_must_mean_the_same_thing_in_every_service(self):
        agreeing = cross_document_findings({
            'cedar-user-server': document(schemas={'User': USER}),
            'cedar-group-server': document(schemas={'User': dict(reversed(list(USER.items())))}),
        })
        diverging = cross_document_findings({
            'cedar-user-server': document(schemas={'User': USER}),
            'cedar-group-server': document(schemas={'User': {'type': 'object'}}),
            'cedar-repo-server': document(schemas={'User': USER}),
        })

        self.assertEqual({}, agreeing)
        self.assertEqual({'cedar-group-server', 'cedar-repo-server', 'cedar-user-server'},
                         set(diverging))
        self.assertEqual([RULE_CROSS_SERVICE],
                         [finding.rule for finding in diverging['cedar-user-server']])
        self.assertIn('cedar-group-server', diverging['cedar-user-server'][0].detail)
        self.assertIn('cedar-user-server', diverging['cedar-group-server'][0].detail)


class OpenApiWorkerTest(unittest.TestCase):
    """The command's walk over the estate: which documents it reads and what it exits with."""

    @staticmethod
    def write_document(cedar_home, repository, content):
        path = Path(cedar_home, repository, repository + '-application',
                    'src', 'main', 'resources', 'assets', 'swagger-api', 'swagger.json')
        path.parent.mkdir(parents=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))

    def check(self, repositories, documents, show_all=False):
        """Run the check over a temporary CEDAR_HOME holding exactly these documents."""
        with tempfile.TemporaryDirectory() as cedar_home:
            for repository, content in documents.items():
                self.write_document(cedar_home, repository, content)
            with patch('org.metadatacenter.worker.OpenApiWorker.GlobalContext.repos') as repos, \
                    patch('org.metadatacenter.worker.OpenApiWorker.console') as console, \
                    patch.object(Util, 'cedar_home', cedar_home):
                repos.get_list_all.return_value = [
                    Repo(name, repo_type, [])
                    for name, repo_type in (entry if isinstance(entry, tuple)
                                            else (entry, RepoType.JAVA)
                                            for entry in repositories)]
                returncode = OpenApiWorker.check_openapi(show_all=show_all)
        return returncode, console

    @staticmethod
    def cells(console):
        table = console.print.call_args_list[0].args[0]
        return [str(cell) for column in table.columns for cell in column.cells]

    @staticmethod
    def detail(console):
        return '\n'.join(str(call.args[0]) for call in console.print.call_args_list[1:])

    def test_a_complete_document_passes_and_is_summarized(self):
        returncode, console = self.check(
            ['cedar-user-server'],
            {'cedar-user-server': document({'/users': {'get': {'responses': {'200': USER_RESPONSE}}}})})

        self.assertEqual(0, returncode)
        self.assertIn('cedar-user-server', self.cells(console))
        self.assertIn('1/1', self.cells(console))

    def test_a_document_with_a_finding_fails_and_names_the_operation(self):
        returncode, console = self.check(
            ['cedar-user-server'],
            {'cedar-user-server': document(
                {'/users': {'get': {'responses': {'200': {'description': 'Successful operation'}}}}})})

        self.assertEqual(1, returncode)
        self.assertIn('GET /users 200', self.detail(console))

    def test_a_skipped_repository_is_left_out_of_the_check(self):
        returncode, console = self.check(
            ['cedar-user-server', 'cedar-impex-server'],
            {'cedar-user-server': document({'/users': {'get': {'responses': {'200': USER_RESPONSE}}}}),
             'cedar-impex-server': document(
                 {'/impex': {'post': {'responses': {'200': {'description': 'Successful operation'}}}}})})

        self.assertEqual(0, returncode)
        self.assertNotIn('cedar-impex-server', self.cells(console))

    def test_a_repository_that_ships_no_document_is_not_looked_at(self):
        returncode, console = self.check(
            ['cedar-user-server', ('cedar-template-editor', RepoType.ANGULAR_JS)],
            {'cedar-user-server': document({'/users': {'get': {'responses': {'200': USER_RESPONSE}}}}),
             'cedar-template-editor': document(
                 {'/mock': {'get': {'responses': {'200': {'description': 'Successful operation'}}}}})})

        self.assertEqual(0, returncode)
        self.assertNotIn('cedar-template-editor', self.cells(console))

    def test_a_document_that_cannot_be_parsed_fails(self):
        returncode, console = self.check(['cedar-user-server'],
                                         {'cedar-user-server': '{ not json'})

        self.assertEqual(1, returncode)
        self.assertIn('could not be read', self.detail(console))

    def test_a_divergent_schema_fails_and_names_the_other_service(self):
        returncode, console = self.check(
            ['cedar-user-server', 'cedar-group-server'],
            {'cedar-user-server': document(schemas={'User': USER}),
             'cedar-group-server': document(schemas={'User': {'type': 'object',
                                                              'properties': {'name': {'type': 'string'}}}})})

        self.assertEqual(1, returncode)
        self.assertIn('cedar-group-server', self.detail(console))
        self.assertIn('defined differently', self.detail(console))

    def test_all_lists_a_document_that_has_nothing_to_report(self):
        complete = {'cedar-user-server': document(
            {'/users': {'get': {'responses': {'200': USER_RESPONSE}}}})}

        quiet_code, quiet = self.check(['cedar-user-server'], complete)
        verbose_code, verbose = self.check(['cedar-user-server'], complete, show_all=True)

        self.assertEqual(0, quiet_code)
        self.assertEqual(0, verbose_code)
        self.assertNotIn('no findings', self.detail(quiet))
        self.assertIn('no findings', self.detail(verbose))

    def test_finding_no_document_at_all_fails(self):
        returncode, console = self.check(['cedar-user-server'], {})

        self.assertEqual(1, returncode)


class BareStringPayloadTest(unittest.TestCase):
    """A JAX-RS resource method that takes its body as a String documents neither side of the wire."""

    def test_a_json_response_typed_as_a_string_fails(self):
        document = {
            'paths': {'/templates/{id}': {'get': {'responses': {'200': {
                'content': {'application/json': {'schema': {'type': 'string'}}}}}}}},
        }
        report = analyze_document('cedar-artifact-server', 'swagger.json', document)
        self.assertEqual([RULE_BARE_STRING], rules(report))
        self.assertIn('application/json', report.failures[0].location)

    def test_a_yaml_request_body_typed_as_a_string_fails(self):
        document = {
            'paths': {'/templates': {'post': {
                'requestBody': {'content': {'application/x-yaml': {'schema': {'type': 'string'}}}},
                'responses': {'201': {'content': {
                    'application/x-yaml': {'schema': {'$ref': '#/components/schemas/Artifact'}}}}}}}},
            'components': {'schemas': {'Artifact': {'type': 'object', 'additionalProperties': True}}},
        }
        report = analyze_document('cedar-artifact-server', 'swagger.json', document)
        self.assertEqual([RULE_BARE_STRING], rules(report))
        self.assertIn('request body', report.failures[0].location)

    def test_a_described_schema_and_an_unstructured_media_type_pass(self):
        document = {
            'paths': {'/templates/{id}/download': {'get': {'responses': {'200': {'content': {
                'application/json': {'schema': {'$ref': '#/components/schemas/Artifact'}},
                'text/plain': {'schema': {'type': 'string'}}}}}}}},
            'components': {'schemas': {'Artifact': {'type': 'object', 'additionalProperties': True}}},
        }
        report = analyze_document('cedar-artifact-server', 'swagger.json', document)
        self.assertEqual([], report.failures)


class UnannotatedOperationTest(unittest.TestCase):
    """A resource method nobody annotated reaches the document with a `default` response alone."""

    def test_an_operation_with_no_success_response_fails(self):
        default_response = {
            'description': 'default response',
            'content': {'application/json': {'schema': {'$ref': '#/components/schemas/User'}}},
        }
        doc = document(paths={'/logs/usage/summary': {'get': {'responses': {
            'default': default_response}}}})
        report = analyze_document('cedar-monitor-server', 'swagger.json', doc)

        self.assertEqual([RULE_NO_SUCCESS], rules(report))
        self.assertIn('GET /logs/usage/summary', report.failures[0].location)

    def test_it_is_not_counted_as_a_described_response(self):
        doc = document(paths={'/logs/usage/summary': {'get': {'responses': {'default': {}}}}})
        report = analyze_document('cedar-monitor-server', 'swagger.json', doc)

        self.assertEqual(0, report.success_responses)
        self.assertEqual(0, report.described_responses)

    def test_a_delete_declaring_only_204_passes(self):
        doc = document(paths={'/groups/{id}': {'delete': {'responses': {'204': {
            'description': 'Deleted'}}}}})
        report = analyze_document('cedar-group-server', 'swagger.json', doc)

        self.assertEqual([], report.failures)


class EmptyResponseTest(unittest.TestCase):
    """A response that carries no body says so by declaring no content, not an empty media type."""

    def test_a_listed_empty_response_passes_and_counts_as_described(self):
        doc = document(paths={'/templates/{template_id}': {'delete': {'responses': {
            '202': {'description': 'Content deleted; downstream cleanup is pending'}}}}})
        report = analyze_document('cedar-resource-server', 'swagger.json', doc)

        self.assertEqual([], report.failures)
        self.assertEqual(1, report.described_responses)

    def test_an_unlisted_empty_response_still_fails(self):
        doc = document(paths={'/templates/{template_id}': {'get': {'responses': {
            '200': {'description': 'The template'}}}}})
        report = analyze_document('cedar-resource-server', 'swagger.json', doc)

        self.assertEqual([RULE_RESPONSE_CONTENT], rules(report))

    def test_a_listed_response_that_gains_content_warns_rather_than_failing(self):
        doc = document(paths={'/templates/{template_id}': {'delete': {'responses': {
            '202': USER_RESPONSE}}}})
        report = analyze_document('cedar-resource-server', 'swagger.json', doc)

        self.assertEqual([], report.failures)
        self.assertEqual([RULE_EMPTY_RESPONSE], [f.rule for f in report.warnings])

    def test_every_listed_response_names_a_repository_the_check_reads(self):
        for repository, _method, _path, _status in EMPTY_RESPONSES:
            self.assertNotIn(repository, OpenApiContract.SKIPPED_REPOSITORIES)


if __name__ == '__main__':
    unittest.main()
