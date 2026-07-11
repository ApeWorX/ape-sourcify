from unittest.mock import PropertyMock

import pytest
import yaml
from ape.exceptions import ProjectError
from ethpm_types import Compiler, ContractType, PackageManifest
from ethpm_types.source import Source

from ape_sourcify.dependency import SourcifyDependency

from .conftest import ADDRESS


def test_dependency_version_id():
    dependency = SourcifyDependency(
        name="deposit-contract",
        sourcify=ADDRESS,
        ecosystem="ethereum",
        network="mainnet",
    )
    assert dependency.version_id == "ethereum_mainnet"
    assert dependency.package_id == f"sourcify/{ADDRESS.lower()}"


def test_dependency_validates_identifiers():
    with pytest.raises(ValueError, match="20-byte"):
        SourcifyDependency(name="bad", sourcify="not-an-address")

    with pytest.raises(ValueError, match="letters"):
        SourcifyDependency(name="bad", sourcify=ADDRESS, network="../mainnet")


def test_dependency_preserves_yaml_integer_address():
    dependency = SourcifyDependency(name="deposit-contract", sourcify=int(ADDRESS, 16))
    assert dependency.sourcify == ADDRESS.lower()


def test_fetch_preserves_manifest_artifacts(mocker, tmp_path):
    dependency = SourcifyDependency(name="deposit-contract", sourcify=ADDRESS)
    contract_type = ContractType(contractName="Foo", sourceId="contracts/Foo.sol", abi=[])
    manifest = PackageManifest(
        sources={
            "contracts/Foo.sol": Source(content='import "@lib/Lib.sol"; contract Foo {}'),
            "lib/Lib.sol": Source(content="library Lib {}"),
        },
        contractTypes={"Foo": contract_type},
        compilers=[
            Compiler(
                name="solidity",
                version="0.8.30+commit.73712a01",
                settings={
                    "optimizer": {"enabled": True, "runs": 200},
                    "remappings": ["@lib/=lib/"],
                },
                contractTypes=["Foo"],
            )
        ],
    )
    mocker.patch.object(SourcifyDependency, "extract_manifest", return_value=manifest)
    manifest_cache_path = tmp_path / "manifests" / "dependency.json"
    mocker.patch.object(
        SourcifyDependency,
        "manifest_cache_path",
        new_callable=PropertyMock,
        return_value=manifest_cache_path,
    )

    destination = tmp_path / "dependency"
    dependency.fetch(destination)

    assert (destination / "contracts" / "Foo.sol").is_file()
    assert (destination / "lib" / "Lib.sol").is_file()
    config = yaml.safe_load((destination / "ape-config.yaml").read_text(encoding="utf8"))
    assert config["contracts_folder"] == "."
    assert config["compile"]["exclude"] == ["lib/Lib.sol"]
    assert config["dependencies"] == [{"local": "lib", "name": "lib"}]
    assert config["solidity"]["import_remapping"] == ["@lib=__sourcify__/remappings/lib"]
    alias_config = yaml.safe_load(
        (destination / "lib" / "ape-config.yaml").read_text(encoding="utf8")
    )
    assert alias_config["contracts_folder"] == "."

    saved = PackageManifest.model_validate_json(manifest_cache_path.read_text(encoding="utf8"))
    assert saved.contract_types == manifest.contract_types
    assert saved.compilers == manifest.compilers
    assert saved.sources == manifest.sources


def test_fetch_rejects_path_traversal(mocker, tmp_path):
    dependency = SourcifyDependency(name="deposit-contract", sourcify=ADDRESS)
    manifest = PackageManifest(sources={"../escape.sol": Source(content="secret")})
    mocker.patch.object(SourcifyDependency, "extract_manifest", return_value=manifest)

    with pytest.raises(ProjectError, match="Unsafe source path"):
        dependency.fetch(destination := tmp_path / "dependency")

    assert not (tmp_path / "escape.sol").exists()
    assert not destination.exists()


def test_fetch_rejects_project_config_collision(mocker, tmp_path):
    dependency = SourcifyDependency(name="deposit-contract", sourcify=ADDRESS)
    manifest = PackageManifest(sources={"lib/ape-config.yaml": Source(content="malicious config")})
    mocker.patch.object(SourcifyDependency, "extract_manifest", return_value=manifest)

    with pytest.raises(ProjectError, match="Unsafe source path"):
        dependency.fetch(destination := tmp_path / "dependency")

    assert not destination.exists()
