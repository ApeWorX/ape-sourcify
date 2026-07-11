import pytest

from ape_sourcify.client import SourcifyClient
from ape_sourcify.networks import KNOWN_SUPPORTED_CHAIN_IDS

from .conftest import ADDRESS


@pytest.mark.live
def test_production_contract_lookup():
    contract = SourcifyClient().get_contract(
        1,
        ADDRESS,
        fields=("abi", "compilation", "sources"),
    )

    assert contract is not None
    assert contract.match in ("match", "exact_match")
    assert contract.compilation is not None
    assert contract.compilation.name == "DepositContract"
    assert "deposit_contract.sol" in contract.sources


@pytest.mark.live
def test_production_chain_catalog():
    client = SourcifyClient()
    assert client.supports_chain(1) is True
    assert client.supports_chain(5) is False
    assert client.get_supported_chain_ids() >= KNOWN_SUPPORTED_CHAIN_IDS
