import json
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from org.metadatacenter import concordance_check


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(['git', '-C', str(repository), *arguments], check=True, capture_output=True,
                          text=True).stdout.strip()


class ConcordanceCheckTest(unittest.TestCase):
    """Locks recorded against two Java repositories, judged against each repository's remote develop."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.validation = self.repository('cedar-model-validation-library')
        self.artifact = self.repository('cedar-artifact-library')

    def repository(self, name):
        path = self.home / name
        path.mkdir()
        git(path, 'init', '-q')
        git(path, 'config', 'user.email', 'test@example.org')
        git(path, 'config', 'user.name', 'Test')
        # Distinct content, so the two repositories' first commits differ.
        self.commit(path, 'src/main/Validator.java', f'{name} rule 1')
        return path

    def commit(self, repository, path, text, *, publish=True):
        file = repository / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
        git(repository, 'add', path)
        git(repository, 'commit', '-q', '-m', f'change {path}')
        if publish:
            git(repository, 'update-ref', 'refs/remotes/origin/develop', 'HEAD')
        return git(repository, 'rev-parse', 'HEAD')

    def lock(self, name, commit):
        path = self.home / concordance_check.TYPESCRIPT_LIBRARY / concordance_check.LOCKS / f'{name}-lock.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'javaCommit': commit, 'sha256': '0' * 64, 'caseCount': 1}))

    def run_check(self):
        output = StringIO()
        with patch.object(concordance_check, 'console', Console(file=output, width=400)):
            code = concordance_check.check_concordance(self.home)
        return code, output.getvalue()

    def test_a_lock_at_the_current_production_code_passes_even_after_test_changes(self):
        recorded = git(self.validation, 'rev-parse', 'HEAD')
        self.lock('regex-policy', recorded)
        self.commit(self.validation, 'src/test/RegexTest.java', 'a new test')
        self.lock('java-rule-matrix', git(self.artifact, 'rev-parse', 'HEAD'))

        code, output = self.run_check()

        self.assertEqual(0, code, output)
        self.assertIn('regex-policy: ' + recorded[:7] + ' current with cedar-model-validation-library', output)
        self.assertIn('java-rule-matrix: ', output)

    def test_a_lock_older_than_a_production_change_on_develop_fails(self):
        recorded = git(self.artifact, 'rev-parse', 'HEAD')
        self.lock('java-rule-matrix', recorded)
        self.lock('regex-policy', git(self.validation, 'rev-parse', 'HEAD'))
        self.commit(self.artifact, 'src/main/Reader.java', 'reads differently')

        code, output = self.run_check()

        self.assertEqual(1, code)
        self.assertIn("java-rule-matrix: " + recorded[:7] + " cedar-artifact-library's src/main has changed "
                      "in 1 file since, e.g. src/main/Reader.java", output)
        self.assertIn('1 of 2 concordance fixtures', output)

    def test_only_the_remote_develop_head_counts(self):
        self.lock('regex-policy', git(self.validation, 'rev-parse', 'HEAD'))
        self.commit(self.validation, 'src/main/Validator.java', 'rule 2, not pushed', publish=False)

        self.assertEqual(0, self.run_check()[0])

    def test_a_commit_no_java_repository_holds_fails(self):
        self.lock('instance-verdicts', 'f' * 40)

        code, output = self.run_check()

        self.assertEqual(1, code)
        self.assertIn('instance-verdicts: fffffff names a commit neither Java repository holds', output)

    def test_no_locks_fails_rather_than_passing_empty(self):
        code, output = self.run_check()

        self.assertEqual(1, code)
        self.assertIn('No concordance locks', output)


if __name__ == '__main__':
    unittest.main()
