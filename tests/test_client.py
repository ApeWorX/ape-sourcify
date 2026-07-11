import pytest
import requests

from ape_sourcify.client import SourcifyClient
from ape_sourcify.exceptions import (
    ContractAlreadyVerifiedError,
    SourcifyAPIError,
    SourcifyResponseError,
    SourcifyTimeoutError,
)

from .conftest import ADDRESS, TX_HASH, VERIFICATION_ID, QueueSession, make_response


def test_get_chains_filters_and_caches():
    now = [10.0]
    session = QueueSession(
        [
            make_response(
                200,
                [
                    {"name": "Ethereum Mainnet", "chainId": 1, "supported": True, "rpc": []},
                    {"name": "Goerli", "chainId": 5, "supported": False, "rpc": []},
                ],
            )
        ]
    )
    client = SourcifyClient(session=session, clock=lambda: now[0])

    assert client.get_supported_chain_ids() == frozenset({1})
    assert client.supports_chain(1) is True
    assert client.supports_chain(5) is False
    assert len(session.calls) == 1


def test_get_contract(contract_data):
    session = QueueSession([make_response(200, contract_data)])
    client = SourcifyClient(session=session)

    contract = client.get_contract(1, ADDRESS, fields=("abi", "compilation"))

    assert contract is not None
    assert contract.compilation is not None
    assert contract.compilation.name == "DepositContract"
    assert session.calls[0][2]["params"] == {"fields": "abi,compilation"}


def test_get_contract_unverified_404():
    session = QueueSession([make_response(404, {"match": None})])
    client = SourcifyClient(session=session)
    assert client.get_contract(1, ADDRESS) is None


def test_api_error_preserves_envelope():
    session = QueueSession(
        [
            make_response(
                400,
                {
                    "customCode": "unsupported_chain",
                    "message": "Chain not found",
                    "errorId": VERIFICATION_ID,
                },
            )
        ]
    )
    client = SourcifyClient(session=session)

    with pytest.raises(SourcifyAPIError, match="unsupported_chain") as err:
        client.get_chains()

    assert err.value.status_code == 400
    assert err.value.error_id == VERIFICATION_ID


def test_malformed_json():
    session = QueueSession([make_response(200, text="not JSON")])
    client = SourcifyClient(session=session)
    with pytest.raises(SourcifyResponseError, match="Expected a JSON response"):
        client.get_chains()


def test_retries_rate_limit_and_honors_retry_after():
    sleeps = []
    session = QueueSession(
        [
            make_response(429, {"message": "slow down"}, headers={"Retry-After": "2"}),
            make_response(
                200,
                [{"name": "Ethereum", "chainId": 1, "supported": True, "rpc": []}],
            ),
        ]
    )
    client = SourcifyClient(session=session, retries=1, sleep=sleeps.append)

    assert client.supports_chain(1) is True
    assert sleeps == [2.0]
    assert len(session.calls) == 2


def test_duplicate_verification_is_not_transport_retried():
    session = QueueSession(
        [
            make_response(
                429,
                {
                    "customCode": "duplicate_verification_request",
                    "message": "Already running",
                    "errorId": VERIFICATION_ID,
                },
            )
        ]
    )
    client = SourcifyClient(session=session, retries=3, sleep=lambda _: None)

    with pytest.raises(SourcifyAPIError) as err:
        client.submit_verification(
            1,
            ADDRESS,
            std_json_input={"language": "Solidity", "sources": {}, "settings": {}},
            compiler_version="0.8.30+commit.73712a01",
            contract_identifier="Foo.sol:Foo",
        )

    assert err.value.custom_code == "duplicate_verification_request"
    assert len(session.calls) == 1


def test_submit_verification_payload_normalizes_values():
    session = QueueSession([make_response(202, {"verificationId": VERIFICATION_ID})])
    client = SourcifyClient(session=session)
    standard_json = {
        "language": "Solidity",
        "sources": {"Foo.sol": {"content": "contract Foo {}"}},
        "settings": {},
    }

    result = client.submit_verification(
        1,
        ADDRESS,
        std_json_input=standard_json,
        compiler_version="v0.8.30+commit.73712a01",
        contract_identifier="Foo.sol:Foo",
        creation_transaction_hash=TX_HASH.upper().replace("0X", "0x"),
    )

    assert result == VERIFICATION_ID
    payload = session.calls[0][2]["json"]
    assert payload["compilerVersion"] == "0.8.30+commit.73712a01"
    assert payload["creationTransactionHash"] == TX_HASH


def test_submit_already_verified():
    session = QueueSession(
        [
            make_response(
                409,
                {"customCode": "already_verified", "message": "Already verified"},
            )
        ]
    )
    client = SourcifyClient(session=session)
    with pytest.raises(ContractAlreadyVerifiedError):
        client.submit_verification(
            1,
            ADDRESS,
            std_json_input={},
            compiler_version="0.8.30+commit.73712a01",
            contract_identifier="Foo.sol:Foo",
        )


def test_get_verification_job_one_sided_match():
    session = QueueSession(
        [
            make_response(
                200,
                {
                    "isJobCompleted": True,
                    "verificationId": VERIFICATION_ID,
                    "contract": {
                        "match": "match",
                        "creationMatch": "match",
                        "runtimeMatch": None,
                        "chainId": "1",
                        "address": ADDRESS,
                    },
                },
            )
        ]
    )
    job = SourcifyClient(session=session).get_verification_job(VERIFICATION_ID)
    assert job.contract is not None
    assert job.contract.match == "match"
    assert job.contract.runtime_match is None


def test_timeout_retries_then_raises():
    session = QueueSession([requests.Timeout(), requests.Timeout()])
    client = SourcifyClient(session=session, retries=1, retry_backoff=0, sleep=lambda _: None)
    with pytest.raises(SourcifyTimeoutError, match="2 attempt"):
        client.get_chains()


def test_invalid_contract_shape_raises_response_error():
    session = QueueSession([make_response(200, {"abi": []})])
    client = SourcifyClient(session=session)
    with pytest.raises(SourcifyResponseError, match="Invalid verified-contract"):
        client.get_contract(1, ADDRESS)
