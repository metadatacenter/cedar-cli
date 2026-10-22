"""Coverage figures for a Java build that ran tests.

cedar-parent attaches JaCoCo to every test run, and each module that runs tests writes a report on the
classes it compiles. That report credits the classes only with the module's own tests, so a shared
library's code that a server's suites run counts in neither report. After a build, cedarcli therefore
also merges every test JVM's execution data and credits each module's classes with any test that ran
them. Between the two lies what the module's repository tests on its own, which is what its CI runs
before any consumer sees a change: a module tested from a sibling module counts there. The three
figures go to the console as one line, and per module and per repository to a JSON file.
cedar-project's CI runs the same report over its workspace through `python -m org.metadatacenter.coverage`.

The figures are a report, never a gate: a build that passed stays passed when they cannot be computed.
"""
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree
import json
import os
import subprocess
import tempfile

from rich.console import Console

from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.util.Util import Util

console = Console()

HELPER = Path(__file__).resolve().parents[2] / 'scripts' / 'java' / 'MergedCoverage.java'
MAVEN_NAMESPACE = {'m': 'http://maven.apache.org/POM/4.0.0'}
PRUNED = ('.git', 'node_modules', 'src')


class CoverageUnavailable(Exception):
    """Why the merged figure could not be computed, phrased to follow "because"."""


def java_checkouts():
    home = Path(Util.cedar_home).resolve()
    return {home / repo.get_fqn() for repo in GlobalContext.repos.get_list_all()
            if repo.repo_type in (RepoType.JAVA, RepoType.JAVA_WRAPPER)}


def target_directories(checkouts):
    """Every Maven target directory under these checkouts.

    An aggregator such as cedar-project builds sibling checkouts through `../` modules, and Maven
    writes their output there, so the plan's own repositories cannot say where to look.
    """
    for checkout in checkouts:
        for directory, children, _ in os.walk(checkout):
            if Path(directory).name == 'target':
                children[:] = []
                yield Path(directory)
            else:
                children[:] = [child for child in children if child not in PRUNED]


def coverage_reports(checkouts, started: float):
    """The JaCoCo reports written under these checkouts since a build started."""
    return {report for target in target_directories(checkouts)
            for report in (target / 'site').glob('jacoco*/index.html')
            if report.stat().st_mtime >= started}


def execution_data(checkouts, started: float):
    """The non-empty execution-data files the build's test JVMs wrote."""
    return sorted(execution for target in target_directories(checkouts)
                  for execution in target.glob('jacoco*.exec')
                  if execution.stat().st_mtime >= started and execution.stat().st_size > 0)


def read_report(report_xml: Path):
    """The classes a report analysed, as class files, and its own line and branch counters.

    The class list is the report's, so the exclusions a module's POM configures carry over. The
    report lives at <module>/target/site/<name>/jacoco.xml, and the classes in <module>/target/classes.
    """
    try:
        root = ElementTree.parse(report_xml).getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise CoverageUnavailable(f"{report_xml} could not be read ({error})") from error
    classes = report_xml.parents[2] / 'classes'
    class_files = [classes / f"{element.get('name')}.class" for element in root.iter('class')]
    counters = {element.get('type'): (int(element.get('covered')),
                                      int(element.get('covered')) + int(element.get('missed')))
                for element in root.findall('counter')}
    return class_files, counters.get('LINE', (0, 0)), counters.get('BRANCH', (0, 0))


def jacoco_classpath(home: Path, repository: Path):
    """JaCoCo's core library and the ASM release it was built against, from the local repository.

    The JaCoCo version is the one cedar-parent pins, so the analysis reads the execution data with the
    release that wrote it. Every build that runs the plugin has already resolved these jars.
    """
    try:
        project = ElementTree.parse(home / 'cedar-parent' / 'pom.xml').getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise CoverageUnavailable(f"cedar-parent's POM could not be read ({error})") from error
    version = project.findtext('m:properties/m:jacoco.maven.plugin.version', namespaces=MAVEN_NAMESPACE)
    if not version:
        raise CoverageUnavailable("cedar-parent names no jacoco.maven.plugin.version")
    jacoco = repository / 'org' / 'jacoco'
    try:
        build = ElementTree.parse(jacoco / 'org.jacoco.build' / version / f'org.jacoco.build-{version}.pom')
    except (OSError, ElementTree.ParseError) as error:
        raise CoverageUnavailable(f"JaCoCo {version}'s build POM is not in the local repository") from error
    asm = build.getroot().findtext('m:properties/m:asm.version', namespaces=MAVEN_NAMESPACE)
    jars = [jacoco / 'org.jacoco.core' / version / f'org.jacoco.core-{version}.jar']
    jars += [repository / 'org' / 'ow2' / 'asm' / name / asm / f'{name}-{asm}.jar'
             for name in ('asm', 'asm-commons', 'asm-tree')]
    missing = [jar.name for jar in jars if not jar.is_file()]
    if not asm or missing:
        raise CoverageUnavailable(f"the local repository lacks {', '.join(missing) or 'an ASM version'}")
    return jars


def repository_of(path: Path, home: Path):
    """The checkout a module or an execution-data file belongs to: its first directory under home."""
    try:
        return path.relative_to(home).parts[0]
    except (ValueError, IndexError) as error:
        raise CoverageUnavailable(f"{path} is not inside a checkout under {home}") from error


def merged_figures(modules, executions, java: Path, classpath):
    """Run the helper over {module directory: (repository, class files)} and [(repository, execution)].

    Returns module directory -> (covered lines, lines, covered branches, branches, lines covered by
    the repository's tests, branches covered by the repository's tests).
    """
    with tempfile.TemporaryDirectory() as directory:
        execution_list = Path(directory, 'executions.txt')
        execution_list.write_text(''.join(f'{repository}\t{execution}\n' for repository, execution in executions))
        module_list = Path(directory, 'modules.txt')
        module_list.write_text(''.join('\t'.join([repository, str(module)] + [str(c) for c in classes]) + '\n'
                                       for module, (repository, classes) in modules.items()))
        try:
            result = subprocess.run(
                [str(java), '-cp', os.pathsep.join(str(jar) for jar in classpath), str(HELPER),
                 str(execution_list), str(module_list)],
                capture_output=True, text=True, timeout=600, env=invocation_environment())
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CoverageUnavailable(f"the analysis did not run ({error})") from error
    if result.returncode:
        last = (result.stderr.strip().splitlines() or ['no output'])[-1]
        raise CoverageUnavailable(f"the analysis exited {result.returncode}: {last}")
    figures = {}
    for line in result.stdout.splitlines():
        module, *numbers = line.split('\t')
        figures[Path(module)] = tuple(int(number) for number in numbers)
    if set(figures) != set(modules):
        raise CoverageUnavailable("the analysis did not report every module")
    return figures


def measure(reports, executions, home: Path, repository: Path, java: Path):
    """Own-test, repository-test and any-test figures for every module that wrote a report.

    A module that ran Failsafe suites has two reports on the same classes; the Surefire one supplies
    its own-test figure.
    """
    modules = {}
    for report in sorted(reports, key=lambda path: path.parent.name != 'jacoco'):
        module = report.parents[3]
        if module not in modules:
            modules[module] = read_report(report.parent / 'jacoco.xml')
    any_test = merged_figures({module: (repository_of(module, home), classes)
                               for module, (classes, _, _) in modules.items()},
                              [(repository_of(execution, home), execution) for execution in executions],
                              java, jacoco_classpath(home, repository))
    measured = {}
    for module, (_, own_lines, own_branches) in modules.items():
        covered_lines, lines, covered_branches, branches, repository_lines, repository_branches = any_test[module]
        if (lines, branches) != (own_lines[1], own_branches[1]):
            raise CoverageUnavailable(f"{module} changed after its report was written")
        measured[module] = {'lines': own_lines + (covered_lines, repository_lines),
                            'branches': own_branches + (covered_branches, repository_branches)}
    return measured


def figure(covered_own, total, covered_any, covered_repository):
    def percent(covered):
        return round(100 * covered / total, 1) if total else None
    return {'total': total, 'ownCovered': covered_own, 'repositoryCovered': covered_repository,
            'anyCovered': covered_any, 'ownPercent': percent(covered_own),
            'repositoryPercent': percent(covered_repository), 'anyPercent': percent(covered_any)}


def summarize(measured, home: Path):
    """Per module, per repository and estate-wide figures, keyed by path relative to CEDAR_HOME.

    Each counter is (own covered, total, any covered, repository covered).
    """
    def add(into, counters):
        for kind in ('lines', 'branches'):
            into[kind] = [a + b for a, b in zip(into[kind], counters[kind])]
    modules, repositories, estate = [], {}, {'lines': [0, 0, 0, 0], 'branches': [0, 0, 0, 0]}
    for module, counters in sorted(measured.items()):
        name = module.relative_to(home).as_posix()
        modules.append({'module': name, **{kind: figure(*counters[kind]) for kind in counters}})
        add(repositories.setdefault(name.split('/')[0], {'lines': [0, 0, 0, 0], 'branches': [0, 0, 0, 0]}),
            counters)
        add(estate, counters)
    return {
        'schemaVersion': 1,
        'measuredAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        **{kind: figure(*estate[kind]) for kind in estate},
        'repositories': [{'repository': name, **{kind: figure(*counters[kind]) for kind in counters}}
                         for name, counters in sorted(repositories.items())],
        'modules': modules,
    }


def summary_line(summary, path: Path):
    lines, count = summary['lines'], len(summary['modules'])
    noun = 'module' if count == 1 else 'modules'
    return (f"Line coverage: {lines['anyPercent']}% by any test, {lines['repositoryPercent']}% by each "
            f"repository's own tests, {lines['ownPercent']}% by each module's own tests "
            f"({lines['total']:,} lines in {count} {noun}). Per module: {path}")


def prune_coverage(cedar_home, *, days=14, apply=False, now=None):
    """Age retention for the coverage records builds write; dry-run unless explicitly applied.

    Only a record this module wrote is considered: a regular file named for its UTC timestamp whose
    JSON carries the schema version and measurement time. Anything else in the directory is left
    alone, as `cedarcli build diagnostics` leaves alone what it does not recognize.
    """
    import re
    now = now or datetime.now(timezone.utc)
    if days < 0:
        raise ValueError('Retention age must be nonnegative')
    root = Path(cedar_home) / '.cedar' / 'coverage'
    if root.is_symlink():
        raise ValueError('Refusing a symlinked coverage directory')
    if not root.exists():
        return []
    records = []
    for path in root.iterdir():
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r'\d{8}T\d{6}Z\.json', path.name):
            continue
        try:
            content = path.read_bytes()
            record = json.loads(content)
            if record.get('schemaVersion') != 1:
                continue
            measured = datetime.strptime(record['measuredAt'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if measured <= now:
            records.append((measured, path, content))
    result = []
    for measured, path, content in sorted(records, reverse=True):
        expired = (now - measured).total_seconds() >= days * 86400
        item = {'path': str(path), 'bytes': len(content), 'action': 'keep',
                'reason': 'age' if expired else 'within retention'}
        if expired:
            item['action'] = 'would remove'
            if apply:
                # Recheck after inventory; never follow a replaced directory or record.
                if root.is_symlink() or path.is_symlink() or path.read_bytes() != content:
                    raise ValueError('Coverage record changed during cleanup')
                path.unlink()
                item['action'] = 'removed'
        result.append(item)
    return result


def report(checkouts, started: float, home: Path, repository: Path, java):
    """Print what coverage the reports under these checkouts show, and record it.

    `java` is called for the Java executable only once there is something to measure, so a missing
    JDK is reported like any other reason the merged figure cannot be computed. Returns the record's
    path, or None when nothing was recorded.
    """
    reports = coverage_reports(checkouts, started)
    if not reports:
        return None
    noun = "report" if len(reports) == 1 else "reports"
    # Unwrapped, so a log shows each sentence whole at any terminal width.
    console.print(f"JaCoCo wrote {len(reports)} coverage {noun} under the tested modules' "
                  "target/site directories.", soft_wrap=True)
    try:
        measured = measure(reports, execution_data(checkouts, started), home, repository, java())
    except (ModeError, CoverageUnavailable) as reason:
        console.print(f"No merged coverage figure, because {reason}.", style="yellow", soft_wrap=True,
                      markup=False, highlight=False)
        return None
    summary = summarize(measured, home)
    if not summary['lines']['total']:
        return None
    directory = home / '.cedar' / 'coverage'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{summary['measuredAt'].replace('-', '').replace(':', '')}.json"
    path.write_text(json.dumps(summary, indent=2) + '\n')
    console.print(summary_line(summary, path), soft_wrap=True, markup=False, highlight=False)
    return path


def report_coverage(started: float):
    """Say what coverage a build that ran Java tests measured."""
    environment = invocation_environment()
    home = Path(Util.cedar_home).resolve()
    repository = Path(environment.get('HOME') or Path.home()).expanduser() / '.m2' / 'repository'
    report(java_checkouts(), started, home, repository,
           lambda: Path(ModeManager.java_17_home(environment)) / 'bin' / 'java')


def main(argv=None):
    """Report a directory of checkouts outside a cedarcli build, as cedar-project's CI does.

    Every directory in it that holds a pom.xml counts as a checkout, and every report under one
    counts, because a CI workspace holds nothing older than its own run. The record lands in the
    directory's .cedar/coverage. A figure that cannot be computed is reported and never fails the
    caller.
    """
    import argparse
    parser = argparse.ArgumentParser(
        prog='python -m org.metadatacenter.coverage',
        description="Report each module's coverage by its own tests, by its repository's tests and by any test.")
    parser.add_argument('workspace', type=Path, help='The directory holding the checkouts, cedar-parent among them')
    parser.add_argument('--java', type=Path, required=True, help='The Java 17 executable')
    parser.add_argument('--repository', type=Path, default=Path.home() / '.m2' / 'repository',
                        help='The local Maven repository; default ~/.m2/repository')
    arguments = parser.parse_args(argv)
    home = arguments.workspace.resolve()
    checkouts = {child for child in home.iterdir() if (child / 'pom.xml').is_file()}
    report(checkouts, 0, home, arguments.repository, lambda: arguments.java)


if __name__ == '__main__':
    main()
