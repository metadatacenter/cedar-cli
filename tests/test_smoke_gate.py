import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from org.metadatacenter import smoke_gate
from org.metadatacenter.smoke_gate import (
    SCHEMA_VERSION,
    SmokeGateError,
    develop_heads,
    evaluate,
    findings_for,
    read_report,
    run_smoke,
    sources_digest,
    stack_findings,
)


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Answer commands by prefix and keep every call, so a test can read how each was made."""

    def __init__(self, answers=None, default=None):
        self.answers = list(answers or [])
        self.default = default or FakeResult()
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        for prefix, answer in self.answers:
            if list(args)[:len(prefix)] == list(prefix):
                return answer(list(args), kwargs) if callable(answer) else answer
        return self.default

    def commands(self, *prefix):
        return [args for args, _ in self.calls if args[:len(prefix)] == list(prefix)]


HEADS = {"cedar-a": "a" * 40, "cedar-b": "b" * 40}

HEALTHY_TSV = (
    "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors\n"
    "resource\t101\t9007\tup\thealthy\tcurrent\t0\n"
    "terminology\t102\t9004\tup\thealthy\tcurrent\t0\n"
    "ui-main\t201\t4200\tup\thealthy\tcurrent\t0\n"
    "ui-designer\t202\t4202\tup\thealthy\t-\t0\n"
)


def fake_home(directory, repositories=HEADS):
    home = Path(directory)
    ops = home / "cedar-development" / "ops"
    (ops / "e2e").mkdir(parents=True)
    (ops / "e2e" / "package.json").write_text("{}", encoding="utf-8")
    (ops / "cedar-services.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (ops / "build-train.json").write_text(json.dumps({
        "organization": "metadatacenter",
        "sourceBranch": "develop",
        "repositories": list(repositories),
    }), encoding="utf-8")
    for repository in repositories:
        (home / repository / ".git").mkdir(parents=True)
    return home


def rev_parse(heads):
    def answer(_args, kwargs):
        repository = Path(kwargs["cwd"]).name
        if repository in heads:
            return FakeResult(stdout=heads[repository])
        return FakeResult(returncode=128, stderr="fatal: ambiguous argument 'refs/heads/develop'")
    return answer


def rest_runner(verdict="PASS", inventory_matched=True, exit_code=0):
    def answer(args, _kwargs):
        report = next(a for a in args if a.startswith("--report=")).split("=", 1)[1]
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text(json.dumps({
            "schemaVersion": 1, "runId": "2026-09-07T10-00-00", "verdict": verdict,
            "counts": {"passed": 803, "failed": 0, "skipped": 7, "total": 810},
            "inventoryMatched": inventory_matched,
        }), encoding="utf-8")
        return FakeResult(exit_code)
    return answer


def runner_for(home, *, tsv=HEALTHY_TSV, heads=HEADS, dirty=(), rest=None, browser=FakeResult(0)):
    controller = str(home / "cedar-development" / "ops" / "cedar-services.sh")

    def status(_args, kwargs):
        repository = Path(kwargs["cwd"]).name
        return FakeResult(stdout=" M pom.xml" if repository in dirty else "")

    return FakeRunner([
        ((controller, "status-tsv"), FakeResult(stdout=tsv)),
        (("git", "rev-parse"), rev_parse(heads)),
        (("git", "status"), status),
        (("npm", "run", "smoke:rest"), rest or rest_runner()),
        (("npm", "run", "smoke"), browser),
    ])


def clock():
    moment = dt.datetime(2026, 9, 7, 10, 0, tzinfo=dt.timezone.utc)

    def tick():
        nonlocal moment
        moment += dt.timedelta(seconds=30)
        return moment
    return tick


def report_for(heads=HEADS, **overrides):
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "verdict": "PASS",
        "finishedAt": "2026-09-07T10:02:30+00:00",
        "sources": dict(heads),
        "dirty": [],
        "tiers": {
            "rest": {"verdict": "PASS", "inventoryMatched": True,
                     "finishedAt": "2026-09-07T10:01:30+00:00"},
            "browser": {"verdict": "PASS", "finishedAt": "2026-09-07T10:02:30+00:00"},
        },
    }
    report.update(overrides)
    return report


class SourcesDigestTest(unittest.TestCase):
    def test_digest_ignores_order_and_follows_every_head(self):
        forward = sources_digest({"cedar-a": "a" * 40, "cedar-b": "b" * 40})
        reversed_ = sources_digest({"cedar-b": "b" * 40, "cedar-a": "a" * 40})
        moved = sources_digest({"cedar-a": "a" * 40, "cedar-b": "c" * 40})
        self.assertEqual(forward, reversed_)
        self.assertNotEqual(forward, moved)
        self.assertEqual(16, len(forward))


class RunSmokeTest(unittest.TestCase):
    """One command runs both tiers and records what they ran against."""

    def setUp(self):
        which = patch("org.metadatacenter.smoke_gate.shutil.which", return_value="/usr/bin/npm")
        which.start()
        self.addCleanup(which.stop)

    def test_a_passing_run_is_recorded_under_the_digest_of_its_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(0, code)
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            exact = reports / f"{sources_digest(HEADS)}.json"
            self.assertTrue(exact.exists())
            report = json.loads(exact.read_text(encoding="utf-8"))
            self.assertEqual(report, json.loads((reports / "latest.json").read_text(encoding="utf-8")))
            self.assertEqual("PASS", report["verdict"])
            self.assertEqual(HEADS, report["sources"])
            self.assertEqual([], report["dirty"])
            self.assertEqual("PASS", report["tiers"]["rest"]["verdict"])
            self.assertTrue(report["tiers"]["rest"]["inventoryMatched"])
            self.assertEqual(810, report["tiers"]["rest"]["counts"]["total"])
            self.assertEqual("PASS", report["tiers"]["browser"]["verdict"])
            self.assertEqual(150.0, report["durationSeconds"])

    def test_both_tiers_run_from_the_e2e_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)

            run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            e2e = str(home / "cedar-development" / "ops" / "e2e")
            npm_calls = [(args, kwargs) for args, kwargs in runner.calls if args[0] == "npm"]
            self.assertEqual(2, len(npm_calls))
            self.assertTrue(all(kwargs["cwd"] == e2e for _, kwargs in npm_calls))
            rest, browser = (args for args, _ in npm_calls)
            self.assertEqual(["npm", "run", "smoke:rest", "--"], rest[:4])
            self.assertTrue(rest[4].startswith("--report="))
            self.assertEqual(["npm", "run", "smoke"], browser)

    def test_an_unhealthy_or_stale_stack_refuses_before_anything_runs(self):
        tsv = (
            "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors\n"
            "resource\t101\t9007\tup\thealthy\tSTALE\t0\n"
            "terminology\t102\t9004\tup\tstarting\tcurrent\t0\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, tsv=tsv)

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            self.assertEqual([], runner.commands("npm"))
            self.assertFalse((home / "cedar-development" / "ops" / "e2e" / "reports").exists())

    def test_a_failing_tier_still_lets_the_other_run_and_records_a_failed_run(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, browser=FakeResult(1))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            self.assertEqual(2, len(runner.commands("npm")))
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            report = json.loads((reports / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual("FAIL", report["verdict"])
            self.assertEqual("PASS", report["tiers"]["rest"]["verdict"])
            self.assertEqual("FAIL", report["tiers"]["browser"]["verdict"])
            self.assertEqual(1, report["tiers"]["browser"]["exitCode"])

    def test_a_rest_inventory_mismatch_fails_the_tier_even_at_exit_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, rest=rest_runner(inventory_matched=False))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            report = json.loads((reports / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual("FAIL", report["tiers"]["rest"]["verdict"])
            self.assertFalse(report["tiers"]["rest"]["inventoryMatched"])

    def test_uncommitted_work_is_recorded_beside_the_heads(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, dirty=("cedar-b",))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(0, code)
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            report = json.loads((reports / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(["cedar-b"], report["dirty"])
            self.assertEqual(
                ["cedar-b had uncommitted changes when the smoke ran"],
                evaluate(report, HEADS))

    def test_without_an_e2e_checkout_there_is_nothing_to_run(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            (home / "cedar-development" / "ops" / "e2e" / "package.json").unlink()
            runner = runner_for(home)

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            self.assertEqual([], runner.calls)


class StackFindingsTest(unittest.TestCase):
    def test_a_healthy_current_stack_has_no_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            self.assertEqual([], stack_findings(home, runner_for(home)))

    def test_a_foreign_listener_is_named(self):
        tsv = (
            "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors\n"
            "resource\t!4242\t9007\tup\thealthy\t-\t0\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            findings = stack_findings(home, runner_for(home, tsv=tsv))
        self.assertEqual(1, len(findings))
        self.assertIn("resource", findings[0])
        self.assertIn("does not manage", findings[0])

    def test_an_unreadable_controller_is_one_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = FakeRunner(default=FakeResult(1, stderr="bash: no such file"))
            findings = stack_findings(home, runner)
        self.assertEqual(["cannot read native stack status: bash: no such file"], findings)


class DevelopHeadsTest(unittest.TestCase):
    def test_absent_repositories_are_skipped_and_missing_branches_are_problems(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory, {"cedar-a": "a" * 40, "cedar-c": "c" * 40})
            runner = runner_for(home, heads={"cedar-a": "a" * 40})

            heads, dirty, problems = develop_heads(
                home, ["cedar-a", "cedar-absent", "cedar-c"], runner)

        self.assertEqual({"cedar-a": "a" * 40}, heads)
        self.assertEqual([], dirty)
        self.assertEqual(1, len(problems))
        self.assertIn("cedar-c has no local develop branch", problems[0])


class EvaluateTest(unittest.TestCase):
    """The gate is a question about commits, answered from the record alone."""

    def test_a_passing_run_against_the_same_heads_has_no_findings(self):
        self.assertEqual([], evaluate(report_for(), HEADS))

    def test_a_moved_head_names_both_commits(self):
        findings = evaluate(report_for(), {"cedar-a": "a" * 40, "cedar-b": "d" * 40})
        self.assertEqual(
            ["cedar-b: the smoke run at 2026-09-07T10:02:30+00:00 tested bbbbbbbb, "
             "this source is dddddddd"],
            findings)

    def test_many_moved_heads_are_summarised(self):
        heads = {f"cedar-{i}": f"{i}" * 40 for i in range(8)}
        report = report_for(heads={name: "f" * 40 for name in heads})
        findings = evaluate(report, heads)
        self.assertEqual(1, len(findings))
        self.assertIn("other commits of 8 repositories", findings[0])
        self.assertIn("and 3 more", findings[0])

    def test_an_unrecorded_repository_is_a_finding(self):
        findings = evaluate(report_for(), {**HEADS, "cedar-c": "c" * 40})
        self.assertEqual(
            ["the smoke run at 2026-09-07T10:02:30+00:00 recorded no source for cedar-c"],
            findings)

    def test_uncommitted_work_in_a_relevant_repository_is_a_finding(self):
        report = report_for(dirty=["cedar-a", "cedar-unrelated"])
        self.assertEqual(
            ["cedar-a had uncommitted changes when the smoke ran"], evaluate(report, HEADS))

    def test_a_failed_or_missing_tier_is_a_finding(self):
        report = report_for()
        report["tiers"]["browser"]["verdict"] = "FAIL"
        del report["tiers"]["rest"]
        findings = evaluate(report, HEADS)
        self.assertEqual(2, len(findings))
        self.assertIn("recorded no rest tier", findings[0])
        self.assertIn("the browser smoke was FAIL at 2026-09-07T10:02:30+00:00", findings[1])

    def test_a_passing_rest_tier_with_another_inventory_is_a_finding(self):
        report = report_for()
        report["tiers"]["rest"]["inventoryMatched"] = False
        self.assertEqual(
            ["the REST smoke ran a different check inventory than the committed one"],
            evaluate(report, HEADS))

    def test_an_unknown_schema_is_one_finding(self):
        findings = evaluate(report_for(schemaVersion=2), HEADS)
        self.assertEqual(1, len(findings))
        self.assertIn("schema 2", findings[0])


class ReadReportTest(unittest.TestCase):
    def _write(self, home, report):
        reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
        reports.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report)
        (reports / f"{sources_digest(report['sources'])}.json").write_text(payload, encoding="utf-8")
        (reports / "latest.json").write_text(payload, encoding="utf-8")

    def test_nothing_recorded_names_the_command_to_run(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SmokeGateError) as caught:
                read_report(fake_home(directory), HEADS)
        self.assertIn("cedarcli test e2e", str(caught.exception))

    def test_the_run_for_these_heads_wins_over_a_later_run_for_others(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            self._write(home, report_for())
            later = report_for(heads={"cedar-a": "a" * 40, "cedar-b": "e" * 40},
                               finishedAt="2026-09-08T09:00:00+00:00")
            self._write(home, later)

            report = read_report(home, HEADS)

        self.assertEqual(HEADS, report["sources"])
        self.assertEqual([], evaluate(report, HEADS))

    def test_without_an_exact_run_the_latest_answers_and_says_what_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            self._write(home, report_for())

            findings = findings_for(home, {"cedar-a": "a" * 40, "cedar-b": "e" * 40})

        self.assertEqual(1, len(findings))
        self.assertIn("cedar-b: the smoke run at", findings[0])

    def test_an_unreadable_record_is_one_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            reports.mkdir(parents=True)
            (reports / "latest.json").write_text("{not json", encoding="utf-8")

            findings = findings_for(home, HEADS)

        self.assertEqual(1, len(findings))
        self.assertIn("cannot be read", findings[0])

    def test_no_cedar_home_is_one_finding(self):
        self.assertEqual(["CEDAR_HOME is not set"], findings_for(None, HEADS))


if __name__ == "__main__":
    unittest.main()


class SourceCurrencyTest(unittest.TestCase):
    """A jar older than the commit the record names cannot contain it."""

    def _service(self, home, service, *, jar_epoch, head_epoch):
        root = home / f"cedar-{service}-server"
        (root / ".git").mkdir(parents=True, exist_ok=True)
        target = root / f"cedar-{service}-server-application" / "target"
        target.mkdir(parents=True, exist_ok=True)
        jar = target / f"cedar-{service}-server-application-2.9.10-SNAPSHOT.jar"
        jar.write_bytes(b"jar")
        os.utime(jar, (jar_epoch, jar_epoch))
        return FakeRunner(answers=[
            (["git", "log"], FakeResult(stdout=f"{head_epoch} {'a' * 40}\n")),
        ])

    def test_a_jar_written_before_its_head_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            runner = self._service(home, "resource", jar_epoch=1_000, head_epoch=2_000)

            findings = smoke_gate.source_currency_findings(home, ["resource"], runner=runner)

        self.assertEqual(1, len(findings))
        self.assertIn("resource was built before its source", findings[0])
        self.assertIn("cedar-resource-server develop aaaaaaaa", findings[0])

    def test_a_jar_written_after_its_head_is_current(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            runner = self._service(home, "resource", jar_epoch=3_000, head_epoch=2_000)

            self.assertEqual(
                [], smoke_gate.source_currency_findings(home, ["resource"], runner=runner))

    def test_a_service_with_no_jar_is_not_an_accusation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "cedar-resource-server" / ".git").mkdir(parents=True)

            self.assertEqual(
                [], smoke_gate.source_currency_findings(home, ["resource"], runner=FakeRunner()))

    def test_the_original_jar_maven_leaves_behind_is_not_the_deployed_one(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            runner = self._service(home, "resource", jar_epoch=3_000, head_epoch=2_000)
            target = home / "cedar-resource-server" / "cedar-resource-server-application" / "target"
            original = target / "original-cedar-resource-server-application-2.9.10-SNAPSHOT.jar"
            original.write_bytes(b"shaded input")
            os.utime(original, (1_000, 1_000))

            self.assertEqual(
                [], smoke_gate.source_currency_findings(home, ["resource"], runner=runner))

    def test_a_stale_jar_stops_a_smoke_run_before_it_records_anything(self):
        tsv = (
            "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors\n"
            "resource\t101\t9007\tup\thealthy\tcurrent\t0\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            root = home / "cedar-resource-server"
            (root / ".git").mkdir(parents=True, exist_ok=True)
            target = root / "cedar-resource-server-application" / "target"
            target.mkdir(parents=True, exist_ok=True)
            jar = target / "cedar-resource-server-application-2.9.10-SNAPSHOT.jar"
            jar.write_bytes(b"jar")
            os.utime(jar, (1_000, 1_000))
            runner = runner_for(home, tsv=tsv)
            runner.answers.insert(0, (["git", "log"], FakeResult(stdout=f"2000 {'b' * 40}\n")))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            self.assertEqual([], runner.commands("npm"))

