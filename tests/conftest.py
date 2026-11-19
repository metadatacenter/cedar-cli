import os
from pathlib import Path

import pytest

# The directory this checkout sits in, as CI's CEDAR_HOME is the workspace that holds cedar-cli
# beside the sibling checkouts some tests read.
CHECKOUT_PARENT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def cedar_home_as_ci_sets_it(monkeypatch):
    """Give a run without CEDAR_HOME the layout CI gives every run.

    Build safety, the CEE demo gate and the in-place payload build read CEDAR_HOME, so without
    this they failed outside CI only because the caller's shell had not exported it.
    """
    if not os.environ.get("CEDAR_HOME"):
        monkeypatch.setenv("CEDAR_HOME", str(CHECKOUT_PARENT))


@pytest.fixture(autouse=True)
def no_cross_repository_contract_reads(monkeypatch):
    """Answer the contract survey with no contracts unless a test supplies its own.

    The survey reads GitHub for every contract and consumer. `check ci` and the train preflight
    both ask it, so a test of either would otherwise depend on the network and on whatever the
    contracts' newest runs happen to be.
    """
    monkeypatch.setattr("org.metadatacenter.train_support.contracts.contract_survey",
                        lambda **_kwargs: [])


@pytest.fixture(autouse=True)
def no_github_job_reads(monkeypatch):
    """Answer GitHub's job listing as unreadable unless a test supplies its own runner.

    A gate that meets a red run asks GitHub for its jobs, to tell a run GitHub failed to start from
    one whose code failed. The surveys behind `check ci` and the train preflight ask with the real
    GitHub CLI, so a test of either reached GitHub with the caller's login. An unreadable listing
    keeps the verdict GitHub gave the run, which is what these tests expect of a red run.
    """
    import subprocess
    from org.metadatacenter.local_ci import gates
    original = gates.github_failed_to_start

    def offline(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "GitHub is not consulted in tests")

    def github_failed_to_start(repository, run, *, runner=None, environment=None):
        return original(repository, run, runner=runner or offline, environment=environment)

    monkeypatch.setattr(gates, "github_failed_to_start", github_failed_to_start)
