import json
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock

import pytest
from requests import Response

from ape_sourcify.config import SourcifyConfig
from ape_sourcify.explorer import Sourcify
from ape_sourcify.types import VerifiedContract

ADDRESS = "0x00000000219ab540356cBB839Cbe05303d7705Fa"
TX_HASH = "0x" + "ab" * 32
VERIFICATION_ID = "72d12273-0723-448e-a9f6-f7957128efa5"


def make_response(
    status_code: int, data=None, *, text: str | None = None, headers=None
) -> Response:
    response = Response()
    response.status_code = status_code
    response.reason = "test response"
    response.url = "https://sourcify.dev/server/test"
    response.headers.update(headers or {})
    if text is not None:
        response._content = text.encode()
    else:
        response._content = json.dumps(data).encode()
        response.headers["Content-Type"] = "application/json"

    return response


class QueueSession:
    def __init__(self, responses: Iterable[Response | Exception]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result

        return result


@pytest.fixture
def contract_data():
    return {
        "match": "exact_match",
        "creationMatch": "exact_match",
        "runtimeMatch": "exact_match",
        "chainId": "1",
        "address": ADDRESS,
        "verifiedAt": "2024-08-08T10:05:44Z",
        "matchId": "2115",
        "abi": [
            {
                "type": "function",
                "name": "get_deposit_count",
                "inputs": [],
                "outputs": [{"name": "", "type": "bytes"}],
                "stateMutability": "view",
            }
        ],
        "compilation": {
            "language": "Solidity",
            "compiler": "solc",
            "compilerVersion": "0.6.11+commit.5ef660b1",
            "compilerSettings": {
                "optimizer": {"enabled": True, "runs": 5_000_000},
                "evmVersion": "istanbul",
            },
            "name": "DepositContract",
            "fullyQualifiedName": "deposit_contract.sol:DepositContract",
        },
        "sources": {
            "deposit_contract.sol": {
                "content": "pragma solidity 0.6.11; contract DepositContract {}\n"
            }
        },
        "stdJsonInput": {
            "language": "Solidity",
            "sources": {
                "deposit_contract.sol": {
                    "content": "pragma solidity 0.6.11; contract DepositContract {}\n"
                }
            },
            "settings": {"optimizer": {"enabled": True, "runs": 5_000_000}},
        },
        "creationBytecode": {"recompiledBytecode": "0x6000"},
        "runtimeBytecode": {"recompiledBytecode": "0x6001"},
        "userdoc": {"kind": "user"},
        "devdoc": {"kind": "dev"},
    }


@pytest.fixture
def verified_contract(contract_data):
    return VerifiedContract.model_validate(contract_data)


@pytest.fixture
def network():
    ecosystem = SimpleNamespace(name="ethereum")
    return SimpleNamespace(
        chain_id=1,
        choice="ethereum:mainnet",
        ecosystem=ecosystem,
        name="mainnet",
    )


@pytest.fixture
def explorer(mocker, network):
    mocker.patch.object(
        Sourcify,
        "config",
        new_callable=PropertyMock,
        return_value=SourcifyConfig(),
    )
    return Sourcify.model_construct(name="sourcify", network=network)


@pytest.fixture
def project_path(tmp_path: Path) -> Path:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "Foo.sol").write_text(
        "pragma solidity 0.8.30; import './Lib.sol'; contract Foo {}\n",
        encoding="utf8",
    )
    (contracts / "Lib.sol").write_text("pragma solidity 0.8.30; library Lib {}\n", encoding="utf8")
    return tmp_path
