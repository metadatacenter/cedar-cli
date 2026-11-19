"""Whether the Java verdicts the TypeScript model library is held to still come from the Java code.

The TypeScript library's concordance gates compare its reading, writing and validation with fixtures
recorded from the Java libraries. Each fixture has a lock that names the Java commit it was recorded
at. A fixture recorded before a change to a Java library's production code may hold verdicts that code
no longer reaches, and the TypeScript gate then stays green against old answers. This check finds,
for each lock, the Java repository that holds its commit, and fails when that repository's `src/main`
has changed between the commit and its `develop` head on the remote.
"""
from dataclasses import dataclass
from pathlib import Path
import json
import subprocess

from rich.console import Console

console = Console()

TYPESCRIPT_LIBRARY = 'cedar-model-typescript-library'
LOCKS = Path('itest') / 'resources' / 'concordance'
# The repositories the TypeScript library records from, in the order a commit is looked up.
JAVA_REPOSITORIES = ('cedar-model-validation-library', 'cedar-artifact-library')
HEAD = 'refs/remotes/origin/develop'
REMEDY = ('Record each again from the current Java develop head, with '
          '`npm run sync:concordance -- --matrix <name>` in cedar-model-typescript-library or, for the '
          'instance verdicts, `ops/cedar_corpus_instance_verdicts.py` in cedar-development, and commit '
          'the fixture and its lock.')


@dataclass(frozen=True)
class LockState:
    name: str
    commit: str
    repository: str | None
    changed: tuple[str, ...]

    @property
    def current(self) -> bool:
        return self.repository is not None and not self.changed


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(repository), *arguments],
                          capture_output=True, text=True, check=False)


def lock_states(home: Path) -> list[LockState]:
    """Each concordance lock with the repository that holds its commit and what changed since."""
    directory = home / TYPESCRIPT_LIBRARY / LOCKS
    locks = sorted(directory.glob('*-lock.json'))
    if not locks:
        raise ValueError(f'No concordance locks under {directory}')
    states = []
    for lock in locks:
        name = lock.name.removesuffix('-lock.json')
        try:
            commit = json.loads(lock.read_text())['javaCommit']
        except (OSError, ValueError, KeyError) as error:
            raise ValueError(f'{lock} names no javaCommit: {error}') from error
        holder = next((home / repository for repository in JAVA_REPOSITORIES
                       if _git(home / repository, 'cat-file', '-e', f'{commit}^{{commit}}').returncode == 0),
                      None)
        if holder is None:
            states.append(LockState(name, commit, None, ()))
            continue
        diff = _git(holder, 'diff', '--name-only', commit, HEAD, '--', 'src/main')
        if diff.returncode:
            raise ValueError(f'{holder.name} cannot compare {commit[:7]} with {HEAD}: {diff.stderr.strip()}')
        states.append(LockState(name, commit, holder.name, tuple(diff.stdout.split())))
    return states


def check_concordance(home: Path) -> int:
    """Print each lock's state and return non-zero when any is stale or names an unknown commit."""
    try:
        states = lock_states(home)
    except ValueError as error:
        console.print(str(error), markup=False)
        return 1
    for state in states:
        if state.repository is None:
            verdict = 'names a commit neither Java repository holds; fetch them, or record it again'
        elif state.changed:
            count = len(state.changed)
            verdict = (f"{state.repository}'s src/main has changed in {count} "
                       f"{'file' if count == 1 else 'files'} since, e.g. {state.changed[0]}")
        else:
            verdict = f'current with {state.repository} develop'
        console.print(f'{state.name}: {state.commit[:7]} {verdict}', markup=False, soft_wrap=True)
    stale = [state for state in states if not state.current]
    if stale:
        console.print(f'{len(stale)} of {len(states)} concordance fixtures may hold verdicts the Java code '
                      f'no longer reaches. {REMEDY}', markup=False, soft_wrap=True)
        return 1
    console.print(f'All {len(states)} concordance fixtures were recorded from the current Java production code.')
    return 0
