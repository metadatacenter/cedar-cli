import os
import subprocess
import tempfile
import unittest

from org.metadatacenter.model.GitSyncState import GitSyncKind
from org.metadatacenter.util.GitSync import GitSync


def git(cwd, *arguments):
    subprocess.run(["git"] + list(arguments), cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class GitSyncAgainstRealGitTest(unittest.TestCase):
    """
    The states are read from git itself rather than from a mock.

    Each one arises the way it does in a workspace: a checkout falls behind because somebody else
    pushed, and moves ahead because this copy committed. Nothing here rewrites history.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = self.temp.name
        self.origin = os.path.join(root, "origin.git")
        self.producer = os.path.join(root, "producer")
        self.consumer = os.path.join(root, "consumer")

        git(root, "init", "--bare", "--initial-branch=develop", self.origin)
        git(root, "clone", self.origin, self.producer)
        self.commit(self.producer, "first")
        git(self.producer, "push", "-u", "origin", "develop")
        git(root, "clone", self.origin, self.consumer)
        GitSync.clear_cache()

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, repo, text):
        with open(os.path.join(repo, "version.txt"), "w") as handle:
            handle.write(text + "\n")
        git(repo, "add", "version.txt")
        git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", text)

    def test_a_fresh_clone_is_current(self):
        state = GitSync.state_for_dir(self.consumer)

        self.assertEqual(GitSyncKind.CURRENT, state.kind)
        self.assertEqual("develop", state.branch)
        self.assertEqual("current", state.describe())

    def test_a_clone_somebody_else_pushed_past_is_behind(self):
        self.commit(self.producer, "second")
        git(self.producer, "push", "origin", "develop")
        git(self.consumer, "fetch")
        GitSync.clear_cache()

        state = GitSync.state_for_dir(self.consumer)

        self.assertEqual(GitSyncKind.BEHIND, state.kind)
        self.assertEqual(1, state.behind)
        self.assertTrue(state.explains_a_stale_version(),
                        "this is the case that must not be reported as a version defect")

    def test_a_clone_with_an_unpushed_commit_is_ahead(self):
        self.commit(self.consumer, "local work")
        GitSync.clear_cache()

        state = GitSync.state_for_dir(self.consumer)

        self.assertEqual(GitSyncKind.AHEAD, state.kind)
        self.assertEqual(1, state.ahead)
        self.assertFalse(state.explains_a_stale_version())

    def test_a_clone_that_both_committed_and_fell_behind_is_diverged(self):
        self.commit(self.producer, "second")
        git(self.producer, "push", "origin", "develop")
        self.commit(self.consumer, "local work")
        git(self.consumer, "fetch")
        GitSync.clear_cache()

        state = GitSync.state_for_dir(self.consumer)

        self.assertEqual(GitSyncKind.DIVERGED, state.kind)
        self.assertFalse(state.explains_a_stale_version(),
                         "a diverged copy holds local commits, so its version is not the remote's")

    def test_a_directory_that_is_not_a_repository_is_named_as_such(self):
        plain = os.path.join(self.temp.name, "plain")
        os.mkdir(plain)

        self.assertEqual(GitSyncKind.NOT_A_REPO, GitSync.state_for_dir(plain).kind)
        self.assertEqual(GitSyncKind.NOT_A_REPO, GitSync.state_for_dir("/no/such/path").kind)

    def test_a_subdirectory_resolves_to_its_repository(self):
        """A sub-repository in the estate's list shares its parent's git root."""
        nested = os.path.join(self.consumer, "nested", "deeper")
        os.makedirs(nested)

        self.assertEqual(GitSyncKind.CURRENT, GitSync.state_for_dir(nested).kind)

    def test_a_clone_reports_when_it_last_heard_from_its_remote(self):
        git(self.consumer, "fetch")
        GitSync.clear_cache()

        state = GitSync.state_for_dir(self.consumer)

        self.assertIsNotNone(state.fetch_age_seconds,
                             "an ahead/behind count is only as fresh as the last fetch")
        self.assertLess(state.fetch_age_seconds, 300)


if __name__ == "__main__":
    unittest.main()
