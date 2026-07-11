from types import SimpleNamespace

import pytest
from ethpm_types import Compiler, ContractType, PackageManifest
from ethpm_types.source import Source

from ape_sourcify.exceptions import (
    ContractAlreadyVerifiedError,
    ContractVerificationError,
    SourcifyAPIError,
    SourcifyTimeoutError,
)
from ape_sourcify.types import VerificationJob
from ape_sourcify.verify import SourceVerifier

from .conftest import ADDRESS, TX_HASH, VERIFICATION_ID


class IsolatedVerifier(SourceVerifier):
    def __init__(self, *args, project, compiler_manager, **kwargs):
        self._project = project
        self._compiler_manager = compiler_manager
        super().__init__(*args, **kwargs)

    @property
    def local_project(self):
        return self._project

    @property
    def compiler_manager(self):
        return self._compiler_manager


@pytest.fixture
def verifier_factory(project_path):
    def make(
        client,
        *,
        settings=None,
        imports=None,
        has_compiler=True,
        timeout=30,
        clock=lambda: 0.0,
        sleep=lambda _: None,
    ):
        source_id = "contracts/Foo.sol"
        contract_type = ContractType(contractName="Foo", sourceId=source_id, abi=[])
        compiler_settings = (
            {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {
                    "contracts/Foo.sol": {"*": ["abi"]},
                    "contracts/Lib.sol": {"*": ["abi"]},
                },
            }
            if settings is None
            else settings
        )
        compiler = Compiler(
            name="solidity",
            version="0.8.30+commit.73712a01",
            settings=compiler_settings,
            contractTypes=["Foo"],
        )
        manifest = PackageManifest(
            sources={
                "contracts/Foo.sol": Source(
                    content=(project_path / "contracts" / "Foo.sol").read_text()
                ),
                "contracts/Lib.sol": Source(
                    content=(project_path / "contracts" / "Lib.sol").read_text()
                ),
            },
            contractTypes={"Foo": contract_type},
            compilers=[compiler],
        )
        project = SimpleNamespace(
            dependencies=[],
            extract_manifest=lambda: manifest,
            manifest=manifest,
            path=project_path,
        )
        import_map = imports or {
            "contracts/Foo.sol": ["contracts/Lib.sol"],
            "contracts/Lib.sol": [],
        }
        compiler_api = SimpleNamespace(get_imports=lambda *_args, **_kwargs: import_map)
        compiler_manager = SimpleNamespace(
            registered_compilers={".sol": compiler_api} if has_compiler else {}
        )
        verifier = IsolatedVerifier(
            ADDRESS,
            1,
            client,
            "https://repo.sourcify.dev",
            project=project,
            compiler_manager=compiler_manager,
            verification_timeout=timeout,
            poll_interval=0.01,
            clock=clock,
            sleep=sleep,
        )
        verifier.__dict__["contract_type"] = contract_type
        verifier.__dict__["manifest"] = manifest
        verifier.__dict__["compiler"] = compiler
        verifier.__dict__["creation_transaction_hash"] = TX_HASH
        return verifier

    return make


def test_build_standard_json_input(verifier_factory, mocker):
    verifier = verifier_factory(mocker.MagicMock())
    result = verifier.build_standard_json_input()

    assert result["language"] == "Solidity"
    assert set(result["sources"]) == {"contracts/Foo.sol", "contracts/Lib.sol"}
    assert result["settings"]["optimizer"] == {"enabled": True, "runs": 200}
    assert set(result["settings"]["outputSelection"]) == {
        "contracts/Foo.sol",
        "contracts/Lib.sol",
    }
    assert verifier.contract_identifier == "contracts/Foo.sol:Foo"
    assert verifier.compiler_version == "0.8.30+commit.73712a01"


def test_build_vyper_input_moves_storage_overrides(verifier_factory, mocker):
    verifier = verifier_factory(
        mocker.MagicMock(),
        settings={
            "optimize": "gas",
            "search_paths": ["/private/host/path"],
            "outputSelection": {"contracts/Foo.sol": ["abi"]},
            "storage_layout_overrides": {"Foo.vy": {"a": {"slot": 0}}},
        },
    )
    verifier.__dict__["compiler"] = Compiler(
        name="vyper",
        version="0.4.1+commit.8a93dd27",
        settings=verifier.__dict__["compiler"].settings,
        contractTypes=["Foo"],
    )
    result = verifier.build_standard_json_input()

    assert result["language"] == "Vyper"
    assert "search_paths" not in result["settings"]
    assert result["settings"]["outputSelection"] == {"contracts/Foo.sol": ["abi"]}
    assert result["storage_layout_overrides"] == {"Foo.vy": {"a": {"slot": 0}}}


def test_rejects_short_solidity_version(verifier_factory, mocker):
    verifier = verifier_factory(mocker.MagicMock())
    verifier.__dict__["compiler"] = Compiler(
        name="solidity", version="0.8.30", settings={}, contractTypes=["Foo"]
    )
    with pytest.raises(ContractVerificationError, match="full Solidity compiler version"):
        _ = verifier.compiler_version


def test_rejects_unsafe_source_path(verifier_factory, mocker):
    verifier = verifier_factory(
        mocker.MagicMock(),
        imports={"contracts/Foo.sol": ["../../secret.txt"]},
    )
    with pytest.raises(ContractVerificationError, match="unsafe source path"):
        verifier.build_standard_json_input()


def test_ignores_unrelated_output_selection(verifier_factory, mocker):
    verifier = verifier_factory(
        mocker.MagicMock(),
        settings={
            "outputSelection": {
                "contracts/Foo.sol": {"*": ["abi"]},
                "contracts/Unrelated.sol": {"*": ["abi"]},
            }
        },
    )

    result = verifier.build_standard_json_input()

    assert set(result["sources"]) == {"contracts/Foo.sol", "contracts/Lib.sol"}
    assert "contracts/Unrelated.sol" not in str(result)


def test_rejects_source_symlink_outside_project(verifier_factory, mocker, project_path):
    secret = project_path.parent / f"{project_path.name}-secret.sol"
    secret.write_text("secret", encoding="utf8")
    (project_path / "contracts" / "Secret.sol").symlink_to(secret)
    verifier = verifier_factory(
        mocker.MagicMock(),
        imports={"contracts/Foo.sol": ["contracts/Secret.sol"]},
    )

    with pytest.raises(ContractVerificationError, match="resolves outside"):
        verifier.build_standard_json_input()


def test_prefers_exact_local_source_content(verifier_factory, mocker, project_path):
    source = project_path / "contracts" / "Foo.sol"
    source.write_text("contract Foo {}", encoding="utf8")
    verifier = verifier_factory(mocker.MagicMock())

    result = verifier.build_standard_json_input()

    assert result["sources"]["contracts/Foo.sol"]["content"] == "contract Foo {}"


def test_fails_closed_without_import_resolver(verifier_factory, mocker):
    verifier = verifier_factory(mocker.MagicMock(), has_compiler=False)

    with pytest.raises(ContractVerificationError, match="source import graph"):
        verifier.build_standard_json_input()


def test_ignores_vyper_compiler_builtin_imports(verifier_factory, mocker):
    verifier = verifier_factory(
        mocker.MagicMock(),
        imports={
            "contracts/Foo.sol": [
                "contracts/Lib.sol",
                "vyper/interfaces/ERC20.json",
                "ethereum/ercs/IERC20.json",
            ]
        },
    )
    verifier.__dict__["compiler"] = Compiler(
        name="vyper",
        version="0.3.10+commit.91361694",
        settings=verifier.__dict__["compiler"].settings,
        contractTypes=["Foo"],
    )

    result = verifier.build_standard_json_input()

    assert set(result["sources"]) == {"contracts/Foo.sol", "contracts/Lib.sol"}
    assert "vyper/interfaces" not in str(result)
    assert "ethereum/ercs" not in str(result)


def test_explains_empty_source_limitation(verifier_factory, mocker, project_path):
    (project_path / "contracts" / "Empty.sol").write_text("", encoding="utf8")
    verifier = verifier_factory(
        mocker.MagicMock(),
        imports={"contracts/Foo.sol": ["contracts/Empty.sol"]},
    )

    with pytest.raises(ContractVerificationError, match="does not accept empty source"):
        verifier.build_standard_json_input()


def test_publish_success_after_pending(verifier_factory, mocker):
    client = mocker.MagicMock()
    client.submit_verification.return_value = VERIFICATION_ID
    client.get_verification_job.side_effect = [
        VerificationJob(isJobCompleted=False, verificationId=VERIFICATION_ID),
        VerificationJob(
            isJobCompleted=True,
            verificationId=VERIFICATION_ID,
            contract={
                "match": "match",
                "creationMatch": "match",
                "runtimeMatch": None,
                "chainId": "1",
                "address": ADDRESS,
            },
        ),
    ]
    sleeps = []
    verifier = verifier_factory(client, sleep=sleeps.append)

    job = verifier.attempt_verification()

    assert job is not None
    assert job.contract is not None
    assert job.contract.match == "match"
    payload = client.submit_verification.call_args.kwargs
    assert payload["creation_transaction_hash"] == TX_HASH
    assert payload["contract_identifier"] == "contracts/Foo.sol:Foo"
    assert sleeps == [0.01]


def test_publish_already_verified_is_idempotent(verifier_factory, mocker):
    client = mocker.MagicMock()
    client.submit_verification.side_effect = ContractAlreadyVerifiedError(
        "Already verified", status_code=409, custom_code="already_verified"
    )
    verifier = verifier_factory(client)
    assert verifier.attempt_verification() is None


def test_publish_waits_for_duplicate_job(verifier_factory, mocker):
    client = mocker.MagicMock()
    duplicate = SourcifyAPIError(
        "Already running",
        status_code=429,
        custom_code="duplicate_verification_request",
    )
    client.submit_verification.side_effect = [
        duplicate,
        ContractAlreadyVerifiedError(
            "Already verified", status_code=409, custom_code="already_verified"
        ),
    ]
    sleeps = []
    verifier = verifier_factory(client, sleep=sleeps.append)

    assert verifier.attempt_verification() is None
    assert client.submit_verification.call_count == 2
    assert sleeps == [0.01]


def test_publish_terminal_failure(verifier_factory, mocker):
    client = mocker.MagicMock()
    client.submit_verification.return_value = VERIFICATION_ID
    client.get_verification_job.return_value = VerificationJob(
        isJobCompleted=True,
        verificationId=VERIFICATION_ID,
        contract={
            "match": None,
            "creationMatch": None,
            "runtimeMatch": None,
            "chainId": "1",
            "address": ADDRESS,
        },
        error={
            "customCode": "no_match",
            "message": "Bytecodes do not match",
            "errorId": VERIFICATION_ID,
        },
    )
    verifier = verifier_factory(client)

    with pytest.raises(ContractVerificationError, match="no_match") as err:
        verifier.attempt_verification()

    assert err.value.error_id == VERIFICATION_ID


def test_publish_timeout(verifier_factory, mocker):
    times = iter((0.0, 2.0))
    client = mocker.MagicMock()
    client.submit_verification.return_value = VERIFICATION_ID
    verifier = verifier_factory(client, timeout=1, clock=lambda: next(times))
    with pytest.raises(SourcifyTimeoutError, match="did not finish"):
        verifier.attempt_verification()
