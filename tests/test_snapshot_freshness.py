import unittest
from datetime import datetime, timedelta, timezone

from org.metadatacenter.util.SnapshotFreshness import (
    SnapshotState,
    evaluate,
    parse_commit_time,
    parse_last_updated,
)

METADATA = """<?xml version="1.0" encoding="UTF-8"?>
<metadata modelVersion="1.1.0">
  <groupId>org.metadatacenter</groupId>
  <artifactId>cedar-parent</artifactId>
  <version>2.9.4-SNAPSHOT</version>
  <versioning>
    <snapshot><timestamp>20260831.221625</timestamp><buildNumber>7</buildNumber></snapshot>
    <lastUpdated>20260831221625</lastUpdated>
  </versioning>
</metadata>
"""


def at(text):
    return parse_commit_time(text)


class SnapshotFreshnessTest(unittest.TestCase):

    def test_nexus_records_its_timestamp_as_utc_digits(self):
        self.assertEqual(
            datetime(2026, 8, 31, 22, 16, 25, tzinfo=timezone.utc),
            parse_last_updated(METADATA))

    def test_metadata_without_a_timestamp_reads_as_no_timestamp(self):
        self.assertIsNone(parse_last_updated("<metadata/>"))
        self.assertIsNone(parse_last_updated(""))
        self.assertIsNone(parse_last_updated("<lastUpdated>not-a-date</lastUpdated>"))

    def test_a_commit_time_is_normalised_to_utc(self):
        self.assertEqual(at("2026-08-29T16:09:08Z"), at("2026-08-29T18:09:08+02:00"))
        self.assertIsNone(at(""))
        self.assertIsNone(at("yesterday"))

    def test_a_snapshot_published_after_its_source_is_current(self):
        finding = evaluate("cedar-parent", "2.9.4-SNAPSHOT",
                           at("2026-08-31T22:16:25Z"), at("2026-08-31T21:08:00Z"))
        self.assertEqual(SnapshotState.CURRENT, finding.state)
        self.assertFalse(finding.is_failure)

    def test_the_incident_this_check_exists_for_is_a_failure(self):
        """cedar-parent, 2026-08-29: the commit landed, the deploy met a Nexus 500, and the
        snapshot stayed two days old while every consumer resolved it."""
        finding = evaluate("cedar-parent", "2.9.4-SNAPSHOT",
                           at("2026-08-28T21:55:38Z"), at("2026-08-29T16:09:08Z"))
        self.assertEqual(SnapshotState.BEHIND, finding.state)
        self.assertTrue(finding.is_failure)
        self.assertIn("18 hours", finding.detail)

    def test_a_build_still_running_is_not_a_failure(self):
        """CI has to build and deploy before the snapshot appears. A threshold that fired during
        that window would be wrong far more often than right, and ignored accordingly."""
        finding = evaluate("cedar-core-library", "2.9.4-SNAPSHOT",
                           at("2026-08-31T10:00:00Z"), at("2026-08-31T10:40:00Z"))
        self.assertEqual(SnapshotState.CURRENT, finding.state)

    def test_the_grace_period_is_where_that_line_is_drawn(self):
        published, committed = at("2026-08-31T10:00:00Z"), at("2026-08-31T13:00:00Z")
        self.assertEqual(SnapshotState.CURRENT,
                         evaluate("r", "v", published, committed, timedelta(hours=4)).state)
        self.assertEqual(SnapshotState.BEHIND,
                         evaluate("r", "v", published, committed, timedelta(hours=2)).state)

    def test_a_snapshot_that_was_never_published_is_a_failure(self):
        finding = evaluate("cedar-core-library", "2.9.4-SNAPSHOT", None, at("2026-08-29T16:09:08Z"))
        self.assertEqual(SnapshotState.ABSENT, finding.state)
        self.assertTrue(finding.is_failure)
        self.assertIn("2.9.4-SNAPSHOT", finding.detail)

    def test_an_unreadable_source_does_not_fail_the_estate(self):
        """The check answers a question about CEDAR's artifacts. When it cannot read the source at
        all it has no answer, and reporting one would point at the estate for a fault in the
        network reaching GitHub."""
        finding = evaluate("cedar-core-library", "2.9.4-SNAPSHOT", at("2026-08-31T10:00:00Z"), None)
        self.assertEqual(SnapshotState.UNREADABLE, finding.state)
        self.assertFalse(finding.is_failure)

    def test_a_gap_is_described_in_a_unit_that_says_something(self):
        base = at("2026-08-01T00:00:00Z")
        self.assertIn("30 minutes", evaluate("r", "v", base, base + timedelta(minutes=30),
                                             timedelta(0)).detail)
        self.assertIn("5 hours", evaluate("r", "v", base, base + timedelta(hours=5),
                                          timedelta(0)).detail)
        self.assertIn("4 days", evaluate("r", "v", base, base + timedelta(days=4),
                                         timedelta(0)).detail)


if __name__ == "__main__":
    unittest.main()
