import datetime as dt
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
import zipfile
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

# The one skip the fixtures' inventory allows, with the one reason it may be skipped for.
SKIP_REASON = "the local terminology store does not serve DOID"
ALLOWED_SKIPS = {"version": 1, "skips": [
    {"suite": "freeze", "section": "freeze", "name": "pin", "reason": SKIP_REASON}]}

HEALTHY_TSV = (
    "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors\n"
    "resource\t101\t9007\tup\thealthy\tcurrent\t0\n"
    "terminology\t102\t9004\tup\thealthy\tcurrent\t0\n"
    "ui-main\t201\t4200\tup\thealthy\tcurrent\t0\n"
    "ui-designer\t202\t4202\tup\thealthy\t-\t0\n"
)


# The browser scripts' committed steps, and the split tier's committed journey scenarios.
BROWSER_STEPS = {"version": 1, "scripts": {"login-smoke-test": ["login", "seed"],
                                            "metadata-first-save-smoke": ["first-save"]}}
JOURNEY_SCENARIOS = {"tab-isolation": [{"id": "tabs", "promise": "each tab keeps its own draft"}]}


def browser_runner(exit_code=0, steps=None):
    """The browser tier: each script writes the steps it completed where the gate asks."""
    def answer(_args, kwargs):
        directory = Path(kwargs["env"]["CEDAR_BROWSER_SMOKE_REPORTS"])
        directory.mkdir(parents=True, exist_ok=True)
        for script, expected in BROWSER_STEPS["scripts"].items():
            completed = (steps or {}).get(script, expected)
            (directory / f"{script}.json").write_text(json.dumps({
                "schemaVersion": 1, "script": script, "verdict": "PASS" if exit_code == 0 else "FAIL",
                "steps": completed, "failedStep": None}), encoding="utf-8")
        return FakeResult(exit_code)
    return answer


def _drop_scenarios(result, args):
    """Rewrite the split report the fake runner wrote, as if its one journey ran no scenario."""
    report = Path(next(a for a in args if a.startswith("--report=")).split("=", 1)[1])
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["journeyResults"][0]["scenarios"] = []
    report.write_text(json.dumps(payload), encoding="utf-8")
    return result


def fake_home(directory, repositories=HEADS):
    home = Path(directory)
    ops = home / "cedar-development" / "ops"
    (ops / "e2e").mkdir(parents=True)
    (ops / "e2e" / "package.json").write_text("{}", encoding="utf-8")
    (ops / "e2e" / "rest").mkdir()
    (ops / "e2e" / "rest" / "expected-checks.json").write_text(
        json.dumps({"version": 1, "checks": REST_CHECKS}), encoding="utf-8")
    (ops / "e2e" / "rest" / "allowed-skips.json").write_text(json.dumps(ALLOWED_SKIPS), encoding="utf-8")
    (ops / "e2e" / "browser-steps.json").write_text(json.dumps(BROWSER_STEPS), encoding="utf-8")
    (ops / "e2e" / "journey-scenarios.json").write_text(json.dumps(JOURNEY_SCENARIOS), encoding="utf-8")
    (ops / "cedar-services.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (ops / "build-train.json").write_text(json.dumps({
        "organization": "metadatacenter",
        "sourceBranch": "develop",
        "repositories": list(repositories),
    }), encoding="utf-8")
    for repository in repositories:
        (home / repository / ".git").mkdir(parents=True)
    write_frontend_build(home, repositories)
    return home


def stamped_jar(path, stamps, classes, dirty=()):
    """A jar holding the given classes and, per module, cedar-parent's stamp of (repository, commit)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in classes.items():
            archive.writestr(name, body)
        for module, (repository, commit) in stamps.items():
            archive.writestr(f"META-INF/cedar/{module}.git.properties",
                             f"#Generated by Git-Commit-Id-Plugin\ngit.commit.id={commit}\n"
                             f"git.dirty={'true' if module in dirty else 'false'}\n"
                             f"git.remote.origin.url=https\\://github.com/metadatacenter/{repository}.git\n")
    return path


def write_frontend_build(home, sources, dirty=(), packages=(), applications=()):
    """Record a frontend reactor deployment built from the given heads.

    `packages` names the repositories that stored a package, and `applications` the hosts the
    frontend inventory lists.
    """
    store = Path(home) / ".reactor"
    (store / "builds").mkdir(parents=True, exist_ok=True)
    build = "f" * 64
    selected = {name: "9" * 64 for name in packages}
    (store / "runtime.json").write_text(json.dumps({"build": build, "packages": selected}), encoding="utf-8")
    (store / "builds" / f"{build}.json").write_text(json.dumps({"schemaVersion": 1, "packages": selected, "repositories": {
        repository: {"source": {"revision": head, "dirty": repository in dirty, "sourceSha256": "0" * 64}}
        for repository, head in dict(sources).items()}}), encoding="utf-8")
    inventory = Path(home) / "cedar-development" / "ops" / "frontend-train.json"
    inventory.parent.mkdir(parents=True, exist_ok=True)
    inventory.write_text(json.dumps({"frontends": [{"repository": name} for name in applications]}),
                         encoding="utf-8")


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
                        **({"status": "skipped", "reason": SKIP_REASON} if key.startswith("freeze\x1f")
                           else {"status": "passed"})) for key in REST_CHECKS],
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


def split_payload(verdict="PASS", exit_code=0, failing_script=None, failing_step=None):
    """A split tier report: the runner's details and the evidence that it ran its inventory."""
    return {
        "schemaVersion": 1, "verdict": verdict,
        "failingScript": failing_script, "failingStep": failing_step,
        "failure": "locator.click: Timeout 25000ms exceeded." if failing_step else None,
        "scripts": [{"name": "modern-workspace", "command": ["node", "modern-workspace-smoke.mjs"],
                     "exitCode": exit_code, "seconds": 371.4}],
        "notRun": ["ced-host"] if exit_code else [],
        "inventoryMode": "verified", "failures": [], "checks": 12,
        "units": [{"name": "modern-workspace", "kind": "chain", "outcome": "pass" if exit_code == 0 else "fail"},
                  {"name": "tab-isolation", "kind": "journey", "outcome": "pass"}],
        "journeyResults": [{"name": "tab-isolation", "journeyKey": "tab-isolation", "outcome": "pass",
                            "scenarios": [{"id": "tabs", "promise": "each tab keeps its own draft"}]}],
        "machine": {"start": {"load": [3.1, 2.9, 2.5], "swap": None},
                    "end": {"load": [4.0, 3.2, 2.6], "swap": None}},
        "steps": [{"script": "modern-workspace", "step": name, "seconds": seconds}
                  for name, seconds in (("login", 1.2), ("metadata", 41.0), ("sharing", 30.5),
                                        ("ownership", 12.0), ("save-status", 15.1), ("delete", 9.9))],
    }


def split_runner(verdict="PASS", exit_code=0, failing_script=None, failing_step=None):
    def answer(args, _kwargs):
        report = next(a for a in args if a.startswith("--report=")).split("=", 1)[1]
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text(json.dumps(split_payload(verdict, exit_code, failing_script, failing_step)),
                                encoding="utf-8")
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


def runner_for(home, *, tsv=HEALTHY_TSV, heads=HEADS, dirty=(), rest=None, browser=None,
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
        (("npm", "run", "smoke"), browser or browser_runner()),
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
            "browser": {"verdict": "PASS", "evidenceValid": True, "finishedAt": "2026-09-07T10:02:30+00:00"},
            "split": {"verdict": "PASS", "evidenceValid": True, "finishedAt": "2026-09-07T10:03:30+00:00"},
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

    def _with_previous_pass(self, directory, previous_checks, retired=None):
        """A home whose last passing record tested cedar-development at a commit holding previous_checks."""
        heads = {**HEADS, "cedar-development": "d" * 40}
        home = fake_home(directory, heads)
        rest = home / smoke_gate.E2E / "rest"
        if retired is not None:
            (rest / "retired-checks.json").write_text(json.dumps({"version": 1, "checks": retired}), encoding="utf-8")
        reports = smoke_gate.reports_dir(home)
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "0123456789abcdef.json").write_text(json.dumps(report_for(
            {**heads, "cedar-development": "e" * 40}, finishedAt="2026-09-01T10:00:00+00:00")), encoding="utf-8")
        runner = runner_for(home, heads=heads)
        runner.answers.insert(0, (("git", "show"), FakeResult(stdout=json.dumps(
            {"version": 1, "checks": previous_checks}))))
        return home, heads, runner

    def test_rest_inventory_cannot_lose_a_check_its_last_pass_ran_without_retiring_it(self):
        dropped = "folders\x1ffolders\x1fdeleted"
        with tempfile.TemporaryDirectory() as directory:
            home, heads, runner = self._with_previous_pass(directory, REST_CHECKS + [dropped], retired=[])
            self.assertEqual(1, run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"}))
            rest = read_report(home, heads)["tiers"]["rest"]
            self.assertEqual([dropped], rest["droppedChecks"])
            self.assertFalse(rest["evidenceValid"])
            self.assertIn("folders / folders / deleted", rest["reportError"])
            self.assertEqual([["git", "show", f"{'e' * 40}:ops/e2e/rest/expected-checks.json"]],
                             runner.commands("git", "show"))

    def test_rest_inventory_may_lose_a_check_the_retired_list_names(self):
        with tempfile.TemporaryDirectory() as directory:
            home, heads, runner = self._with_previous_pass(
                directory, REST_CHECKS + ["folders\x1ffolders\x1fdeleted", REST_CHECKS[1]],
                retired=[{"suite": "folders", "section": "folders", "name": "deleted", "reason": "merged"},
                         {"suite": "folders", "section": "folders", "name": "created", "reason": "one repetition"}])
            self.assertEqual(0, run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"}))
            self.assertEqual([], read_report(home, heads)["tiers"]["rest"]["droppedChecks"])

    def test_rest_inventory_comparison_fails_closed_when_it_cannot_be_made(self):
        cases = {
            "no retired list": dict(retired=None),
            "a retired check without a reason": dict(retired=[{"suite": "a", "section": "b", "name": "c", "reason": ""}]),
        }
        for name, case in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                home, heads, runner = self._with_previous_pass(directory, REST_CHECKS, **case)
                self.assertEqual(1, run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"}))
                rest = read_report(home, heads)["tiers"]["rest"]
                self.assertFalse(rest["evidenceValid"])
                self.assertIn("cannot compare the REST inventory", rest["reportError"])

    def test_rest_tier_runs_within_a_wall_clock_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home)
            self.assertEqual(0, run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"}))
            budgets = {tuple(args[:3]): kwargs.get("timeout") for args, kwargs in runner.calls if args[:2] == ["npm", "run"]}
            self.assertEqual(smoke_gate.REST_BUDGET_SECONDS, budgets[("npm", "run", "smoke:rest")])
            self.assertFalse(read_report(home, HEADS)["tiers"]["rest"]["timedOut"])

    def test_rest_tier_that_overruns_its_budget_fails(self):
        def stalled(args, kwargs):
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, rest=stalled)
            self.assertEqual(1, run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"}))
            rest = read_report(home, HEADS)["tiers"]["rest"]
            self.assertEqual("FAIL", rest["verdict"])
            self.assertEqual(124, rest["exitCode"])
            self.assertTrue(rest["timedOut"])
            self.assertEqual(4, len(runner.commands("npm", "run")))

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

    def test_a_split_run_without_a_report_fails_for_want_of_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            runner = runner_for(home, split=FakeResult(0))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            split = json.loads((reports / "latest.json").read_text(encoding="utf-8"))["tiers"]["split"]
            self.assertEqual("FAIL", split["verdict"])
            self.assertIn("reportError", split)
            self.assertFalse(split["evidenceValid"])

    def test_browser_and_split_tiers_must_report_every_step_and_scenario_their_inventories_name(self):
        cases = {
            "browser": dict(browser=browser_runner(steps={"login-smoke-test": ["login"]})),
            "split": dict(split=lambda args, kwargs: _drop_scenarios(split_runner()(args, kwargs), args)),
        }
        for tier, overrides in cases.items():
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as directory:
                home = fake_home(directory)
                runner = runner_for(home, **overrides)

                code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

                self.assertEqual(1, code)
                report = read_report(home, HEADS)
                self.assertEqual("FAIL", report["tiers"][tier]["verdict"])
                self.assertFalse(report["tiers"][tier]["evidenceValid"])
                self.assertIn(f"the {tier} smoke", " ".join(evaluate(report, HEADS)))

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
            # The REST tier runs on a thread of its own, so its call may come before or after the browser's.
            rest, browser, split, search = (runner.commands("npm", "run", script)[0]
                                            for script in ("smoke:rest", "smoke", "smoke:workspace:modern:full",
                                                           "smoke:search"))
            self.assertEqual(["npm", "run", "smoke:rest", "--"], rest[:4])
            self.assertTrue(rest[4].startswith("--report="))
            self.assertEqual(["npm", "run", "smoke"], browser)
            self.assertEqual(["npm", "run", "smoke:workspace:modern:full", "--"], split[:4])
            self.assertTrue(split[4].startswith("--report="))
            self.assertRegex(split[4], rf"split-smoke-{sources_digest(HEADS)}-[0-9a-f]{{32}}\.json$")

            self.assertEqual(["npm", "run", "smoke:search", "--"], search[:4])
            self.assertTrue(search[4].startswith("--report="))

    def test_the_rest_tier_runs_beside_the_others_and_writes_its_output_beside_its_report(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            browser_started = threading.Event()
            write_report = rest_runner()
            seen = {}

            def rest(args, kwargs):
                seen["beside"] = browser_started.wait(timeout=10)
                kwargs["stdout"].write("rest output\n")
                return write_report(args, kwargs)

            def browser(args, kwargs):
                browser_started.set()
                browser_runner()(args, kwargs)
                return FakeResult(0)

            runner = runner_for(home, rest=rest, browser=browser)
            self.assertEqual(0, run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"}))

            self.assertTrue(seen["beside"], "the REST tier waited for the browser tier instead of running beside it")
            reports = home / "cedar-development" / "ops" / "e2e" / "reports" / "smoke-gate"
            rest_record = json.loads((reports / "latest.json").read_text(encoding="utf-8"))["tiers"]["rest"]
            self.assertEqual(str(Path(rest_record["report"]).with_suffix(".log")), rest_record["log"])
            self.assertEqual("rest output\n", Path(rest_record["log"]).read_text(encoding="utf-8"))
            streams = {args[2]: kwargs for args, kwargs in runner.calls if args[0] == "npm"}
            self.assertIs(subprocess.STDOUT, streams["smoke:rest"]["stderr"])
            for script in ("smoke", "smoke:workspace:modern:full", "smoke:search"):
                self.assertNotIn("stdout", streams[script])
            # The split and search tiers hold the split tier's lock, so they keep their order.
            self.assertEqual(["smoke", "smoke:workspace:modern:full", "smoke:search"],
                             [args[2] for args, _ in runner.calls if args[0] == "npm" and args[2] != "smoke:rest"])

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
        for value in (0, 7, 1.5, True):
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

    def _service(self, home, service, *, jar_epoch, head_epoch, stamp="a" * 40):
        root = home / f"cedar-{service}-server"
        (root / ".git").mkdir(parents=True, exist_ok=True)
        target = root / f"cedar-{service}-server-application" / "target"
        target.mkdir(parents=True, exist_ok=True)
        jar = target / f"cedar-{service}-server-application-2.9.10-SNAPSHOT.jar"
        stamped_jar(jar, {f"cedar-{service}-server-application": (f"cedar-{service}-server", stamp)}, {})
        os.utime(jar, (jar_epoch, jar_epoch))
        return FakeRunner(answers=[
            (["git", "log"], FakeResult(stdout=f"{head_epoch} {'a' * 40}\n")),
        ])

    def test_a_jar_built_from_another_commit_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            runner = self._service(home, "resource", jar_epoch=3_000, head_epoch=2_000, stamp="b" * 40)

            findings = smoke_gate.source_currency_findings(home, ["resource"], runner=runner)

        self.assertEqual(["resource was built from cedar-resource-server bbbbbbbb, not develop aaaaaaaa"], findings)

    def test_a_jar_without_a_stamp_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            runner = self._service(home, "resource", jar_epoch=3_000, head_epoch=2_000)
            jar = (home / "cedar-resource-server" / "cedar-resource-server-application" / "target"
                   / "cedar-resource-server-application-2.9.10-SNAPSHOT.jar")
            stamped_jar(jar, {}, {"Main.class": b"m"})

            findings = smoke_gate.source_currency_findings(home, ["resource"], runner=runner)

        self.assertEqual(1, len(findings))
        self.assertIn("resource was built without a stamp of cedar-resource-server", findings[0])

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
            stamped_jar(jar, {"cedar-resource-server-application": ("cedar-resource-server", "a" * 40)}, {})
            os.utime(jar, (1_000, 1_000))
            runner = runner_for(home, tsv=tsv)
            runner.answers.insert(0, (["git", "log"], FakeResult(stdout=f"2000 {'b' * 40}\n")))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            self.assertEqual([], runner.commands("npm"))


class DeployedSourceTest(unittest.TestCase):
    """What the stack runs must have been built from the heads the record names."""

    def setUp(self):
        version = patch.dict(os.environ, {"CEDAR_VERSION": "2.9.10-SNAPSHOT"})
        version.start()
        self.addCleanup(version.stop)

    @staticmethod
    def _jar(path, classes, epoch, stamps):
        stamped_jar(path, stamps, classes)
        os.utime(path, (epoch, epoch))
        return path

    def _stack(self, home, *, library_classes, server_classes, library_epoch=3_000, server_epoch=4_000,
               library_commit="c" * 40, server_commit="c" * 40):
        for repository in ("cedar-resource-server", "cedar-model-validation-library"):
            (home / repository / ".git").mkdir(parents=True, exist_ok=True)
        library = {"cedar-model-validation-library": ("cedar-model-validation-library", library_commit)}
        self._jar(home / "cedar-model-validation-library" / "target"
                  / "cedar-model-validation-library-2.9.10-SNAPSHOT.jar", library_classes, library_epoch, library)
        self._jar(home / "cedar-resource-server" / "cedar-resource-server-application" / "target"
                  / "cedar-resource-server-application-2.9.10-SNAPSHOT.jar", server_classes, server_epoch,
                  {**library, "cedar-resource-server-application": ("cedar-resource-server", server_commit)})

    @staticmethod
    def _git(commits, moved=None):
        """Answer `git log` and `git reflog` per repository: commit epoch and, optionally, arrival epoch."""
        def log(_args, kwargs):
            epoch = commits.get(Path(kwargs["cwd"]).name)
            return FakeResult(stdout=f"{epoch} {'c' * 40}") if epoch else FakeResult(128)

        def reflog(_args, kwargs):
            epoch = (moved or {}).get(Path(kwargs["cwd"]).name)
            return FakeResult(stdout=f"develop@{{{epoch}}}") if epoch else FakeResult(stdout="")
        return FakeRunner([(["git", "log"], log), (["git", "reflog"], reflog)])

    def test_a_server_embedding_the_latest_library_build_is_current(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            classes = {"org/metadatacenter/model/validation/Validator.class": b"v2"}
            self._stack(home, library_classes=classes, server_classes={**classes, "Main.class": b"m"})
            runner = self._git({"cedar-resource-server": 2_000, "cedar-model-validation-library": 2_000})

            self.assertEqual([], smoke_gate.source_currency_findings(home, ["resource"], runner=runner))

    def test_a_library_built_from_an_earlier_commit_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            classes = {"org/metadatacenter/model/validation/Validator.class": b"v1"}
            self._stack(home, library_classes=classes, server_classes=classes, library_commit="b" * 40)
            runner = self._git({"cedar-resource-server": 2_000, "cedar-model-validation-library": 2_000})

            findings = smoke_gate.source_currency_findings(home, ["resource"], runner=runner)

        self.assertIn("cedar-model-validation-library was built from cedar-model-validation-library bbbbbbbb, "
                      "not develop cccccccc", findings)
        self.assertIn("resource was built from cedar-model-validation-library bbbbbbbb, not develop cccccccc",
                      findings)

    def test_a_server_built_against_another_library_build_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            name = "org/metadatacenter/model/validation/Validator.class"
            self._stack(home, library_classes={name: b"rebuilt"}, server_classes={name: b"older snapshot"})
            runner = self._git({"cedar-resource-server": 2_000, "cedar-model-validation-library": 2_000})

            findings = smoke_gate.source_currency_findings(home, ["resource"], runner=runner)

        self.assertEqual(1, len(findings))
        self.assertIn("resource was built before its libraries' latest build", findings[0])
        self.assertIn("cedar-model-validation-library in which 1 of 1 classes differ", findings[0])

    def test_the_stamp_not_the_dates_decides_a_server_jar(self):
        # A commit made elsewhere before the build and pulled after it has an early date; the stamp
        # still names the commit the build took.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            classes = {"org/metadatacenter/model/validation/Validator.class": b"v1"}
            self._stack(home, library_classes=classes, server_classes=classes, server_commit="b" * 40)
            runner = self._git({"cedar-resource-server": 2_000, "cedar-model-validation-library": 2_000},
                               moved={"cedar-resource-server": 3_500})

            findings = smoke_gate.source_currency_findings(home, ["resource"], runner=runner)

        self.assertEqual(["resource was built from cedar-resource-server bbbbbbbb, not develop cccccccc"], findings)

    def test_a_build_that_saw_uncommitted_changes_is_recorded_dirty(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            jar = (home / "cedar-resource-server" / "cedar-resource-server-application" / "target"
                   / "cedar-resource-server-application-2.9.10-SNAPSHOT.jar")
            stamped_jar(jar, {"cedar-resource-server-application": ("cedar-resource-server", "c" * 40)}, {},
                        dirty={"cedar-resource-server-application"})

            self.assertEqual(["cedar-resource-server"], smoke_gate.stamped_dirty(home, ["resource", "ui-main"]))

    def test_a_parent_commit_after_the_build_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            classes = {"org/metadatacenter/model/validation/Validator.class": b"v1"}
            self._stack(home, library_classes=classes, server_classes=classes)
            (home / "cedar-parent" / ".git").mkdir(parents=True)
            runner = self._git({"cedar-resource-server": 2_000, "cedar-model-validation-library": 2_000,
                                "cedar-parent": 4_500})

            findings = smoke_gate.source_currency_findings(home, ["resource"], runner=runner)

        self.assertEqual(1, len(findings))
        self.assertIn("resource was built before cedar-parent develop", findings[0])

    def test_a_library_never_built_here_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._stack(home, library_classes={}, server_classes={"Main.class": b"m"})
            for jar in (home / "cedar-model-validation-library" / "target").glob("*.jar"):
                jar.unlink()
            runner = self._git({"cedar-resource-server": 2_000, "cedar-model-validation-library": 2_000})

            findings = smoke_gate.source_currency_findings(home, ["resource"], runner=runner)

        self.assertEqual(1, len(findings))
        self.assertIn("cedar-model-validation-library was built nowhere on this machine", findings[0])

    def test_frontends_built_from_another_commit_or_dirty_are_named(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            write_frontend_build(home, {"cedar-a": "a" * 40, "cedar-b": "0" * 40, "cedar-c": "c" * 40},
                                 dirty={"cedar-c"})

            findings, dirty, build = smoke_gate.frontend_sources(
                home, {"cedar-a": "a" * 40, "cedar-b": "b" * 40, "cedar-c": "c" * 40})

        self.assertEqual(1, len(findings))
        self.assertIn("cedar-b built from 00000000, not develop bbbbbbbb", findings[0])
        self.assertEqual(["cedar-c"], dirty)
        self.assertEqual("f" * 64, build)

    def test_no_frontend_deployment_is_a_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            findings, dirty, build = smoke_gate.frontend_sources(Path(directory), HEADS)

        self.assertEqual(1, len(findings))
        self.assertIn("no frontend reactor deployment records", findings[0])
        self.assertIsNone(build)

    def test_a_run_refuses_frontends_built_from_other_heads_and_records_a_dirty_build(self):
        with patch("org.metadatacenter.smoke_gate.shutil.which", return_value="/usr/bin/npm"):
            with tempfile.TemporaryDirectory() as directory:
                home = fake_home(directory)
                write_frontend_build(home, {"cedar-a": "a" * 40, "cedar-b": "0" * 40})
                runner = runner_for(home)

                code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

                self.assertEqual(1, code)
                self.assertEqual([], runner.commands("npm"))

            with tempfile.TemporaryDirectory() as directory:
                home = fake_home(directory)
                write_frontend_build(home, HEADS, dirty={"cedar-b"})
                runner = runner_for(home)

                run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

                record = json.loads((smoke_gate.reports_dir(home) / "latest.json").read_text())
                self.assertEqual(["cedar-b"], record["dirty"])
                self.assertEqual("f" * 64, record["frontendBuild"])


class AllowedSkipTest(unittest.TestCase):
    """A skipped REST check counts only when the committed allowlist names it with its reason."""

    def _details(self, home, payload):
        report = home / "rest.json"
        report.write_text(json.dumps(payload), encoding="utf-8")
        inventory = home / "cedar-development" / "ops" / "e2e" / "rest" / "expected-checks.json"
        return smoke_gate._rest_details(report, inventory)

    def test_an_allowed_skip_with_its_reason_is_valid_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            details = self._details(fake_home(directory), rest_payload())

        self.assertTrue(details["evidenceValid"], details.get("reportError"))
        self.assertEqual([], details["unallowedSkips"])

    def test_a_skip_the_allowlist_does_not_name_fails_the_evidence(self):
        payload = rest_payload()
        folders = next(check for check in payload["checks"] if check["suite"] == "folders")
        folders.update(status="skipped", reason="the deployment has no folders")
        payload["counts"].update(passed=2, skipped=2)
        with tempfile.TemporaryDirectory() as directory:
            details = self._details(fake_home(directory), payload)

        self.assertFalse(details["evidenceValid"])
        self.assertEqual(["folders\x1ffolders\x1fcreated"], details["unallowedSkips"])
        self.assertIn("rest/allowed-skips.json does not allow", details["reportError"])

    def test_an_allowed_check_skipped_for_another_reason_fails_the_evidence(self):
        payload = rest_payload()
        next(check for check in payload["checks"] if check["suite"] == "freeze")["reason"] = "the search did not answer"
        with tempfile.TemporaryDirectory() as directory:
            details = self._details(fake_home(directory), payload)

        self.assertFalse(details["evidenceValid"])
        self.assertEqual(["freeze\x1ffreeze\x1fpin"], details["unallowedSkips"])

    def test_a_missing_allowlist_fails_the_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            (home / "cedar-development" / "ops" / "e2e" / "rest" / "allowed-skips.json").unlink()
            details = self._details(home, rest_payload())

        self.assertFalse(details["evidenceValid"])
        self.assertIn("allowed-skips.json", details["reportError"])

    def test_the_gate_names_unallowed_skips(self):
        record = report_for()
        record["tiers"]["rest"].update(unallowedSkips=["folders\x1ffolders\x1fcreated"])
        self.assertEqual(["the REST smoke skipped 1 check(s) that rest/allowed-skips.json does not allow"],
                         evaluate(record, HEADS))


class ChangesDuringRunTest(unittest.TestCase):
    """What moves while the tiers run turns the record into a failure that names it."""

    def setUp(self):
        which = patch("org.metadatacenter.smoke_gate.shutil.which", return_value="/usr/bin/npm")
        which.start()
        self.addCleanup(which.stop)

    @staticmethod
    def _after_tiers(runner, before, after):
        """Answer one way until a tier has run and another way after it."""
        def answer(args, kwargs):
            value = after if runner.commands("npm") else before
            return value(args, kwargs) if callable(value) else value
        return answer

    def _run(self, directory, change):
        home = fake_home(directory)
        runner = runner_for(home)
        change(home, runner)
        code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})
        return code, json.loads((smoke_gate.reports_dir(home) / "latest.json").read_text())

    def test_an_undisturbed_run_records_no_change(self):
        with tempfile.TemporaryDirectory() as directory:
            code, record = self._run(directory, lambda home, runner: None)

        self.assertEqual(0, code)
        self.assertEqual([], record["changedDuringRun"])
        self.assertEqual([], evaluate(record, HEADS))

    def test_a_head_that_moves_while_the_tiers_run_fails_the_record(self):
        moved = {**HEADS, "cedar-b": "c" * 40}

        def change(home, runner):
            runner.answers.insert(0, (("git", "rev-parse"),
                                      self._after_tiers(runner, rev_parse(HEADS), rev_parse(moved))))
        with tempfile.TemporaryDirectory() as directory:
            code, record = self._run(directory, change)

        self.assertEqual(1, code)
        self.assertEqual("FAIL", record["verdict"])
        self.assertIn("cedar-b moved from bbbbbbbb to cccccccc while the tiers ran", record["changedDuringRun"])
        self.assertTrue(any("changed while the smoke run" in finding for finding in evaluate(record, HEADS)))

    def test_a_service_restarted_while_the_tiers_run_fails_the_record(self):
        restarted = HEALTHY_TSV.replace("resource\t101", "resource\t999")

        def change(home, runner):
            controller = str(home / "cedar-development" / "ops" / "cedar-services.sh")
            runner.answers.insert(0, ((controller, "status-tsv"), self._after_tiers(
                runner, FakeResult(stdout=HEALTHY_TSV), FakeResult(stdout=restarted))))
        with tempfile.TemporaryDirectory() as directory:
            code, record = self._run(directory, change)

        self.assertEqual(1, code)
        self.assertIn("resource was served by another process after the run (pid 101, then 999)",
                      record["changedDuringRun"])

    def test_a_clean_tree_edited_while_the_tiers_run_fails_the_record(self):
        def change(home, runner):
            runner.answers.insert(0, (("git", "status"), self._after_tiers(
                runner, FakeResult(stdout=""), FakeResult(stdout=" M src/app.ts"))))
        with tempfile.TemporaryDirectory() as directory:
            code, record = self._run(directory, change)

        self.assertEqual(1, code)
        self.assertIn("cedar-a gained uncommitted changes while the tiers ran", record["changedDuringRun"])

    def test_a_frontend_redeployed_while_the_tiers_run_fails_the_record(self):
        def change(home, runner):
            original = runner.answers[[prefix for prefix, _ in runner.answers].index(("npm", "run", "smoke"))][1]

            def redeploy(args, kwargs):
                store = home / ".reactor"
                (store / "builds" / ("e" * 64 + ".json")).write_text(
                    (store / "builds" / ("f" * 64 + ".json")).read_text())
                (store / "runtime.json").write_text(json.dumps({"build": "e" * 64, "packages": {}}))
                return original(args, kwargs) if callable(original) else original
            runner.answers.insert(0, (("npm", "run", "smoke"), redeploy))
        with tempfile.TemporaryDirectory() as directory:
            code, record = self._run(directory, change)

        self.assertEqual(1, code)
        self.assertIn("the frontends were redeployed with another reactor build while the tiers ran",
                      record["changedDuringRun"])


class ExpectedCeeVersionTest(unittest.TestCase):
    """The browser tier is told which CEE the recorded head declares, and fails on any other."""

    HEADS = {**HEADS, "cedar-embeddable-editor": "e" * 40}

    def setUp(self):
        which = patch("org.metadatacenter.smoke_gate.shutil.which", return_value="/usr/bin/npm")
        which.start()
        self.addCleanup(which.stop)

    def _runner(self, home, package):
        runner = runner_for(home, heads=self.HEADS)
        runner.answers.insert(0, (("git", "show", "e" * 40 + ":package.json"), package))
        return runner

    def test_the_browser_tier_is_given_the_version_the_head_declares(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory, self.HEADS)
            runner = self._runner(home, FakeResult(stdout=json.dumps({"version": "2.0.36-dev.1"})))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(0, code)
            browser = next(kwargs for args, kwargs in runner.calls if args == ["npm", "run", "smoke"])
            self.assertEqual("2.0.36-dev.1", browser["env"]["CEDAR_EXPECT_CEE_VERSION"])
            record = json.loads((smoke_gate.reports_dir(home) / "latest.json").read_text())
            self.assertEqual("2.0.36-dev.1", record["tiers"]["browser"]["expectedCeeVersion"])

    def test_an_unreadable_cee_version_stops_the_run_before_any_tier(self):
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory, self.HEADS)
            runner = self._runner(home, FakeResult(128, stderr="fatal: path 'package.json' does not exist"))

            code = run_smoke(home, runner=runner, clock=clock(), environment={"PATH": "/usr/bin"})

            self.assertEqual(1, code)
            self.assertEqual([], runner.commands("npm"))

    def test_a_record_without_a_cee_head_expects_no_version(self):
        self.assertEqual((None, None), smoke_gate.expected_cee_version("/home", HEADS, runner=FakeRunner()))


class SplitReportPathTest(unittest.TestCase):
    """Each attempt writes its split report to a path of its own."""

    def setUp(self):
        which = patch("org.metadatacenter.smoke_gate.shutil.which", return_value="/usr/bin/npm")
        which.start()
        self.addCleanup(which.stop)

    def test_a_crashed_split_attempt_does_not_inherit_an_earlier_report(self):
        crashed = FakeResult(1)
        with tempfile.TemporaryDirectory() as directory:
            home = fake_home(directory)
            run_smoke(home, runner=runner_for(home), clock=clock(), environment={"PATH": "/usr/bin"})
            first = json.loads((smoke_gate.reports_dir(home) / "latest.json").read_text())["tiers"]["split"]
            self.assertEqual("PASS", first["runnerVerdict"])

            run_smoke(home, runner=runner_for(home, split=lambda _args, _kwargs: crashed), clock=clock(),
                      environment={"PATH": "/usr/bin"})
            second = json.loads((smoke_gate.reports_dir(home) / "latest.json").read_text())["tiers"]["split"]

        self.assertNotEqual(first["report"], second["report"])
        self.assertNotIn("runnerVerdict", second)
        self.assertIn("reportError", second)


class ServedBundleTest(unittest.TestCase):
    """What each frontend serves must carry the stamp of the source the reactor's record names."""

    SOURCES = {"cedar-workspace": "a" * 40, "cedar-embeddable-editor": "e" * 40,
               "cedar-template-designer": "d" * 40}

    @staticmethod
    def _package(root, key, name, commit, content="5" * 64, files=None):
        directory = root / "node_modules" / key
        directory.mkdir(parents=True, exist_ok=True)
        package = {"name": f"@org.metadatacenter/{name}", "version": "1.0.0"}
        if commit is not None:
            package["cedarBuildInfo"] = {"sourceCommit": commit, "sourceDirty": False, "sourceSha256": content}
        (directory / "package.json").write_text(json.dumps(package), encoding="utf-8")
        for file, body in (files or {}).items():
            (directory / file).write_bytes(body)
        return directory

    @staticmethod
    def _build_info(root, application, commit, dirty=False):
        info = {"application": application, "version": "1.0.0", "versionModifier": "", "sourceCommit": commit,
                "sourceDirty": dirty, "bundleSha256": smoke_gate.served_tree_sha256(root)}
        (root / "app" / "config").mkdir(parents=True, exist_ok=True)
        (root / "app" / "config" / "build-info.json").write_text(json.dumps(info), encoding="utf-8")

    def _workspace(self, home, *, stamp="e" * 40, served=b"// cee", info=True):
        """A Workspace serving a copy of the CEE bundle it installed, and recording its own build."""
        root = home / "cedar-workspace"
        root.mkdir(parents=True)
        (root / "package.json").write_text(json.dumps({"name": "cedar-workspace", "dependencies": {
            "cedar-embeddable-editor": "npm:@org.metadatacenter/cedar-embeddable-editor@1.0.0"}}), encoding="utf-8")
        self._package(root, "cedar-embeddable-editor", "cedar-embeddable-editor", stamp,
                      files={"cedar-embeddable-editor.js": b"// cee"})
        copy = root / "app" / "third_party_components" / "cedar-embeddable-editor"
        copy.mkdir(parents=True)
        (copy / "cedar-embeddable-editor.js").write_bytes(served)
        (root / "app" / "index.html").write_text("<html></html>", encoding="utf-8")
        if info:
            self._build_info(root, "cedar-workspace", self.SOURCES["cedar-workspace"])
        write_frontend_build(home, self.SOURCES, packages=["cedar-embeddable-editor"],
                             applications=["cedar-workspace"])
        return root

    def _findings(self, home):
        findings, dirty, _build = smoke_gate.frontend_sources(home, self.SOURCES)
        return findings, dirty

    def test_a_host_serving_stamped_packages_and_its_recorded_build_has_no_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._workspace(home)
            self.assertEqual(([], []), self._findings(home))

    def test_an_installed_package_without_a_stamp_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._workspace(home, stamp=None)
            findings, _dirty = self._findings(home)
        self.assertEqual(1, len(findings))
        self.assertIn("cedar-workspace serves cedar-embeddable-editor from a package with no build stamp", findings[0])

    def test_a_package_built_from_another_commit_is_refused_unless_its_content_is_the_recorded_one(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = self._workspace(home, stamp="1" * 40)
            findings, _dirty = self._findings(home)
            self.assertEqual(1, len(findings))
            self.assertIn("built from 11111111, not the reactor's eeeeeeee", findings[0])
            # The reactor reused a package built under another commit from identical content.
            self._package(root, "cedar-embeddable-editor", "cedar-embeddable-editor", "1" * 40, content="0" * 64)
            self.assertEqual([], self._findings(home)[0])
            self._package(root, "cedar-embeddable-editor", "cedar-embeddable-editor", "1" * 40, content="2" * 64)
            self.assertEqual(1, len(self._findings(home)[0]))

    def test_a_served_copy_that_is_not_the_installed_bundle_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._workspace(home, served=b"// an earlier cee")
            findings, _dirty = self._findings(home)
        self.assertEqual(1, len(findings))
        self.assertIn("serves a cedar-embeddable-editor.js that is not the one in its installed", findings[0])

    def test_a_host_build_identity_must_exist_name_develop_and_describe_the_served_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = self._workspace(home, info=False)
            self.assertIn("serves no readable build identity at /config/build-info.json", self._findings(home)[0][0])
            self._build_info(root, "cedar-workspace", "0" * 40)
            self.assertIn("serves a build of 00000000, not develop aaaaaaaa", self._findings(home)[0][0])
            self._build_info(root, "cedar-workspace", self.SOURCES["cedar-workspace"], dirty=True)
            self.assertEqual(([], ["cedar-workspace"]), self._findings(home))
            (root / "app" / "index.html").write_text("<html>edited</html>", encoding="utf-8")
            self.assertIn("does not describe the tree it serves", self._findings(home)[0][0])

    def test_the_served_tree_digest_is_the_one_the_build_identity_writer_records(self):
        # write-native-frontend-build-info.mjs hashes "<sha256>  <path>\n" per file, sorted by path bytes.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app" / "config").mkdir(parents=True)
            (root / "app" / "b.js").write_bytes(b"b")
            (root / "app" / "a b.js").write_bytes(b"a")
            (root / "app" / "config" / "build-info.json").write_text("{}", encoding="utf-8")
            manifest = (f"{hashlib.sha256(b'a').hexdigest()}  app/a b.js\n"
                        f"{hashlib.sha256(b'b').hexdigest()}  app/b.js\n")
            self.assertEqual(hashlib.sha256(manifest.encode()).hexdigest(), smoke_gate.served_tree_sha256(root))

    def test_a_designer_must_serve_its_installed_components_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / "cedar-template-designer"
            root.mkdir(parents=True)
            (root / "package.json").write_text(json.dumps({"name": "cedar-template-designer", "dependencies": {
                "cedar-embeddable-editor": "npm:@org.metadatacenter/cedar-embeddable-editor@1.0.0"}}), encoding="utf-8")
            self._package(root, "cedar-embeddable-editor", "cedar-embeddable-editor", "e" * 40,
                          files={"integration.mjs": b"// module"})
            components = root / "app" / "components"
            components.mkdir(parents=True)
            (components / "cedar-editors.js").write_bytes(b"// editors")
            entry = {"source": "package", "bundle": "integration.mjs",
                     "sha256": hashlib.sha256(b"// module").hexdigest(),
                     "delivery": {"file": "cedar-editors.js", "sha256": hashlib.sha256(b"// editors").hexdigest()}}
            (components / "manifest.json").write_text(json.dumps({"cedar-embeddable-editor": entry}), encoding="utf-8")
            self._build_info(root, "cedar-template-designer", self.SOURCES["cedar-template-designer"])
            write_frontend_build(home, self.SOURCES, packages=["cedar-embeddable-editor"],
                                 applications=["cedar-template-designer"])
            self.assertEqual(([], []), self._findings(home))

            (components / "cedar-editors.js").write_bytes(b"// other editors")
            self._build_info(root, "cedar-template-designer", self.SOURCES["cedar-template-designer"])
            findings = self._findings(home)[0]
            self.assertEqual(1, len(findings))
            self.assertIn("serves cedar-embeddable-editor bytes other than its installed package's", findings[0])

            (components / "manifest.json").write_text(json.dumps(
                {"cedar-embeddable-editor": {"source": "local-override", "sha256": "0" * 64}}), encoding="utf-8")
            self._build_info(root, "cedar-template-designer", self.SOURCES["cedar-template-designer"])
            self.assertIn("from a local override", self._findings(home)[0][0])

    def test_a_frontend_inventory_that_cannot_be_read_is_a_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._workspace(home)
            (home / "cedar-development" / "ops" / "frontend-train.json").unlink()
            self.assertIn("frontend inventory cannot be read", self._findings(home)[0][0])


class KeycloakListenerTest(unittest.TestCase):
    """Keycloak loads the event listener from a jar copied outside every controller row."""

    HEAD = "c" * 40

    def _home(self, directory, *, commit=HEAD, installed=True, jar_epoch=2_000_000_000):
        home = Path(directory)
        (home / "cedar-keycloak-event-listener" / ".git").mkdir(parents=True)
        jar = home / "keycloak" / "providers" / "cedar-keycloak-event-listener.jar"
        if installed:
            stamped_jar(jar, {} if commit is None else {
                "cedar-keycloak-event-listener": ("cedar-keycloak-event-listener", commit)}, {"A.class": b"a"})
            os.utime(jar, (jar_epoch, jar_epoch))
        else:
            jar.parent.mkdir(parents=True)
        return home

    def _runner(self, started="Mon May 18 05:33:20 2033"):
        return FakeRunner([(["git", "rev-parse"], FakeResult(stdout=self.HEAD)),
                           (["lsof"], FakeResult(stdout="4242\n")),
                           (["env", "LC_ALL=C", "ps"], FakeResult(stdout=started))])

    def _findings(self, home, runner=None):
        with patch.dict(os.environ, {"CEDAR_KEYCLOAK_HOME": str(home / "keycloak")}):
            return smoke_gate.keycloak_listener_findings(home, runner or self._runner())

    def test_a_listener_stamped_with_develop_and_loaded_after_install_is_current(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual([], self._findings(self._home(directory)))

    def test_a_listener_from_another_commit_or_without_a_stamp_is_refused(self):
        for commit, expected in (("0" * 40, "built from cedar-keycloak-event-listener 00000000, not develop cccccccc"),
                                 (None, "built without a stamp of cedar-keycloak-event-listener")):
            with self.subTest(commit=commit), tempfile.TemporaryDirectory() as directory:
                findings = self._findings(self._home(directory, commit=commit))
                self.assertEqual(1, len(findings))
                self.assertIn(expected, findings[0])
                self.assertIn("cedarcli dev copy-keycloak-listener", findings[0])

    def test_a_keycloak_started_before_the_jar_was_installed_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            findings = self._findings(self._home(directory), self._runner(started="Thu Jan  1 00:00:00 2015"))
        self.assertEqual(["keycloak started before its cedar-keycloak-event-listener.jar was installed, so it runs "
                          "an earlier listener; restart Keycloak"], findings)

    def test_no_installed_listener_is_refused_and_no_checkout_is_not_judged(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIn("holds no cedar-keycloak-event-listener.jar",
                          self._findings(self._home(directory, installed=False))[0])
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual([], self._findings(Path(directory)))


class RunningCliTest(unittest.TestCase):
    def test_the_gate_must_run_from_the_cli_checkout_it_records(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            module = home / "cedar-cli" / "org" / "metadatacenter" / "smoke_gate.py"
            module.parent.mkdir(parents=True)
            module.write_text("", encoding="utf-8")
            heads = {"cedar-cli": "a" * 40}
            self.assertEqual([], smoke_gate.running_cli_findings(home, heads, module))
            elsewhere = home / "worktree" / "cedar-cli" / "org" / "metadatacenter" / "smoke_gate.py"
            elsewhere.parent.mkdir(parents=True)
            elsewhere.write_text("", encoding="utf-8")
            findings = smoke_gate.running_cli_findings(home, heads, elsewhere)
            self.assertEqual(1, len(findings))
            self.assertIn("not the cedar-cli checkout", findings[0])
            self.assertEqual([], smoke_gate.running_cli_findings(home, {}, elsewhere))
