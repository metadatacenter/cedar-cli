import pytest


@pytest.fixture(autouse=True)
def no_cross_repository_contract_reads(monkeypatch):
    """Answer the contract survey with no contracts unless a test supplies its own.

    The survey reads GitHub for every contract and consumer. `check ci` and the train preflight
    both ask it, so a test of either would otherwise depend on the network and on whatever the
    contracts' newest runs happen to be.
    """
    monkeypatch.setattr("org.metadatacenter.train_support.contracts.contract_survey",
                        lambda **_kwargs: [])
