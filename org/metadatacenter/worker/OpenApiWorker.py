"""Reads the committed OpenAPI documents across the estate and reports where they fall short."""

import json
from pathlib import Path

from rich.console import Console
from rich.table import Column, Table

from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.OpenApiContract import (
    DOCUMENT_GLOB,
    SKIPPED_REPOSITORIES,
    analyze_document,
    cross_document_findings,
    unreadable_document,
)
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()

# Only a Java repository ships an OpenAPI document. The frontends consume the documents and the rest
# have no HTTP surface, so looking for one under them would report an absence that is correct.
DOCUMENT_TYPES = (RepoType.JAVA, RepoType.JAVA_WRAPPER)


class OpenApiWorker(Worker):

    @staticmethod
    def check_openapi(show_all=False):
        """Check every committed OpenAPI document for the completeness a generated client needs.

        Exits non-zero on any failure, which is the condition that leaves an operation in the
        generated client with no type to send or return.
        """
        reports = []
        documents = {}
        for repository, location, path in OpenApiWorker._document_paths():
            try:
                document = json.loads(path.read_text())
            except (OSError, ValueError) as error:
                reports.append(unreadable_document(
                    repository, location, f'the document could not be read: {error}'))
                continue
            if not isinstance(document, dict):
                reports.append(unreadable_document(
                    repository, location, 'the document is not a JSON object'))
                continue
            documents[repository] = document
            reports.append(analyze_document(repository, location, document))

        if not reports:
            console.print('[red]No OpenAPI document was found under CEDAR_HOME, so the estate\'s '
                          'API contracts could not be checked.[/red]')
            console.print(f'Each microservice repository holds one at {DOCUMENT_GLOB}.')
            return 1

        divergences = cross_document_findings(documents)
        for report in reports:
            report.findings.extend(divergences.get(report.repository, []))

        console.print(OpenApiWorker._summary_table(reports))
        OpenApiWorker._print_findings(reports, show_all)

        failing = [report for report in reports if report.failures]
        if failing:
            console.print(
                '\nAn operation that describes no content generates as an operation with no type, '
                'and the caller parses the payload itself. Describe the response on the resource '
                'method with the schema it answers with, then run this check again.')
            return 1
        console.print('\n[green]Every checked document describes what a client needs.[/green]')
        return 0

    @staticmethod
    def _document_paths():
        """Every committed document under a Java repository, with the repository that owns it."""
        seen = set()
        for repo in Worker.get_flat_repo_list(GlobalContext.repos.get_list_all()):
            if repo.repo_type not in DOCUMENT_TYPES or repo.name in SKIPPED_REPOSITORIES:
                continue
            if repo.get_fqn() in seen:
                continue
            seen.add(repo.get_fqn())
            root = Path(Util.get_wd(repo))
            for path in sorted(root.glob(DOCUMENT_GLOB)):
                yield repo.name, str(path)[len(Util.cedar_home):], path

    @staticmethod
    def _summary_table(reports):
        table = Table(
            Column(header='Repository', no_wrap=True),
            Column(header='Operations', justify='right'),
            Column(header='2xx described', justify='right'),
            Column(header='Schemas', justify='right'),
            Column(header='Writes without body', justify='right'),
            Column(header='Status'),
            title='OpenAPI contract completeness',
        )
        for report in sorted(reports, key=lambda item: (item.repository, item.location)):
            table.add_row(
                report.repository,
                str(report.operations),
                report.described,
                str(report.schemas),
                str(report.writes_without_body),
                report.status,
            )
        table.caption = OpenApiWorker._caption(reports)
        return table

    @staticmethod
    def _caption(reports):
        operations = sum(report.operations for report in reports)
        described = sum(report.described_responses for report in reports)
        successes = sum(report.success_responses for report in reports)
        failures = sum(len(report.failures) for report in reports)
        warnings = sum(len(report.warnings) for report in reports)
        failing = [report for report in reports if report.failures]

        caption = (f'{len(reports)} documents, {operations} operations, '
                   f'{described}/{successes} 2xx responses described')
        if failures:
            caption += (f'\n[red]{failures} failures in {len(failing)} documents: '
                        + ', '.join(sorted({report.repository for report in failing})) + '[/red]')
        if warnings:
            caption += f'\n[yellow]{warnings} to review[/yellow]'
        caption += ('\nWrites without body counts the POST, PUT and PATCH operations that declare no '
                    'requestBody and are not on the bodiless allowlist.')
        if SKIPPED_REPOSITORIES:
            skipped = ', '.join(f'{name} ({reason})'
                                for name, reason in sorted(SKIPPED_REPOSITORIES.items()))
            caption += f'\nSkipped: {skipped}'
        return caption

    @staticmethod
    def _print_findings(reports, show_all):
        """The findings under each document, so a reader sees one service's work in one place."""
        for report in sorted(reports, key=lambda item: (item.repository, item.location)):
            if not report.findings and not show_all:
                continue
            console.print(f'\n[bold]{report.repository}[/bold] {report.location}')
            if not report.findings:
                console.print('  [green]no findings[/green]')
                continue
            for finding in report.findings:
                style = 'red' if finding.is_failure else 'yellow'
                console.print(
                    f'  [{style}]{finding.rule}[/{style}]  {finding.location}: {finding.detail}',
                    highlight=False)
