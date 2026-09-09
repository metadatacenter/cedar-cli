"""Whether a committed OpenAPI document says enough for a client to be generated from it.

Every CEDAR microservice ships one OpenAPI document under its application module, and the estate's
API consumers are generated from those documents rather than written by hand. A document that names
a 2xx response and stops there tells a generator nothing: the operation appears in the client with no
return type, and the caller falls back to parsing the payload itself. An audit in September 2026
found that shape in most operations across the eleven documents the estate commits.

Generation depends on seven conditions: an operation declares a success response at all, that response
describes its content, that description is more than a bare string, a schema reference resolves, a named schema carries a definition, a write operation
declares the body it reads, and a schema name means the same thing in every service that uses it.
Each one failed somewhere in that audit, so each stays checked rather than trusted.

This module holds the rules and nothing else. Reading repositories and rendering the report belong to
OpenApiWorker, which keeps these decisions testable against a document in memory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Tuple

# Where a microservice repository keeps its document, relative to the repository directory. The
# module in between is the application module, whose name varies by service.
DOCUMENT_GLOB = '*/src/main/resources/assets/swagger-api/swagger.json'

HTTP_METHODS = ('get', 'put', 'post', 'delete', 'patch', 'head', 'options', 'trace')

# The methods that carry a payload. A generated client needs the payload's type to offer them.
WRITE_METHODS = ('post', 'put', 'patch')

SCHEMA_REF_PREFIX = '#/components/schemas/'

# Media types that carry a structured document. A JAX-RS resource method that takes the body as an
# unannotated String parameter generates `{"type": "string"}` for these, which passes for a described
# payload and describes nothing.
STRUCTURED_MEDIA_SUFFIXES = ('json', 'yaml')

# A 204 says there is no payload, so it is the one 2xx that describes itself by staying empty.
NO_CONTENT_STATUS = '204'

# Services excluded from the OpenAPI audit, and left out of this check for the same reason. These
# three are legacy. They still answer requests, and their documents receive no further work, so
# failing on them would report a gap nobody intends to close.
SKIPPED_REPOSITORIES = {
    'cedar-impex-server': 'legacy service excluded from the audit',
    'cedar-submission-server': 'legacy service excluded from the audit',
    'cedar-valuerecommender-server': 'legacy service excluded from the audit',
}

# Write operations whose handler reads no request body, so a document that declares none for them is
# right. Each entry names the repository, the method and the path, and says what the handler does
# instead. Every entry was read against its resource class before it was added; an entry whose route
# later gains a requestBody is reported as stale rather than as a failure.
BODILESS_WRITES = {
    ('cedar-messaging-server', 'POST', '/command/mark-all-as-read'):
        'marks the caller\'s own unread messages read and returns the count',
    ('cedar-user-server', 'POST', '/users/{id}/api-keys/{keyId}/regenerate'):
        'rotates the secret of the key named in the path and returns the user',
    ('cedar-worker-server', 'POST', '/command/regenerate-inclusion-subgraph'):
        'starts the one regeneration job the service runs and returns that job',
    ('cedar-resource-server', 'POST', '/command/generate-empty-rules-index'):
        'starts an index job whose only input is the index it claims',
    ('cedar-resource-server', 'POST', '/command/generate-empty-search-index'):
        'starts an index job whose only input is the index it claims',
    ('cedar-resource-server', 'POST', '/command/reset-rules-index-job'):
        'releases the rules index claim and returns the resulting status',
    ('cedar-resource-server', 'POST', '/command/reset-search-index-job'):
        'releases the search index claim and returns the resulting status',
    ('cedar-resource-server', 'POST', '/command/reset-valuesets-import'):
        'releases the value sets import claim and returns the resulting status',
    ('cedar-resource-server', 'POST', '/command/load-valuesets-ontology'):
        'starts the value sets import, which reads its ontology from configuration',
    ('cedar-resource-server', 'POST', '/template-elements/{template_element_id}/download'):
        'reads the artifact named in the path; the POST exists for callers that predate the GET',
    ('cedar-resource-server', 'POST', '/template-fields/{template_field_id}/download'):
        'reads the artifact named in the path; the POST exists for callers that predate the GET',
    ('cedar-resource-server', 'POST', '/template-instances/{template_instance_id}/download'):
        'reads the artifact named in the path; the POST exists for callers that predate the GET',
    ('cedar-resource-server', 'POST', '/templates/{template_id}/download'):
        'reads the artifact named in the path; the POST exists for callers that predate the GET',
}

# Success responses that carry no body. OpenAPI states an empty response by declaring no content at
# all, so these are right to have none; documenting an empty media type instead would promise a
# client a JSON document of unknown shape. Each entry names the repository, method, path and status,
# and says why the response is empty. An entry that stops matching a declared response is reported
# as stale rather than ignored.
EMPTY_RESPONSES = {
    ('cedar-resource-server', 'DELETE', '/templates/{template_id}', '202'):
        'content is gone and downstream cleanup is still running, which needs no body',
    ('cedar-resource-server', 'DELETE', '/template-elements/{template_element_id}', '202'):
        'content is gone and downstream cleanup is still running, which needs no body',
    ('cedar-resource-server', 'DELETE', '/template-fields/{template_field_id}', '202'):
        'content is gone and downstream cleanup is still running, which needs no body',
    ('cedar-resource-server', 'DELETE', '/template-instances/{template_instance_id}', '202'):
        'content is gone and downstream cleanup is still running, which needs no body',
    ('cedar-resource-server', 'POST', '/command/auth-user-callback', '201'):
        'provisions the user\'s objects for the Keycloak listener, which reads no body back',
    ('cedar-resource-server', 'POST', '/command/copy-artifact-to-folder', '200'):
        'reached only when the artifact server returns no entity, leaving nothing to return',
    ('cedar-resource-server', 'POST', '/command/create-draft-artifact', '200'):
        'reached only when the artifact server returns no entity, leaving nothing to return',
}

RULE_NO_SUCCESS = 'no success response'
RULE_EMPTY_RESPONSE = 'empty response'
RULE_RESPONSE_CONTENT = 'response content'
RULE_BARE_STRING = 'bare string payload'
RULE_SCHEMA_REFERENCE = 'schema reference'
RULE_STUB_SCHEMA = 'stub schema'
RULE_REQUEST_BODY = 'request body'
RULE_CROSS_SERVICE = 'cross-service agreement'
RULE_DOCUMENT = 'document'


@dataclass(frozen=True)
class Finding:
    """One defect in one document, named where a reader can act on it."""

    rule: str
    location: str
    detail: str
    is_failure: bool = True


@dataclass
class DocumentReport:
    """What one document holds, and where it falls short."""

    repository: str
    location: str
    operations: int = 0
    success_responses: int = 0
    described_responses: int = 0
    schemas: int = 0
    writes_without_body: int = 0
    findings: List[Finding] = field(default_factory=list)

    @property
    def failures(self) -> List[Finding]:
        return [finding for finding in self.findings if finding.is_failure]

    @property
    def warnings(self) -> List[Finding]:
        return [finding for finding in self.findings if not finding.is_failure]

    @property
    def status(self) -> str:
        """The document's verdict, in the markup the report table renders."""
        if self.failures:
            return f'[red]{len(self.failures)} failing[/red]'
        if self.warnings:
            return f'[yellow]{len(self.warnings)} to review[/yellow]'
        return '[green]complete[/green]'

    @property
    def described(self) -> str:
        return f'{self.described_responses}/{self.success_responses}'


def analyze_document(repository: str, location: str, document: Mapping[str, Any]) -> DocumentReport:
    """Apply the six single-document rules and report what one document holds.

    Cross-service agreement needs every document at once, so cross_document_findings applies it
    separately and adds its findings to the reports this returns.
    """
    report = DocumentReport(repository=repository, location=location)
    paths = document.get('paths') or {}
    schemas = ((document.get('components') or {}).get('schemas')) or {}
    report.schemas = len(schemas)

    for path, path_item in sorted(paths.items()):
        if not isinstance(path_item, Mapping):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, Mapping):
                continue
            report.operations += 1
            _check_responses(document, report, method, path, operation)
            _check_request_body(report, method, path, operation)

    _check_schema_references(document, report, schemas)
    _check_stub_schemas(report, schemas)
    return report


def cross_document_findings(documents: Mapping[str, Mapping[str, Any]]) -> Dict[str, List[Finding]]:
    """Rule seven: where a schema name means one thing in one service and something else in another.

    A generated client resolves a name once. Two services that answer with `FolderServerFolder` and
    disagree about its fields produce a client that is wrong for one of them, and the disagreement
    shows up as a deserialization failure in a caller rather than as anything either service can see.
    The comparison is structural: the same definition formatted differently agrees.
    """
    forms: Dict[str, Dict[str, List[str]]] = {}
    for repository, document in documents.items():
        schemas = ((document.get('components') or {}).get('schemas')) or {}
        for name, schema in schemas.items():
            forms.setdefault(name, {}).setdefault(_canonical(schema), []).append(repository)

    findings: Dict[str, List[Finding]] = {}
    for name, by_form in sorted(forms.items()):
        if len(by_form) < 2:
            continue
        for form, repositories in by_form.items():
            others = sorted({repository
                             for other_form, other_repositories in by_form.items()
                             if other_form != form
                             for repository in other_repositories})
            for repository in repositories:
                findings.setdefault(repository, []).append(Finding(
                    rule=RULE_CROSS_SERVICE,
                    location=name,
                    detail=f'defined differently in {", ".join(others)}'))
    return findings


def unreadable_document(repository: str, location: str, reason: str) -> DocumentReport:
    """A report for a document that could not be parsed, which fails on that alone."""
    report = DocumentReport(repository=repository, location=location)
    report.findings.append(Finding(rule=RULE_DOCUMENT, location=location, detail=reason))
    return report


def _check_responses(document: Mapping[str, Any], report: DocumentReport,
                     method: str, path: str, operation: Mapping[str, Any]) -> None:
    """Rules one and two: every operation declares a 2xx, and describes the content it answers with.

    A resource method with no OpenAPI annotations at all reaches the document with a `default`
    response and nothing else. That is not a described operation with no payload; it is an
    operation nobody documented, and counting it as complete is how fifteen of one service's routes
    stayed invisible to this check while it reported that service as done.
    """
    responses = operation.get('responses') or {}
    if not any(_is_success(status) for status in responses):
        report.findings.append(Finding(
            rule=RULE_NO_SUCCESS, location=f'{method.upper()} {path}',
            detail='declares no 2xx response, so the operation carries no documentation at all'))
        return
    for status in sorted(responses):
        if not _is_success(status) or status == NO_CONTENT_STATUS:
            continue
        report.success_responses += 1
        response = responses[status]
        where = f'{method.upper()} {path} {status}'
        if (report.repository, method.upper(), path, status) in EMPTY_RESPONSES:
            report.described_responses += 1
            if isinstance(response, Mapping) and response.get('content'):
                report.findings.append(Finding(
                    rule=RULE_EMPTY_RESPONSE, location=where,
                    detail='listed as carrying no body, and now declares content',
                    is_failure=False))
            continue
        if isinstance(response, Mapping) and '$ref' in response:
            resolved = _resolve(document, response['$ref'])
            if resolved is None:
                report.findings.append(Finding(
                    rule=RULE_RESPONSE_CONTENT, location=where,
                    detail=f'the response reference {response["$ref"]} does not resolve'))
                continue
            response = resolved
        if isinstance(response, Mapping) and response.get('content'):
            report.described_responses += 1
            _check_bare_strings(report, where, response)
        else:
            report.findings.append(Finding(
                rule=RULE_RESPONSE_CONTENT, location=where,
                detail='declares no content, so a client has no type to return'))


def _check_request_body(report: DocumentReport, method: str, path: str,
                        operation: Mapping[str, Any]) -> None:
    """Rule six: every write declares the body it reads, unless its handler reads none."""
    if method not in WRITE_METHODS:
        return
    where = f'{method.upper()} {path}'
    allowlisted = (report.repository, method.upper(), path) in BODILESS_WRITES
    declares_body = bool(operation.get('requestBody'))
    if declares_body and allowlisted:
        report.findings.append(Finding(
            rule=RULE_REQUEST_BODY, location=where,
            detail='allowlist entry is stale: the operation now declares a requestBody',
            is_failure=False))
        return
    if not declares_body and not allowlisted:
        report.writes_without_body += 1
        report.findings.append(Finding(
            rule=RULE_REQUEST_BODY, location=where,
            detail='declares no requestBody, so a client cannot send the payload the handler reads'))
        return
    if declares_body:
        _check_bare_strings(report, f'{where} request body', operation['requestBody'])


def _check_bare_strings(report: DocumentReport, where: str, payload: Mapping[str, Any]) -> None:
    """Rule three: a structured payload carries a schema rather than the type of a raw string."""
    content = payload.get('content') if isinstance(payload, Mapping) else None
    if not isinstance(content, Mapping):
        return
    for media_type, media in sorted(content.items()):
        if not isinstance(media_type, str) or not media_type.endswith(STRUCTURED_MEDIA_SUFFIXES):
            continue
        schema = media.get('schema') if isinstance(media, Mapping) else None
        if isinstance(schema, Mapping) and schema == {'type': 'string'}:
            report.findings.append(Finding(
                rule=RULE_BARE_STRING, location=f'{where} ({media_type})',
                detail='typed as a bare string, which is what an unannotated String body parameter '
                       'generates'))


def _check_schema_references(document: Mapping[str, Any], report: DocumentReport,
                             schemas: Mapping[str, Any]) -> None:
    """Rule four: every reference to a component schema names one the document defines."""
    for ref, where in sorted(set(_iter_schema_refs(document))):
        name = ref[len(SCHEMA_REF_PREFIX):]
        if name.split('/')[0] not in schemas:
            report.findings.append(Finding(
                rule=RULE_SCHEMA_REFERENCE, location=where,
                detail=f'{ref} names a schema this document does not define'))


def _check_stub_schemas(report: DocumentReport, schemas: Mapping[str, Any]) -> None:
    """Rule five: a named schema carries a definition rather than an empty object.

    An object schema with no members generates as a type with no fields, which reads as a described
    response and behaves like an undescribed one. A schema that is only a reference is not a stub:
    its definition is the one it points at.
    """
    for name, schema in sorted(schemas.items()):
        if not isinstance(schema, Mapping) or '$ref' in schema:
            continue
        if schema.get('type') not in (None, 'object'):
            continue
        if any(schema.get(key) for key in
               ('properties', 'additionalProperties', 'allOf', 'oneOf', 'anyOf', 'enum')):
            continue
        report.findings.append(Finding(
            rule=RULE_STUB_SCHEMA, location=name,
            detail='declares no members, so it generates as a type with no fields'))


def _iter_schema_refs(node: Any, trail: str = '') -> Iterator[Tuple[str, str]]:
    """Every reference to a component schema in the document, with where it was written."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == '$ref' and isinstance(value, str) and value.startswith(SCHEMA_REF_PREFIX):
                yield value, trail or '(document root)'
            else:
                yield from _iter_schema_refs(value, f'{trail}.{key}' if trail else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_schema_refs(value, f'{trail}[{index}]')


def _resolve(document: Mapping[str, Any], ref: str) -> Any:
    """The document member a local reference points at, or None when it points nowhere."""
    if not isinstance(ref, str) or not ref.startswith('#/'):
        return None
    node: Any = document
    for step in ref[2:].split('/'):
        step = step.replace('~1', '/').replace('~0', '~')
        if not isinstance(node, Mapping) or step not in node:
            return None
        node = node[step]
    return node


def _is_success(status: str) -> bool:
    return isinstance(status, str) and len(status) == 3 and status.startswith('2') and status.isdigit()


def _canonical(schema: Any) -> str:
    """A schema definition reduced to one string, so two definitions compare structurally."""
    return json.dumps(schema, sort_keys=True, separators=(',', ':'))
