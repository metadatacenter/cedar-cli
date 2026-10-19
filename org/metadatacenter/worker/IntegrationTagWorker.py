"""Reports the Java test classes that start a server or store without the integration tag."""

from pathlib import Path

from rich.console import Console
from rich.table import Column, Table

from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.IntegrationTags import INTEGRATION_TAG, scan
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()

JAVA_TYPES = (RepoType.JAVA, RepoType.JAVA_WRAPPER)

REMEDY = (f'Add @Tag("{INTEGRATION_TAG}") to each class named above, or to the base class it '
          'extends when the base is what starts the server.')


class IntegrationTagWorker(Worker):

    @staticmethod
    def reports():
        """One report per Java repository that holds test classes."""
        seen = set()
        reports = []
        for repo in Worker.get_flat_repo_list(GlobalContext.repos.get_list_all()):
            if repo.repo_type not in JAVA_TYPES or repo.get_fqn() in seen:
                continue
            seen.add(repo.get_fqn())
            root = Path(Util.get_wd(repo))
            if not root.is_dir():
                continue
            report = scan(root)
            if report.classes:
                reports.append((repo.name, report))
        return reports

    @staticmethod
    def findings():
        """Each untagged integration class as one line, for callers that only need the verdict."""
        return [f'{name}/{finding.path}: {finding.name} {finding.reason}'
                for name, report in IntegrationTagWorker.reports() for finding in report.findings]

    @staticmethod
    def check_test_tags(show_all=False):
        """Print the integration split per repository and exit non-zero on an untagged class."""
        reports = IntegrationTagWorker.reports()
        if not reports:
            console.print('[red]No Java test classes were found under CEDAR_HOME, so the '
                          'integration tag could not be checked.[/red]')
            return 1
        table = Table(
            Column(header='Repository', no_wrap=True),
            Column(header='Test classes', justify='right'),
            Column(header='Integration', justify='right'),
            Column(header='Untagged', justify='right'),
            title=f'Integration tag: @Tag("{INTEGRATION_TAG}")',
        )
        for name, report in sorted(reports, key=lambda item: item[0]):
            if not show_all and not report.findings and not report.integration:
                continue
            untagged = len(report.findings)
            table.add_row(name, str(report.classes), str(report.integration),
                          f'[red]{untagged}[/red]' if untagged else '0')
        classes = sum(report.classes for _, report in reports)
        integration = sum(report.integration for _, report in reports)
        table.caption = (f'{classes} test classes in {len(reports)} repositories, '
                         f'{integration} of them integration classes')
        console.print(table)

        findings = [(name, finding) for name, report in reports for finding in report.findings]
        if not findings:
            console.print('\n[green]Every test class that starts a server or store carries the '
                          'integration tag.[/green]')
            return 0
        console.print(f'\n[red]{len(findings)} integration classes carry no tag:[/red]')
        for name, finding in findings:
            extra = f' (and {finding.subclasses} subclasses)' if finding.subclasses else ''
            console.print(f'  {name}/{finding.path}: {finding.name} {finding.reason}{extra}',
                          markup=False, highlight=False)
        console.print(f'\n{REMEDY}')
        return 1
