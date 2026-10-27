import base64
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from org.metadatacenter.train_support import contracts
from org.metadatacenter.train_support.contracts import (
    ContractVerdict, contract_consumers, contract_verdict, dispatch_contracts,
)

CONSUMERS_WORKFLOW = """
    strategy:
      matrix:
        include:
          - repo: cedar-embeddable-editor
            directory: .
          - repo: cedar-workspace
            directory: .
"""

READERS_WORKFLOW = """
      - name: Check out every consumer
        run: |
          for repo in cedar-embeddable-editor cedar-embeddable-designer \\
              cedar-workspace; do
            git clone --quiet --branch develop "https://github.com/metadatacenter/$repo.git" "$repo"
          done
          git clone --quiet --branch main https://github.com/metadatacenter/cedar-cee-mcp.git mcp/cedar-cee-mcp
"""


class FakeGithub:
    """Answer `gh api` routes from a table, and record `gh workflow run` dispatches."""

    def __init__(self, routes, dispatch_code=0):
        self.routes = routes
        self.dispatched = []
        self.dispatch_code = dispatch_code

    def __call__(self, args, **_kwargs):
        if args[:3] == ["gh", "workflow", "run"]:
            self.dispatched.append(args)
            return SimpleNamespace(returncode=self.dispatch_code, stdout="", stderr="HTTP 403")
        route = args[2]
        if route not in self.routes:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"HTTP 404 {route}")
        return SimpleNamespace(returncode=0, stdout=json.dumps(self.routes[route]), stderr="")


def contract_routes(workflow_text, *, run=None, pushes=None):
    """Routes for one contract at develop `c…`, whose consumers were pushed at `pushes`."""
    head = "c" * 40
    routes = {
        "repos/metadatacenter/cedar-design-tokens/commits/develop": {"sha": head},
        f"repos/metadatacenter/cedar-design-tokens/contents/.github/workflows/consumers.yml?ref={head}":
            {"content": base64.b64encode(workflow_text.encode()).decode()},
        "repos/metadatacenter/cedar-design-tokens/actions/workflows/consumers.yml/runs"
        "?branch=develop&per_page=20": {"workflow_runs": [] if run is None else [dict({
            "head_sha": head, "event": "push", "status": "completed", "conclusion": "success",
            "created_at": "2026-10-27T10:00:00Z", "html_url": "https://github.example/runs/1"},
            **run)]},
    }
    for repository, pushed in (pushes or {}).items():
        revision = repository[-1] * 40
        routes[f"repos/metadatacenter/{repository}/commits/develop"] = {
            "sha": revision, "commit": {"committer": {"date": "2026-10-01T00:00:00Z"}}}
        routes[f"repos/metadatacenter/{repository}/actions/runs?head_sha={revision}&per_page=50"] = {
            "workflow_runs": [] if pushed is None else [
                {"event": "push", "created_at": pushed},
                {"event": "workflow_dispatch", "created_at": "2026-10-27T23:00:00Z"}]}
    return routes


class ContractConsumersTest(unittest.TestCase):
    def test_matrix_entries_loop_names_and_explicit_clones_are_consumers(self):
        self.assertEqual(
            [("cedar-embeddable-editor", "develop"), ("cedar-workspace", "develop")],
            contract_consumers(CONSUMERS_WORKFLOW))
        self.assertEqual(
            [("cedar-embeddable-editor", "develop"), ("cedar-embeddable-designer", "develop"),
             ("cedar-workspace", "develop"), ("cedar-cee-mcp", "main")],
            contract_consumers(READERS_WORKFLOW))


class ContractVerdictTest(unittest.TestCase):
    def verdict(self, routes):
        return contract_verdict("cedar-design-tokens", "consumers.yml",
                                runner=FakeGithub(routes), environment={})

    def test_a_run_after_every_consumer_push_covers_them(self):
        verdict = self.verdict(contract_routes(CONSUMERS_WORKFLOW, run={}, pushes={
            "cedar-embeddable-editor": "2026-10-27T09:00:00Z", "cedar-workspace": None}))
        self.assertEqual(("green", "consumers.yml covers all 2 consumers"),
                         (verdict.state, verdict.detail))
        self.assertFalse(verdict.blocks_a_train)

    def test_a_consumer_pushed_after_the_run_makes_it_stale(self):
        # A dispatch run at the consumer's head does not date its push; the push run does.
        verdict = self.verdict(contract_routes(CONSUMERS_WORKFLOW, run={}, pushes={
            "cedar-embeddable-editor": "2026-10-27T11:00:00Z",
            "cedar-workspace": "2026-10-27T09:00:00Z"}))
        self.assertEqual("stale", verdict.state)
        self.assertIn("before pushes to cedar-embeddable-editor rrrrrrrr", verdict.detail)
        self.assertNotIn("cedar-workspace", verdict.detail)
        self.assertTrue(verdict.dispatchable)

    def test_a_failure_before_a_consumer_push_is_stale_rather_than_red(self):
        # On 2026-10-27 the contract failed on consumers' old baselines; their pushes then fixed
        # it, and only a fresh run could say so.
        verdict = self.verdict(contract_routes(CONSUMERS_WORKFLOW, run={"conclusion": "failure"}, pushes={
            "cedar-embeddable-editor": "2026-10-27T11:00:00Z",
            "cedar-workspace": "2026-10-27T09:00:00Z"}))
        self.assertEqual("stale", verdict.state)
        self.assertIn("concluded failure at 2026-10-27T10:00:00Z, before pushes to "
                      "cedar-embeddable-editor rrrrrrrr", verdict.detail)
        self.assertTrue(verdict.dispatchable)
        self.assertTrue(verdict.blocks_a_train)

    def test_red_pending_missing_and_unreadable_runs_block(self):
        pushes = {"cedar-embeddable-editor": None, "cedar-workspace": None}
        for run, state in (({"conclusion": "failure"}, "red"),
                           ({"status": "in_progress", "conclusion": None}, "pending"),
                           (None, "missing"),
                           ({"conclusion": "cancelled"}, "missing")):
            with self.subTest(state=state, run=run):
                verdict = self.verdict(contract_routes(CONSUMERS_WORKFLOW, run=run, pushes=pushes))
                self.assertEqual(state, verdict.state)
                self.assertTrue(verdict.blocks_a_train)
        routes = contract_routes(CONSUMERS_WORKFLOW, run={}, pushes={})
        verdict = self.verdict(routes)  # consumers' commits are unreadable
        self.assertEqual("error", verdict.state)


class ContractDispatchTest(unittest.TestCase):
    def test_only_contracts_no_run_covers_are_dispatched(self):
        verdicts = [ContractVerdict("cedar-design-tokens", "consumers.yml", "stale", "s"),
                    ContractVerdict("cedar-design-tokens", "token-readers.yml", "missing", "m"),
                    ContractVerdict("cedar-x", "red.yml", "red", "r")]
        github = FakeGithub({}, dispatch_code=1)
        lines = dispatch_contracts(verdicts, runner=github, environment={})
        self.assertEqual(
            [["gh", "workflow", "run", "consumers.yml", "--repo",
              "metadatacenter/cedar-design-tokens", "--ref", "develop"],
             ["gh", "workflow", "run", "token-readers.yml", "--repo",
              "metadatacenter/cedar-design-tokens", "--ref", "develop"]],
            github.dispatched)
        self.assertEqual("cedar-design-tokens consumers.yml: not dispatched: HTTP 403", lines[0])


class ContractGateTest(unittest.TestCase):
    stale = ContractVerdict("cedar-design-tokens", "consumers.yml", "stale",
                            "consumers.yml last ran T, before pushes to cedar-x 11111111",
                            "https://github.example/runs/1")

    def test_the_train_preflight_refuses_a_stale_contract(self):
        from org.metadatacenter.train_support import preflight
        with patch.object(contracts, "contract_survey", return_value=[self.stale]):
            with self.assertRaisesRegex(ValueError, "before pushes to cedar-x") as refused:
                preflight._contract_preflight()
        self.assertIn("cedarcli check ci --dispatch-stale-contracts", str(refused.exception))
        with patch.object(contracts, "contract_survey", return_value=[]):
            preflight._contract_preflight()

    def test_check_ci_reports_and_dispatches_a_stale_contract(self):
        from rich.console import Console
        from org.metadatacenter.worker.BuildTrainWorker import BuildTrainWorker
        buffer = io.StringIO()
        with (
            patch("org.metadatacenter.train_support.survey.source_ci_survey", return_value=[]),
            patch.object(contracts, "contract_survey", return_value=[self.stale]),
            patch.object(contracts, "dispatch_contracts",
                         return_value=["cedar-design-tokens consumers.yml: dispatched"]) as dispatch,
            patch("org.metadatacenter.train_support.output.console",
                  Console(file=buffer, width=200, force_terminal=False)),
        ):
            code = BuildTrainWorker.report_source_ci(dispatch_stale_contracts=True)
        output = buffer.getvalue()
        self.assertEqual(1, code)
        dispatch.assert_called_once_with([self.stale])
        self.assertIn("Cross-repository contracts at develop", output)
        self.assertIn("cedar-design-tokens consumers.yml: dispatched", output)
        self.assertIn("1 contract(s) do not cover their consumers", output)
