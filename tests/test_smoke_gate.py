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
REST_CHECKS = ["runner\x1fpreflight\x1fclean", "folders\x1ffolders\x1fcreated",
               "freeze\x1ffreeze\x1fpin", "runner\x1fpostflight\x1fclean"]

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
    (ops / "e2e" / "rest").mkdir()
    (ops / "e2e" / "rest" / "expected-checks.json").write_text(
        json.dumps({"version": 1, "checks": REST_CHECKS}), encoding="utf-8")
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


def rest_payload():
    return {
        "schemaVersion": 1, "runId": "2026-09-07T10-00-00", "verdict": "PASS", "interrupted": False,
        "startedAt": "2026-09-07T10:00:00Z", "finishedAt": "2026-09-07T10:01:00Z",
        "selectedSuites": ["folders", "freeze"], "inventoryMatched": True,
        "counts": {"passed": 3, "failed": 0, "skipped": 1, "total": 4},
        "checks": [dict(zip(("suite", "section", "name"), key.split("\x1f")),
                        status="skipped" if key.startswith("freeze\x1f") else "passed") for key in REST_CHECKS],
    }


def rest_runner(verdict="PASS", inventory_matched=True, exit_code=0, payload=None):
    def answer(args, _kwargs):
        report = next(a for a in args if a.startswith("--report=")).split("=", 1)[1]
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        evidence = rest_payload() if payload is None else payload
        if payload is None:
            evidence.update(verdict=verdict, inventoryMatched=inventory_matched)
        Path(report).write_text(json.dumps(evidence), encoding="utf-8")
        return FakeResult(exit_code)
    return answer


def split_runner(verdict="PASS", exit_code=0, failing_script=None, failing_step=None):
    def answer(args, _kwargs):
        report = next(a for a in args if a.startswith("--report=")).split("=", 1)[1]
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text(json.dumps({
            "schemaVersion": 1, "verdict": verdict,
            "failingScript": failing_script, "failingStep": failing_step,
            "failure": "locator.click: Timeout 25000ms exceeded." if failing_step else None,
            "scripts": [{"name": "modern-workspace", "command": ["node", "modern-workspace-smoke.mjs"],
                         "exitCode": exit_code, "seconds": 371.4}],
            "notRun": ["ced-host"] if exit_code else [],
            "machine": {"start": {"load": [3.1, 2.9, 2.5], "swap": None},
                        "end": {"load": [4.0, 3.2, 2.6], "swap": None}},
            "steps": [{"script": "modern-workspace", "step": name, "seconds": seconds}
                      for name, seconds in (("login", 1.2), ("metadata", 41.0), ("sharing", 30.5),
                                            ("ownership", 12.0), ("save-status", 15.1), ("delete", 9.9))],
        }), encoding="utf-8")
        return FakeResult(exit_code)
    return answer


def lane_runner(exit_code=1):
    """A lane run of the split tier in which two lanes fail and the others pass."""
    def answer(args, _kwargs):
        report = next(a for a in args if a.startswith("--report=")).split("=", 1)[1]
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        lane = {"root": None, "plannedSeconds": None, "startedAfterSeconds": 0.0, "failure": None, "notRun": []}
        Path(report).write_text(json.dumps({
            "schemaVersion": 2, "verdict": "FAIL", "sequential": False, "generalLanes": 2,
            "durationSeconds": 212.4, "checks": 190,
            "failingScript": "general-2", "failingStep": "uncertain-create",
            "failure": "locator.click: Timeout 25000ms exceeded.",
            "failures": [
                {"lane": "general-2", "script": "general-2", "step": "uncertain-create", "exitCode": 1,
                 "message": "locator.click: Timeout 25000ms exceeded.", "finishedAt": "2026-09-07T10:03:00Z"},
                {"lane": "identity", "script": "account-groups", "step": None, "exitCode": 1,
                 "message": None, "finishedAt": "2026-09-07T10:03:10Z"},
            ],
            "lanes": [
                {**lane, "name": "core", "keys": [], "scripts": ["modern-workspace"], "seconds": 61.0,
                 "exitCode": 0, "failingScript": None, "failingStep": None},
                {**lane, "name": "identity", "keys": ["account-switch"], "scripts": ["account-groups", "folder-delete"],
                 "seconds": 120.5, "exitCode": 1, "failingScript": "account-groups", "failingStep": None,
                 "notRun": ["folder-delete"]},
                {**lane, "name": "general-1", "keys": ["save-status"], "scripts": ["ced-host"], "seconds": 205.1,
                 "exitCode": 0, "failingScript": None, "failingStep": None},
                {**lane, "name": "general-2", "keys": ["uncertain-create", "duplication"], "scripts": [],
                 "seconds": 150.2, "exitCode": 1, "failingScript": "general-2", "failingStep": "uncertain-create"},
            ],
            "scripts": [{"name": "modern-workspace", "lane": "core", "command": ["node", "modern-workspace-smoke.mjs", "--chain-only"],
                         "exitCode": 0, "seconds": 61.0}],
            "notRun": ["folder-delete"],
            "machine": {"start": {"load": [9.1, 8.0, 7.5], "swap": None}, "peak": {"load": 14.2, "swapUsedMB": None},
                        "end": {"load": [12.0, 9.2, 8.1], "swap": None}},
            "steps": [{"script": "general-2", "step": "uncertain-create", "seconds": 30.2}],
        }), encoding="utf-8")
        return FakeResult(exit_code)
    return answer


def queue_runner(exit_code=124):
    """A queued run of the split tier, schema 3, in which one journey overran its deadline."""
    def answer(args, _kwargs):
        report = next(a for a in args if a.startswith("--report=")).split("=", 1)[1]
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        timeout = {"lane": "general-1", "unit": "save-status", "process": "general-1",
                   "elapsedSeconds": 86.4, "deadlineSeconds": 86}
        sweep = {"lane": "general-1", "process": "general-1", "unit": "save-status", "recorded": 3,
                 "root": {"id": "https://repo/folders/r", "name": "Focused journey 1 general-1", "how": "recursive",
                          "deleted": {"folder": 2, "template": 1}},
                 "removed": [], "alreadyGone": [{"collection": "templates", "id": "t"}],
                 "failed": [{"collection": "folders", "id": "f", "status": 400, "text": "nonEmptyFolder"}],
                 "secondUser": [],
                 "restored": [{"restore": "api-key", "key": "k1", "label": "API key \"smoke 1\"", "as": "user1",
                               "changed": 1}],
                 "alreadyRestored": [],
                 "notRestored": [{"restore": "api-key", "key": "k2", "label": "API key \"smoke 2\"", "as": "user1",
                                  "status": 503, "text": "unavailable"}],
                 "seconds": 0.9}
        lane = {"root": None, "plannedSeconds": 110.0, "startedAfterSeconds": 0.0, "failure": None, "notRun": [],
                "overheadSeconds": 2.0, "timeout": None, "sweep": None, "sweeps": [], "worker": None}
        Path(report).write_text(json.dumps({
            "schemaVersion": 3, "verdict": "FAIL", "sequential": False, "generalLanes": 2,
            "durationSeconds": 131.2, "checks": 180,
            "failingScript": "general-1", "failingStep": "save-status",
            "failure": "save-status timed out on general-1 after 86.4 s, past its deadline of 86 s",
            "failures": [{"lane": "general-1", "script": "general-1", "step": "save-status", "exitCode": 124,
                          "message": "save-status timed out on general-1 after 86.4 s, past its deadline of 86 s",
                          "finishedAt": "2026-10-17T10:03:00Z", "unit": "save-status", "timeout": timeout}],
            "lanes": [
                {**lane, "name": "core", "keys": ["duplication"], "scripts": ["modern-workspace-chain"], "seconds": 128.0,
                 "exitCode": 0, "failingScript": None, "failingStep": None, "busySeconds": 120.5, "idleSeconds": 8.7,
                 "units": [{"name": "modern-workspace-chain", "kind": "chain", "seconds": 45.0, "outcome": "pass"},
                           {"name": "duplication", "kind": "journey", "seconds": 13.9, "outcome": "pass"}]},
                {**lane, "name": "general-1", "keys": ["save-status"], "scripts": [], "seconds": 90.0,
                 "exitCode": 124, "failingScript": "general-1", "failingStep": "save-status", "busySeconds": 86.4,
                 "idleSeconds": 42.8, "timeout": timeout, "sweep": sweep, "sweeps": [sweep],
                 "units": [{"name": "save-status", "kind": "journey", "seconds": 86.4, "outcome": "timeout"}]},
            ],
            "scripts": [{"name": "general-1", "lane": "general-1", "command": ["node", "focused-journey-smoke.mjs"],
                         "exitCode": 1, "signal": "SIGKILL", "seconds": 90.0, "timedOut": True}],
            "notRun": [],
            "machine": {"start": {"load": [6.1, 6.0, 6.5], "swap": None}, "peak": {"load": 13.0, "swapUsedMB": None},
                        "end": {"load": [10.0, 8.2, 7.1], "swap": None}},
            "steps": [{"script": "general-1", "step": "save-status", "seconds": 86.4}],
            "lock": {"path": "/home/.cedar/locks/split-tier.lock",
                     "holder": {"pid": 4242, "startedAt": "2026-10-17T10:00:00Z", "command": "node split-tier.mjs",
                                "cwd": "/e2e", "host": "workstation"},
                     "replaced": {"pid": 17, "startedAt": "2026-10-16T09:00:00Z", "command": "node split-tier.mjs"}},
            "unitTimeout": {"factor": 3, "marginSeconds": 30, "floorSeconds": 60, "capSeconds": 1200,
                            "workerLimitSeconds": 120},
            "units": [], "timeouts": [timeout], "sweeps": [sweep],
            "evidenceDirectory": "/tmp/cedar-split-tier-x/evidence",
        }), encoding="utf-8")
        return FakeResult(exit_code)
    return answer


def search_payload():
    families = ("content", "access", "paging", "protocol", "lifecycle", "journey")
    return {"tier": "all", "status": "IMPLEMENTED_CASES_PASS_WITH_DECLARED_GAPS",
            "finishedAt": "2026-10-17T10:03:00Z", "runToken": "unique", "harnessDigest": "h", "corpusDigest": "c",
            "cleanupErrors": [], "browserErrors": [],
            "matrixCells": dict.fromkeys(families, 1),
            "cells": {family: ["lifecycle/cell"] if family == "lifecycle" else
                      [{"id": "cell" if family == "journey" else f"{family}/cell"}] for family in families},
            "checks": [{"id": f"{family}/cell", "status": "pass"} for family in families],
            "completion": {"complete": True, "missing": [], "unexpected": [], "failed": []}}


def search_runner(payload=None, exit_code=0):
    def answer(args, _kwargs):
        path = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--report=")))
        path.write_text(json.dumps(search_payload() if payload is None else payload), encoding="utf-8")
        return FakeResult(exit_code)
    return answer


def runner_for(home, *, tsv=HEALTHY_TSV, heads=HEADS, dirty=(), rest=None, browser=FakeResult(0),
               split=None, search=None):
    controller = str(home / "cedar-development" / "ops" / "cedar-services.sh")

    def status(_args, kwargs):
        repository = Path(kwargs["cwd"]).name
        return FakeResult(stdout=" M pom.xml" if repository in dirty else "")

    return FakeRunner([
        ((controller, "status-tsv"), FakeResult(stdout=tsv)),
        (("git", "rev-parse"), rev_parse(heads)),
        (("git", "status"), status),
        (("npm", "run", "smoke:rest"), rest or rest_runner()),
        (("npm", "run", "smoke:workspace:modern:full"), split or split_runner()),
        (("npm", "run", "smoke"), browser),
        (("npm", "run", "smoke:search"), search or search_runner()),
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
            "rest": {"verdict": "PASS", "inventoryMatched": True, "evidenceValid": True,
                     "finishedAt": "2026-09-07T10:01:30+00:00"},
            "browser": {"verdict": "PASS", "finishedAt": "2026-09-07T10:02:30+00:00"},
            "split": {"verdict": "PASS", "finishedAt": "2026-09-07T10:03:30+00:00"},
            "search": {"verdict": "PASS", "evidenceValid": True},
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
    """One command runs every tier and records what they ran against."""

    def setUp(self):
        which = patch("org.metadatacenter.smoke_gate.shutil.which", return_value="/usr/bin/npm")
        which.start()
        self.addCleanup(which.stop)

    def test_rest_evidence_fails_closed_even_when_the_process_exits_zero(self):
        mutations = {
            "failed verdict": lambda r: r.update(verdict="FAIL"),
            "interrupted": lambda r: r.update(interrupted=True),
            "unfinished": lambda r: r.pop("finishedAt"),
            "wrong schema": lambda r: r.update(schemaVersion=2),
            "no run id": lambda r: r.pop("runId"),
            "partial suite": lambda r: r.update(selectedSuites=["folders"]),
            "unverified inventory": lambda r: r.pop("inventoryMatched"),
            "missing check": lambda r: r["checks"].pop(),
            "duplicate check": lambda r: r["checks"].__setitem__(0, r["checks"][1]),
            "unexpected check": lambda r: r["checks"][0].update(name="unplanned"),
            "reordered checks": lambda r: r["checks"].reverse(),
            "failed check": lambda r: r["checks"][0].update(status="failed"),
            "unknown check status": lambda r: r["checks"][0].update(status="pending"),
            "wrong counts": lambda r: r["counts"].update(total=800),
            "string count": lambda r: r["counts"].update(passed="3"),
            "cleanup failure": lambda r: r["counts"].update(failed=1, total=5),
            "malformed checks": lambda r: r.update(checks=None),
            "backwards timestamps": lambda r: r.update(finishedAt="2026-09-06T10:00:00Z"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                home = fake_home(directory)
                payload = rest_payload()
                mutate(payload)
                runner = runner_for(home, rest=rest_runner(payload=payload))
                self.assertEqual(1, run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"}))
                report = read_report(home, HEADS)
                self.assertEqual("FAIL", report["tiers"]["rest"]["verdict"])
                self.assertFalse(report["tiers"]["rest"]["evidenceValid"])
                self.assertTrue(evaluate(report, HEADS))
                self.assertEqual(4, len(runner.commands("npm", "run")))

    def test_rest_report_cannot_invent_its_own_check_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            inventory = home / smoke_gate.E2E / "rest" / "expected-checks.json"
            inventory.write_text(json.dumps({"version": 1, "checks": REST_CHECKS + ["new\x1fnew\x1fnew"]}))
            self.assertEqual(1, run_smoke(home, runner=runner_for(home), clock=clock(), environment={"PATH": "/usr/bin"}))

    def test_rest_rerun_cannot_reuse_an_older_passing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            self.assertEqual(0, run_smoke(home, runner=runner_for(home), clock=clock(), environment={"PATH": "/usr/bin"}))
            previous = read_report(home, HEADS)["tiers"]["rest"]["report"]
            for code in (0, 75):
                self.assertEqual(1, run_smoke(home, runner=runner_for(home, rest=FakeResult(code)),
                                             clock=clock(), environment={"PATH": "/usr/bin"}))
                rest = read_report(home, HEADS)["tiers"]["rest"]
                self.assertNotEqual(previous, rest["report"])
                self.assertFalse(rest["evidenceValid"])
                self.assertIn("reportError", rest)
            self.assertTrue(Path(previous).exists())

    def test_malformed_rest_reports_fail_without_preventing_the_other_tiers(self):
        for content in ("{", "null", "[]", "{}"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                home = fake_home(directory)
                def answer(args, _kwargs):
                    path = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--report=")))
                    path.write_text(content)
                    return FakeResult(0)
                runner = runner_for(home, rest=answer)
                self.assertEqual(1, run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"}))
                self.assertEqual(4, len(runner.commands("npm", "run")))

    def test_search_evidence_fails_closed_even_when_the_process_exits_zero(self):
        mutations = {
            "rest only": lambda r: r.update(tier="rest"),
            "unfinished": lambda r: r.pop("finishedAt"),
            "running": lambda r: r.update(status="RUNNING"),
            "cleanup failure": lambda r: r.update(cleanupErrors=[{"message": "failed delete"}]),
            "browser error": lambda r: r.update(browserErrors=["page error"]),
            "fatal": lambda r: r.update(fatal={"message": "timeout"}),
            "missing cell": lambda r: r["checks"].pop(),
            "duplicate cell": lambda r: r["checks"].__setitem__(0, r["checks"][1]),
            "unexpected cell": lambda r: r["checks"][0].update(id="unplanned"),
            "failed cell": lambda r: r["checks"][0].update(status="fail"),
            "skipped cell": lambda r: r["checks"][0].update(status="skip"),
            "incomplete": lambda r: r["completion"].update(complete=False),
            "missing completion": lambda r: r.pop("completion"),
            "no journeys": lambda r: r["cells"].pop("journey"),
            "no protocol inventory": lambda r: r["cells"].pop("protocol"),
            "missing protocol check": lambda r: r.update(checks=[c for c in r["checks"]
                                                                 if not c["id"].startswith("protocol/")]),
            "wrong inventory count": lambda r: r["matrixCells"].update(content=2),
            "empty family": lambda r: (r["cells"].update(content=[]), r["matrixCells"].update(content=0)),
            "duplicate inventory": lambda r: r["cells"]["content"][0].update(id="access/cell"),
            "malformed": lambda r: r.update(checks=None),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                home = fake_home(directory)
                payload = search_payload()
                mutate(payload)
                runner = runner_for(home, search=search_runner(payload))
                self.assertEqual(1, run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"}))
                report = read_report(home, HEADS)
                self.assertEqual("FAIL", report["tiers"]["search"]["verdict"])
                self.assertFalse(report["tiers"]["search"]["evidenceValid"])
                self.assertEqual(4, len(runner.commands("npm", "run")))

    def test_search_rerun_cannot_reuse_an_older_passing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            self.assertEqual(0, run_smoke(home, runner=runner_for(home), clock=clock(), environment={"PATH": "/usr/bin"}))
            previous = read_report(home, HEADS)["tiers"]["search"]["report"]
            for code in (0, 75):
                self.assertEqual(1, run_smoke(home, runner=runner_for(home, search=FakeResult(code)),
                                             clock=clock(), environment={"PATH": "/usr/bin"}))
                search = read_report(home, HEADS)["tiers"]["search"]
                self.assertNotEqual(previous, search["report"])
                self.assertFalse(search["evidenceValid"])
                self.assertIn("reportError", search)
            self.assertTrue(Path(previous).exists())

    def test_old_three_tier_runs_and_unverified_search_verdicts_do_not_certify_a_train(self):
        report = report_for()
        del report["tiers"]["search"]
        self.assertEqual(1, len(evaluate(report, HEADS)))
        self.assertIn("no search tier", evaluate(report, HEADS)[0])
        report["tiers"]["search"] = {"verdict": "PASS"}
        self.assertIn("no complete matrix", evaluate(report, HEADS)[0])

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
            self.assertEqual(len(REST_CHECKS), report["tiers"]["rest"]["counts"]["total"])
            self.assertEqual("PASS", report["tiers"]["browser"]["verdict"])
            self.assertEqual("PASS", report["tiers"]["split"]["verdict"])
            self.assertEqual(270.0, report["durationSeconds"])
            self.assertTrue(report["tiers"]["search"]["evidenceValid"])

    def test_the_split_record_names_the_failing_script_and_step(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, split=split_runner(
                "FAIL", exit_code=1, failing_script="modern-workspace", failing_step="metadata"))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            split = json.loads((reports / "latest.json").read_text(encoding="utf-8"))["tiers"]["split"]
            self.assertEqual("FAIL", split["verdict"])
            self.assertEqual("modern-workspace", split["failingScript"])
            self.assertEqual("metadata", split["failingStep"])
            self.assertIn("Timeout", split["failure"])
            self.assertEqual(["ced-host"], split["notRun"])
            self.assertEqual([3.1, 2.9, 2.5], split["machine"]["start"]["load"])
            self.assertEqual(["metadata", "sharing", "save-status", "ownership", "delete"],
                             [step["step"] for step in split["slowestSteps"]])

    def test_a_split_run_without_a_report_keeps_its_exit_code_as_the_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, split=FakeResult(0))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(0, code)
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            split = json.loads((reports / "latest.json").read_text(encoding="utf-8"))["tiers"]["split"]
            self.assertEqual("PASS", split["verdict"])
            self.assertIn("reportError", split)

    def test_the_split_record_names_every_failing_lane_and_its_step(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, split=lane_runner())

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            split = json.loads((reports / "latest.json").read_text(encoding="utf-8"))["tiers"]["split"]
            self.assertEqual("FAIL", split["verdict"])
            self.assertEqual(2, split["schemaVersion"])
            self.assertEqual(("general-2", "uncertain-create"), (split["failingScript"], split["failingStep"]))
            self.assertEqual([("general-2", "general-2", "uncertain-create"), ("identity", "account-groups", None)],
                             [(f["lane"], f["script"], f["step"]) for f in split["failures"]])
            self.assertEqual(["core", "identity", "general-1", "general-2"], [lane["name"] for lane in split["lanes"]])
            identity = split["lanes"][1]
            self.assertEqual((["account-switch"], ["account-groups", "folder-delete"], 120.5, 1, ["folder-delete"]),
                             (identity["keys"], identity["scripts"], identity["seconds"], identity["exitCode"],
                              identity["notRun"]))
            self.assertEqual("uncertain-create", split["lanes"][3]["failingStep"])
            self.assertEqual((False, 2, 190), (split["sequential"], split["generalLanes"], split["checks"]))
            self.assertEqual("core", split["scripts"][0]["lane"])
            self.assertEqual(14.2, split["machine"]["peak"]["load"])

    def test_the_split_record_names_a_timeout_its_sweep_and_the_lock_holder(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, split=queue_runner())

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            split = json.loads((reports / "latest.json").read_text(encoding="utf-8"))["tiers"]["split"]
            self.assertEqual(("FAIL", 124, 3), (split["verdict"], split["exitCode"], split["schemaVersion"]))
            self.assertEqual(("general-1", "save-status"), (split["failingScript"], split["failingStep"]))
            self.assertEqual([{"lane": "general-1", "unit": "save-status", "process": "general-1",
                               "elapsedSeconds": 86.4, "deadlineSeconds": 86}], split["timeouts"])
            self.assertEqual(86, split["failures"][0]["timeout"]["deadlineSeconds"])
            core, general = split["lanes"]
            self.assertEqual((120.5, 8.7, 2.0), (core["busySeconds"], core["idleSeconds"], core["overheadSeconds"]))
            self.assertEqual([{"name": "modern-workspace-chain", "seconds": 45.0, "outcome": "pass"},
                              {"name": "duplication", "seconds": 13.9, "outcome": "pass"}], core["units"])
            self.assertIsNone(core["sweep"])
            self.assertEqual("save-status", general["timeout"]["unit"])
            sweep = general["sweep"]
            self.assertEqual(("recursive", {"folder": 2, "template": 1}), (sweep["root"]["how"], sweep["root"]["deleted"]))
            self.assertEqual((0, 1, 1), (sweep["removed"], sweep["alreadyGone"], sweep["failed"]))
            self.assertEqual([{"collection": "folders", "id": "f", "status": 400}], sweep["notRemoved"])
            self.assertEqual((1, 0), (sweep["restored"], sweep["alreadyRestored"]))
            self.assertEqual([{"restore": "api-key", "label": "API key \"smoke 2\"", "as": "user1", "status": 503}],
                             sweep["notRestored"])
            self.assertEqual([sweep], split["sweeps"])
            self.assertEqual((4242, 17), (split["lock"]["holder"]["pid"], split["lock"]["replaced"]["pid"]))
            self.assertEqual(1200, split["unitTimeout"]["capSeconds"])
            self.assertEqual("/tmp/cedar-split-tier-x/evidence", split["evidenceDirectory"])

    def test_a_refused_split_run_is_recorded_without_reading_another_runs_report(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            refused = lambda _args, _kwargs: FakeResult(75)
            runner = runner_for(home, split=refused)
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            reports.mkdir(parents=True, exist_ok=True)
            # A report an earlier run left at this path says nothing about this run.
            (reports / f"split-smoke-{sources_digest(HEADS)}.json").write_text(
                json.dumps({"schemaVersion": 3, "verdict": "PASS"}), encoding="utf-8")

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            split = json.loads((reports / "latest.json").read_text(encoding="utf-8"))["tiers"]["split"]
            self.assertEqual(("FAIL", 75, True), (split["verdict"], split["exitCode"], split["lockRefused"]))
            self.assertNotIn("runnerVerdict", split)
            self.assertIn("held the lock", split["reportError"])

    def test_a_sequential_report_keeps_its_fields_and_has_no_lanes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)

            run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            split = json.loads((reports / "latest.json").read_text(encoding="utf-8"))["tiers"]["split"]
            self.assertEqual(1, split["schemaVersion"])
            self.assertNotIn("lanes", split)
            self.assertNotIn("failures", split)
            self.assertEqual([{"name": "modern-workspace", "seconds": 371.4, "exitCode": 0}], split["scripts"])

    def test_every_tier_runs_from_the_e2e_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)

            run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            e2e = str(home / "cedar-development" / "ops" / "e2e")
            npm_calls = [(args, kwargs) for args, kwargs in runner.calls if args[0] == "npm"]
            self.assertEqual(4, len(npm_calls))
            self.assertTrue(all(kwargs["cwd"] == e2e for _, kwargs in npm_calls))
            rest, browser, split, search = (args for args, _ in npm_calls)
            self.assertEqual(["npm", "run", "smoke:rest", "--"], rest[:4])
            self.assertTrue(rest[4].startswith("--report="))
            self.assertEqual(["npm", "run", "smoke"], browser)
            self.assertEqual(["npm", "run", "smoke:workspace:modern:full", "--"], split[:4])
            self.assertTrue(split[4].startswith("--report="))
            self.assertTrue(split[4].endswith(f"split-smoke-{sources_digest(HEADS)}.json"))

            self.assertEqual(["npm", "run", "smoke:search", "--"], search[:4])
            self.assertTrue(search[4].startswith("--report="))

    def test_rest_worker_budget_applies_to_rest_suites_and_search_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)
            self.assertEqual(0, run_smoke(home, runner=runner, clock=clock(),
                environment={"PATH": "/usr/bin"}, rest_workers=4))
            self.assertIn("--workers=4", runner.commands("npm", "run", "smoke:rest")[0])
            self.assertIn("--workers=4", runner.commands("npm", "run", "smoke:search")[0])
            self.assertEqual(["npm", "run", "smoke"], runner.commands("npm", "run", "smoke")[0])
        for value in (0, 5, 1.5, True):
            with self.assertRaises(ValueError):
                run_smoke(rest_workers=value)

    def test_split_lanes_only_change_the_split_command(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)
            self.assertEqual(0, run_smoke(home, runner=runner, clock=clock(),
                environment={"PATH": "/usr/bin"}, split_lanes=3))
            split = runner.commands("npm", "run", "smoke:workspace:modern:full")[0]
            self.assertEqual("--lanes=3", split[-1])
            self.assertTrue(split[4].startswith("--report="))
            self.assertFalse(any(arg.startswith("--lanes") for arg in runner.commands("npm", "run", "smoke:rest")[0]))
            self.assertEqual(["npm", "run", "smoke"], runner.commands("npm", "run", "smoke")[0])
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)
            run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})
            split = runner.commands("npm", "run", "smoke:workspace:modern:full")[0]
            self.assertFalse(any(arg.startswith("--lanes") for arg in split),
                             "without the option the runner's own default applies")
        for value in (0, 5, 1.5, True):
            with self.assertRaises(ValueError):
                run_smoke(split_lanes=value)

    def test_a_split_timeout_cap_only_changes_the_split_command(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)
            self.assertEqual(0, run_smoke(home, runner=runner, clock=clock(),
                environment={"PATH": "/usr/bin"}, split_lanes=2, split_timeout_cap=600))
            split = runner.commands("npm", "run", "smoke:workspace:modern:full")[0]
            self.assertEqual(["--lanes=2", "--timeout-cap=600"], split[-2:])
            self.assertFalse(any(arg.startswith("--timeout-cap") for arg in runner.commands("npm", "run", "smoke:rest")[0]))
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)
            run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})
            split = runner.commands("npm", "run", "smoke:workspace:modern:full")[0]
            self.assertFalse(any(arg.startswith("--timeout-cap") for arg in split),
                             "without the option the runner's own cap applies")
        for value in (59, 7201, 600.0, True, "600"):
            with self.assertRaises(ValueError):
                run_smoke(split_timeout_cap=value)

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

    def test_a_warming_service_is_waited_out_rather_than_refused(self):
        """A frontend compiling its bundle takes minutes to leave `starting`."""
        warming = (
            "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors\n"
            "resource\t101\t9007\tup\thealthy\tcurrent\t0\n"
            "terminology\t102\t9004\tup\tstarting\tcurrent\t0\n"
        )
        answers = [warming, warming, HEALTHY_TSV]

        def status(_args, _kwargs):
            return FakeResult(stdout=answers.pop(0) if len(answers) > 1 else answers[0])

        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)
            runner.answers[0] = (runner.answers[0][0], status)
            slept = []

            code = run_smoke(home, runner=runner, clock=clock(),
                             environment={"PATH": "/usr/bin"}, sleeper=slept.append)

            self.assertEqual(0, code, "a stack that finished warming should run the tiers")
            self.assertEqual(len(smoke_gate.TIERS), len(runner.commands("npm")))
            self.assertTrue(slept, "the gate should have waited rather than refused at once")

    def test_a_slow_probe_is_waited_out_rather_than_refused(self):
        """A server that is serving and did not answer in time is not a reason to refuse the gate.

        One slow dependency inside a health report produces it, the next poll usually clears it, and
        the run it would otherwise refuse is a run against a stack that was healthy all along.
        """
        slow = (
            "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors\n"
            "resource\t101\t9007\tup\thealthy\tcurrent\t0\n"
            "terminology\t102\t9004\tup\tslow\tcurrent\t0\n"
        )
        answers = [slow, HEALTHY_TSV]

        def status(_args, _kwargs):
            return FakeResult(stdout=answers.pop(0) if len(answers) > 1 else answers[0])

        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)
            runner.answers[0] = (runner.answers[0][0], status)
            slept = []

            code = run_smoke(home, runner=runner, clock=clock(),
                             environment={"PATH": "/usr/bin"}, sleeper=slept.append)

            self.assertEqual(0, code, "a probe that answered on the next poll should run the tiers")
            self.assertEqual(len(smoke_gate.TIERS), len(runner.commands("npm")))
            self.assertTrue(slept, "the gate should have waited rather than refused at once")

    def test_a_service_that_never_warms_still_refuses(self):
        warming = (
            "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors\n"
            "terminology\t102\t9004\tup\tstarting\tcurrent\t0\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, tsv=warming)
            slept = []

            code = run_smoke(home, runner=runner, clock=clock(),
                             environment={"PATH": "/usr/bin"}, sleeper=slept.append)

            self.assertEqual(1, code)
            self.assertEqual([], runner.commands("npm"))
            self.assertEqual(
                smoke_gate.WARMUP_WAIT_SECONDS // smoke_gate.WARMUP_POLL_SECONDS, len(slept),
                "the wait is bounded by its polls, so it ends without a wall clock")

    def test_a_failing_tier_still_lets_the_other_run_and_records_a_failed_run(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, browser=FakeResult(1))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            self.assertEqual(len(smoke_gate.TIERS), len(runner.commands("npm")))
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

    def test_old_rest_records_without_validated_evidence_require_a_new_run(self):
        for value in (None, False):
            report = report_for()
            report["tiers"]["rest"]["evidenceValid"] = value
            self.assertEqual(["the REST smoke has no validated complete check evidence"], evaluate(report, HEADS))

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

    def setUp(self):
        # The native profile selects the deployed version. These fixtures own theirs.
        version = patch.dict(os.environ, {"CEDAR_VERSION": "2.9.10-SNAPSHOT"})
        version.start()
        self.addCleanup(version.stop)

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
