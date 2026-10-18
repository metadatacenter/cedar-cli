"""Finds Java test classes that start a server or store without carrying the integration tag.

A Java test that starts a Dropwizard application, an embedded store, embedded Redis, OpenSearch or
a local socket server carries `@Tag("integration")`. Every other test is a unit test. Nothing in
Maven fails when a new suite leaves the tag off, so without this check the split drifts silently.
The check reads the test sources and names each class that starts one of those without the tag.

A class starts something when its own source does, when a class it extends does, or when it uses a
test-source helper that does. The tag counts when the class or any class it extends carries it,
since JUnit inherits `@Tag` down the class hierarchy. Comments are ignored, so a Javadoc mention of
a harness is not a start.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

INTEGRATION_TAG = 'integration'

# What a test starts, by the type or call that starts it. Each pattern names a construction or a
# harness, not a client interface that a unit test would mock.
STARTS = {
    'a Dropwizard application': r'\b(DropwizardAppExtension|DropwizardTestSupport|DropwizardAppRule)\b',
    'an embedded Mongo': r'\bEmbeddedCedarMongo\b|\bTransitionWalker\b|de\.flapdoodle',
    'an embedded MySQL': r'\bEmbeddedCedarMySql\b|\bMariaDB4j\b|ch\.vorburger',
    'an embedded Neo4j': r'\bEmbeddedCedarNeo4j\b|\bNeo4jBuilders\b|\bTestServerBuilders\b',
    'an embedded Redis': r'\bRedisServer\b|redis\.embedded|\bEmbeddedRedis\w*',
    'OpenSearch': r'\b(RestHighLevelClient|OpenSearchClient)\b|org\.opensearch\.client',
    'a local socket server': (r'\bHttpServer\.create\b|\bnew ServerSocket\b|\bMockWebServer\b'
                              r'|\bWireMock\w*|\bTcpFaultProxy\b|GrizzlyHttpServerFactory'),
    'a container': r'\bTestcontainers\b|\bGenericContainer\b',
}
_STARTS = {name: re.compile(pattern) for name, pattern in STARTS.items()}
# A unit test may hold a mocked OpenSearch client; only a constructed one talks to the engine.
_MOCKED_SEARCH_CLIENT = re.compile(r'mock\((RestHighLevelClient|OpenSearchClient)|@Mock\s+\w*Client')

_TAG = re.compile(r'@Tag\(\s*"' + INTEGRATION_TAG + r'"\s*\)')
_TEST_METHOD = re.compile(r'@(Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b')
_DECLARATION = re.compile(
    r'^(?:public\s+|protected\s+)?((?:abstract\s+|final\s+)*)class\s+(\w+)'
    r'(?:<[^>{]*>)?(?:\s+extends\s+([\w.]+))?', re.M)
_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
_LINE_COMMENT = re.compile(r'//[^\n]*')
_PRUNED = {'.git', 'target', 'node_modules', 'build'}


@dataclass
class TestClass:
    name: str
    path: Path
    code: str
    parent: str | None
    abstract: bool
    tests: int
    tagged: bool
    starts: list = field(default_factory=list)


@dataclass
class Finding:
    """An integration class with no tag on itself or any class it extends."""
    path: Path
    name: str
    reason: str
    subclasses: int = 0


@dataclass
class Report:
    root: Path
    classes: int = 0
    integration: int = 0
    findings: list = field(default_factory=list)


def _strip_comments(text):
    return _LINE_COMMENT.sub('', _BLOCK_COMMENT.sub('', text))


def parse(path, text):
    """The top-level class a test source declares, or None for a source that declares none."""
    code = _strip_comments(text)
    match = _DECLARATION.search(code)
    if not match:
        return None
    starts = [name for name, pattern in _STARTS.items() if pattern.search(code)]
    if 'OpenSearch' in starts and _MOCKED_SEARCH_CLIENT.search(code):
        starts.remove('OpenSearch')
    return TestClass(
        name=match.group(2),
        path=path,
        code=code,
        parent=match.group(3).split('.')[-1] if match.group(3) else None,
        abstract='abstract' in match.group(1),
        tests=len(_TEST_METHOD.findall(code)),
        tagged=bool(_TAG.search(code)),
        starts=starts,
    )


def test_sources(root):
    """Every Java source under a test source root in one repository, generated output excluded."""
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = sorted(d for d in subdirectories if d not in _PRUNED)
        if f'{os.sep}src{os.sep}test{os.sep}' not in f'{directory}{os.sep}':
            continue
        for name in sorted(files):
            if name.endswith('.java'):
                yield Path(directory) / name


def analyze(classes, root=Path('.')):
    """Report the integration classes among one repository's test classes that carry no tag."""
    by_name = {}
    for test_class in classes:
        by_name.setdefault(test_class.name, test_class)

    def parent_of(test_class):
        return by_name.get(test_class.parent) if test_class.parent else None

    # Why each class is an integration class: what it starts itself, then what it inherits or
    # uses, carried to a fixed point so a helper of a helper still counts.
    reasons = {c.name: f'starts {c.starts[0]}' for c in classes if c.starts}
    changed = True
    while changed:
        changed = False
        for test_class in classes:
            if test_class.name in reasons:
                continue
            parent = parent_of(test_class)
            if parent and parent.name in reasons:
                reasons[test_class.name] = f'extends {parent.name}, which {reasons[parent.name]}'
                changed = True
                continue
            for name in reasons:
                if name != test_class.name and re.search(rf'\b{name}\b', test_class.code):
                    reasons[test_class.name] = f'uses {name}, which {reasons[name]}'
                    changed = True
                    break

    def covered(test_class, seen=()):
        if test_class.tagged:
            return True
        parent = parent_of(test_class)
        return bool(parent) and parent.name not in seen and covered(parent, seen + (test_class.name,))

    subclasses = {}
    for test_class in classes:
        parent = parent_of(test_class)
        if parent:
            subclasses.setdefault(parent.name, []).append(test_class)

    report = Report(root=root)
    for test_class in classes:
        runs = test_class.tests > 0 or bool(subclasses.get(test_class.name))
        if not runs:
            continue  # A helper is not a suite; the suites that use it answer for it.
        report.classes += 1
        if test_class.name not in reasons:
            continue
        report.integration += 1
        if covered(test_class):
            continue
        parent = parent_of(test_class)
        if parent and parent.name in reasons and not covered(parent):
            continue  # Tagging the uncovered ancestor covers this class too; report that one.
        descendants = _descendants(test_class.name, subclasses)
        report.findings.append(Finding(
            path=test_class.path, name=test_class.name, reason=reasons[test_class.name],
            subclasses=len(descendants)))
    report.findings.sort(key=lambda finding: str(finding.path))
    return report


def _descendants(name, subclasses):
    found = []
    for child in subclasses.get(name, []):
        found.append(child)
        found.extend(_descendants(child.name, subclasses))
    return found


def scan(root):
    """Read one repository's test sources and report its untagged integration classes."""
    root = Path(root)
    classes = []
    for path in test_sources(root):
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        test_class = parse(path.relative_to(root), text)
        if test_class:
            classes.append(test_class)
    return analyze(classes, root)
