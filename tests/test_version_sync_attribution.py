import unittest
from unittest.mock import patch

from org.metadatacenter.model.GitSyncState import GitSyncKind, GitSyncState
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.VersionReport import VersionReport
from org.metadatacenter.worker.VersionWorker import VersionWorker

TARGET = "2.9.11-SNAPSHOT"
STALE = "2.9.10-SNAPSHOT"


def behind(count=2):
    return GitSyncState.tracking("develop", 0, count, 60.0)


def current():
    return GitSyncState.tracking("develop", 0, 0, 60.0)


@patch.dict("os.environ", {"CEDAR_HOME": "/tmp/CEDAR"})
class VersionSyncAttributionTest(unittest.TestCase):
    """
    A version mismatch is attributed to its cause rather than merely counted.

    The estate on disk cannot exercise these cases: it is either current, in which case nothing is
    behind, or it has been pulled, in which case nothing is stale.
    """

    @staticmethod
    def run_check(worker, repo_versions, sync_by_repo, by_file=False):
        repos = [Repo(name, RepoType.MISC, []) for name in repo_versions]

        def add_version(repo, report):
            for index, version in enumerate(repo_versions[repo.name]):
                report.add(repo, f"/{repo.name}", f"file-{index}", "test", version)

        def sync_for(path):
            name = path.rsplit("/", 1)[-1]
            return sync_by_repo.get(name, current())

        with patch("org.metadatacenter.worker.VersionWorker.GlobalContext.repos") as global_repos, \
                patch("org.metadatacenter.worker.VersionWorker.GitSync.state_for_dir", side_effect=sync_for), \
                patch("org.metadatacenter.worker.VersionWorker.Util.write_rich_cedar_file"), \
                patch("org.metadatacenter.worker.VersionWorker.console") as console, \
                patch.object(worker, "get_version_report", side_effect=add_version):
            global_repos.get_list_all.return_value = repos
            returncode = worker.check_versions(by_file=by_file)
        printed = [str(call.args[0]) for call in console.print.call_args_list]
        objects = [call.args[0] for call in console.print.call_args_list]
        return returncode, printed, objects

    def test_a_stale_clone_is_not_a_failure(self):
        worker = VersionWorker()

        returncode, printed, _ = self.run_check(
            worker,
            {"current-repo": [TARGET, TARGET], "unpulled-repo": [STALE, STALE]},
            {"unpulled-repo": behind()})

        self.assertEqual(0, returncode, "a workspace nobody pulled must not fail the check")
        self.assertTrue(any("cedarcli git pull" in line for line in printed),
                        "the remedy for a stale clone should be named")

    def test_a_divergence_in_a_current_checkout_fails(self):
        worker = VersionWorker()

        returncode, printed, _ = self.run_check(
            worker,
            {"current-repo": [TARGET, TARGET], "wrong-repo": [STALE, STALE]},
            {"wrong-repo": current()})

        self.assertEqual(1, returncode)
        self.assertTrue(any("wrong-repo" in line for line in printed))
        self.assertFalse(any("cedarcli git pull" in line for line in printed),
                         "a current checkout is not fixed by pulling")

    def test_a_half_applied_bump_is_reported_as_itself(self):
        worker = VersionWorker()

        returncode, printed, _ = self.run_check(
            worker,
            {"whole-repo": [TARGET, TARGET, TARGET], "half-bumped": [TARGET, STALE]},
            {"half-bumped": behind()})

        self.assertTrue(any("half-applied bump" in line for line in printed))
        self.assertTrue(any("half-bumped" in line for line in printed))
        self.assertEqual(0, returncode, "the stale half is still explained by the clone")

    def test_the_report_carries_one_row_per_repository(self):
        worker = VersionWorker()
        repo_versions = {"a-repo": [TARGET, TARGET, TARGET], "b-repo": [TARGET, TARGET]}

        _, _, objects = self.run_check(worker, repo_versions, {})
        _, _, objects_by_file = self.run_check(worker, repo_versions, {}, by_file=True)

        self.assertEqual(2, objects[0].row_count, "one row per repository by default")
        self.assertEqual(5, objects_by_file[0].row_count, "one row per file under --by-file")


class GitSyncStateTest(unittest.TestCase):

    def test_only_being_behind_excuses_a_stale_version(self):
        self.assertTrue(behind().explains_a_stale_version())
        self.assertFalse(current().explains_a_stale_version())
        self.assertFalse(GitSyncState.tracking("develop", 1, 0, None).explains_a_stale_version())
        self.assertFalse(GitSyncState.no_upstream("develop", None).explains_a_stale_version())
        self.assertFalse(GitSyncState.not_a_repo().explains_a_stale_version())

    def test_a_diverged_copy_is_not_excused_by_its_remote(self):
        diverged = GitSyncState.tracking("develop", 1, 2, None)

        self.assertEqual(GitSyncKind.DIVERGED, diverged.kind)
        self.assertFalse(diverged.explains_a_stale_version(),
                         "local commits mean the version cannot be attributed to the remote alone")
        self.assertEqual("ahead 1, behind 2", diverged.describe())

    def test_a_repo_with_no_upstream_is_named_rather_than_judged(self):
        self.assertEqual("no upstream", GitSyncState.no_upstream("wip", None).describe())
        self.assertEqual("detached", GitSyncState.detached(None).describe())


class RollupStatusTest(unittest.TestCase):

    class FakeEntry:
        def __init__(self, status):
            self.status = status

    def test_internal_disagreement_outranks_every_other_status(self):
        entries = [self.FakeEntry("✅"), self.FakeEntry("❌")]

        self.assertEqual("⚠️", VersionWorker.rollup_status(entries, True))

    def test_the_worst_member_status_wins(self):
        self.assertEqual("❌", VersionWorker.rollup_status(
            [self.FakeEntry("✅"), self.FakeEntry("⏳"), self.FakeEntry("❌")], False))
        self.assertEqual("⏳", VersionWorker.rollup_status(
            [self.FakeEntry("✅"), self.FakeEntry("⏳")], False))
        self.assertEqual("✅", VersionWorker.rollup_status([self.FakeEntry("✅")], False))


class RemedyLineTest(unittest.TestCase):

    def test_a_stale_fetch_is_disclosed_when_a_divergence_is_claimed(self):
        repo = Repo("some-repo", RepoType.MISC, [])
        report = VersionReport()
        report.add(repo, "/some-repo", "file", "test", STALE, GitSyncState.tracking("develop", 0, 0, 7200.0))
        report.add(repo, "/some-repo", "other", "test", TARGET, GitSyncState.tracking("develop", 0, 0, 7200.0))
        report.summarize()

        lines = report.get_remedy_lines()

        self.assertTrue(any("cedarcli git fetch" in line for line in lines),
                        "an ahead/behind count is only as fresh as the last fetch, and should say so")
        self.assertTrue(any("2h old" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
