import unittest
from unittest import mock

from org.metadatacenter.util.SnapshotFreshness import parse_commit_time
from org.metadatacenter.worker.SnapshotWorker import SnapshotWorker


def commit(sha, date, *files):
    return {"sha": sha, "commit": {"committer": {"date": date}}, "files": [{"filename": f} for f in files]}


class SnapshotWorkerCommitTest(unittest.TestCase):

    def answers(self, by_path):
        return mock.patch.object(SnapshotWorker, "_gh_json", side_effect=lambda path: by_path.get(path))

    def test_a_head_commit_that_changes_the_artifact_is_the_only_one_read(self):
        head = commit("a", "2026-10-15T04:17:57Z", "pom.xml")
        with self.answers({"repos/metadatacenter/r/commits/develop": head}) as gh:
            self.assertEqual((parse_commit_time("2026-10-15T04:17:57Z"), 0), SnapshotWorker._committed_at("r"))
        self.assertEqual(1, gh.call_count)

    def test_commits_that_change_only_ci_are_passed_over(self):
        head = commit("c", "2026-10-15T04:17:57Z", ".github/workflows/ci.yml")
        middle = commit("b", "2026-10-12T00:00:00Z", "README.md")
        source = commit("a", "2026-10-09T05:33:30Z", "pom.xml")
        listing = [{"sha": "c"}, {"sha": "b"}, {"sha": "a"}]
        with self.answers({
            "repos/metadatacenter/r/commits/develop": head,
            "repos/metadatacenter/r/commits?sha=develop&per_page=20": listing,
            "repos/metadatacenter/r/commits/b": middle,
            "repos/metadatacenter/r/commits/a": source,
        }):
            self.assertEqual((parse_commit_time("2026-10-09T05:33:30Z"), 2), SnapshotWorker._committed_at("r"))

    def test_without_a_commit_that_changes_the_artifact_the_head_is_compared(self):
        head = commit("b", "2026-10-15T04:17:57Z", ".github/workflows/ci.yml")
        with self.answers({
            "repos/metadatacenter/r/commits/develop": head,
            "repos/metadatacenter/r/commits?sha=develop&per_page=20": [{"sha": "b"}],
        }):
            self.assertEqual((parse_commit_time("2026-10-15T04:17:57Z"), 0), SnapshotWorker._committed_at("r"))

    def test_an_unreadable_head_is_unreadable(self):
        with self.answers({}):
            self.assertEqual((None, 0), SnapshotWorker._committed_at("r"))


if __name__ == "__main__":
    unittest.main()
