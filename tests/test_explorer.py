from types import SimpleNamespace
from unittest.mock import PropertyMock

import pytest
from ape.exceptions import APINotImplementedError
from ape.types import ContractType

from ape_sourcify import NETWORKS, Sourcify, SourcifyConfig
from ape_sourcify.exceptions import SourcifyAPIError
from ape_sourcify.networks import KNOWN_SUPPORTED_CHAIN_IDS, NETWORK_CHAIN_IDS

from .conftest import ADDRESS, TX_HASH


def test_get_address_url(explorer):
    assert explorer.get_address_url(ADDRESS) == f"https://repo.sourcify.dev/1/{ADDRESS}"


def test_get_transaction_url_uses_chain_explorer(explorer):
    assert explorer.get_transaction_url(TX_HASH) == f"https://etherscan.io/tx/{TX_HASH}"


def test_get_transaction_url_unknown_chain(mocker):
    network = SimpleNamespace(
        chain_id=999_123,
        choice="custom:test",
        ecosystem=SimpleNamespace(name="custom"),
        name="test",
    )
    mocker.patch.object(
        Sourcify,
        "config",
        new_callable=PropertyMock,
        return_value=SourcifyConfig(),
    )
    explorer = Sourcify.model_construct(name="sourcify", network=network)
    with pytest.raises(APINotImplementedError, match="No transaction explorer"):
        explorer.get_transaction_url(TX_HASH)


def test_get_contract_type(mocker, explorer, verified_contract):
    client = mocker.MagicMock()
    client.get_contract.return_value = verified_contract
    mocker.patch.object(Sourcify, "client", new_callable=PropertyMock, return_value=client)

    contract_type = explorer.get_contract_type(ADDRESS.lower())

    assert isinstance(contract_type, ContractType)
    assert contract_type.name == "DepositContract"
    assert contract_type.source_id == "deposit_contract.sol"
    assert contract_type.deployment_bytecode.bytecode == "0x6000"
    assert contract_type.runtime_bytecode.bytecode == "0x6001"
    assert "get_deposit_count" in contract_type.view_methods
    client.get_contract.assert_called_once()


def test_get_contract_type_unverified(mocker, explorer):
    client = mocker.MagicMock()
    client.get_contract.return_value = None
    mocker.patch.object(Sourcify, "client", new_callable=PropertyMock, return_value=client)
    assert explorer.get_contract_type(ADDRESS) is None


def test_get_manifest_preserves_sources_and_settings(mocker, explorer, verified_contract):
    client = mocker.MagicMock()
    client.get_contract.return_value = verified_contract
    mocker.patch.object(Sourcify, "client", new_callable=PropertyMock, return_value=client)

    manifest = explorer.get_manifest(ADDRESS)

    assert manifest is not None
    assert manifest.contract_types is not None
    assert manifest.contract_types["DepositContract"].source_id == "deposit_contract.sol"
    assert "pragma solidity" in str(manifest.sources["deposit_contract.sol"].content)
    compiler = manifest.get_contract_compiler("DepositContract")
    assert compiler is not None
    assert compiler.version == "0.6.11+commit.5ef660b1"
    assert compiler.settings == {"optimizer": {"enabled": True, "runs": 5_000_000}}


def test_get_manifest_preserves_vyper_storage_overrides(mocker, explorer, verified_contract):
    contract = verified_contract.model_copy(deep=True)
    contract.std_json_input["storage_layout_overrides"] = {"Foo.vy": {"owner": {"slot": 0}}}
    client = mocker.MagicMock()
    client.get_contract.return_value = contract
    mocker.patch.object(Sourcify, "client", new_callable=PropertyMock, return_value=client)

    manifest = explorer.get_manifest(ADDRESS)

    assert manifest is not None
    compiler = manifest.get_contract_compiler("DepositContract")
    assert compiler is not None
    assert compiler.settings["storage_layout_overrides"] == {"Foo.vy": {"owner": {"slot": 0}}}


def test_supports_chain_dynamic(mocker):
    client = mocker.MagicMock()
    client.supports_chain.side_effect = lambda chain_id: chain_id == 1
    mocker.patch.object(Sourcify, "_chain_client", client)

    assert Sourcify.supports_chain(1) is True
    assert Sourcify.supports_chain(5) is False


def test_supports_chain_offline_fallback(mocker):
    client = mocker.MagicMock()
    client.supports_chain.side_effect = SourcifyAPIError("offline")
    mocker.patch.object(Sourcify, "_chain_client", client)

    assert Sourcify.supports_chain(1) is True
    assert Sourcify.supports_chain(5) is False


def test_supports_chain_uses_configured_server(mocker):
    config = SourcifyConfig(
        api_url="https://staging.sourcify.dev/server",
        request_timeout=12,
        retries=1,
        retry_backoff=0.25,
        chain_cache_ttl=60,
    )
    mocker.patch.object(Sourcify, "_load_config", return_value=config)
    client_type = mocker.patch("ape_sourcify.explorer.SourcifyClient")
    client_type.return_value.supports_chain.return_value = True
    Sourcify._chain_clients.clear()

    assert Sourcify.supports_chain(123_456) is True
    client_type.assert_called_once_with(
        "https://staging.sourcify.dev/server",
        timeout=12,
        retries=1,
        retry_backoff=0.25,
        chain_cache_ttl=60,
    )


def test_registration_matrix():
    from ape_sourcify import explorers

    registrations = list(explorers())
    assert ("ethereum", "mainnet", Sourcify) in registrations
    assert ("ethereum", "mainnet-fork", Sourcify) in registrations
    assert ("hyperliquid", "mainnet", Sourcify) in registrations
    assert ("hyperliquid", "sepolia-fork", Sourcify) in registrations
    assert len(registrations) == 2 * sum(len(networks) for networks in NETWORKS.values())
    registered_chain_ids = {
        chain_id for networks in NETWORK_CHAIN_IDS.values() for chain_id in networks.values()
    }
    assert registered_chain_ids == KNOWN_SUPPORTED_CHAIN_IDS


def test_config_defaults():
    config = SourcifyConfig()
    assert str(config.api_url) == "https://sourcify.dev/server"
    assert str(config.repository_url) == "https://repo.sourcify.dev/"
    assert config.verification_timeout == 300
