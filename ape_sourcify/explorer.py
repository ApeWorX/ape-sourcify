import copy
from functools import cached_property
from typing import ClassVar

from ape.api import ExplorerAPI
from ape.exceptions import APINotImplementedError, NetworkError
from ape.types import AddressType, ContractType
from ethpm_types import Compiler, PackageManifest
from ethpm_types.source import Source

from ape_sourcify.client import SourcifyClient
from ape_sourcify.config import SourcifyConfig
from ape_sourcify.exceptions import SourcifyError
from ape_sourcify.networks import (
    KNOWN_SUPPORTED_CHAIN_IDS,
    get_block_explorer_url,
    get_chain_metadata,
)
from ape_sourcify.types import VerifiedContract
from ape_sourcify.verify import SourceVerifier

_CONTRACT_TYPE_FIELDS = (
    "abi",
    "compilation",
    "creationBytecode.recompiledBytecode",
    "runtimeBytecode.recompiledBytecode",
    "userdoc",
    "devdoc",
)
_MANIFEST_FIELDS = (*_CONTRACT_TYPE_FIELDS, "sources", "stdJsonInput", "metadata")


class Sourcify(ExplorerAPI):
    """Ape explorer backed by Sourcify's source-verification repository."""

    _chain_client: ClassVar[SourcifyClient | None] = None
    _chain_clients: ClassVar[dict[tuple[str, float, int, float, float], SourcifyClient]] = {}

    @property
    def config(self) -> SourcifyConfig:
        return self._load_config()

    @classmethod
    def _load_config(cls) -> SourcifyConfig:
        config = cls.config_manager.get_config("sourcify")
        if isinstance(config, SourcifyConfig):
            return config

        data = config.model_dump() if hasattr(config, "model_dump") else config
        return SourcifyConfig.model_validate(data)

    @cached_property
    def client(self) -> SourcifyClient:
        return SourcifyClient(
            str(self.config.api_url),
            timeout=self.config.request_timeout,
            retries=self.config.retries,
            retry_backoff=self.config.retry_backoff,
            chain_cache_ttl=self.config.chain_cache_ttl,
        )

    @property
    def chain_id(self) -> int:
        try:
            return self.network.chain_id
        except Exception as err:
            metadata = get_chain_metadata(self.network.ecosystem.name, self.network.name)
            if metadata and (chain_id := metadata.get("chainId")) is not None:
                return int(chain_id)

            raise NetworkError(
                f"Unable to determine the chain ID for '{self.network.choice}'."
            ) from err

    def get_address_url(self, address: AddressType) -> str:
        address = self._normalize_address(address)
        base_url = str(self.config.repository_url).rstrip("/")
        return f"{base_url}/{self.chain_id}/{address}"

    def get_transaction_url(self, transaction_hash: str) -> str:
        # Sourcify has no transaction UI. Use the chain's canonical EIP-3091 explorer when Ape's
        # bundled chain metadata provides one, so transaction submission logs remain useful.
        base_url = get_block_explorer_url(
            self.chain_id,
            self.network.ecosystem.name,
            self.network.name,
        )
        if not base_url:
            raise APINotImplementedError(
                f"No transaction explorer is known for chain ID {self.chain_id}."
            )

        return f"{base_url}/tx/{transaction_hash}"

    def get_contract_type(self, address: AddressType) -> ContractType | None:
        address = self._normalize_address(address)
        contract = self.client.get_contract(
            self.chain_id,
            str(address),
            fields=_CONTRACT_TYPE_FIELDS,
        )
        return self._to_contract_type(contract) if contract else None

    def get_manifest(self, address: AddressType) -> PackageManifest | None:
        address = self._normalize_address(address)
        contract = self.client.get_contract(
            self.chain_id,
            str(address),
            fields=_MANIFEST_FIELDS,
        )
        if not contract or not contract.compilation:
            return None

        contract_type = self._to_contract_type(contract)
        name = contract.compilation.name
        standard_input = contract.std_json_input or {}
        settings = (
            copy.deepcopy(standard_input.get("settings") or contract.compilation.compiler_settings)
            or {}
        )
        storage_layout_overrides = standard_input.get("storage_layout_overrides")
        if storage_layout_overrides is None:
            storage_layout_overrides = standard_input.get("storageLayoutOverrides")

        if storage_layout_overrides is not None:
            # Compiler.settings is the only EthPM location capable of carrying this Vyper
            # standard-input field. SourceVerifier moves it back to the top level on publish.
            settings["storage_layout_overrides"] = storage_layout_overrides

        compiler = Compiler(
            name=contract.compilation.language.lower(),
            version=contract.compilation.compiler_version,
            settings=settings,
            contractTypes=[name],
        )
        sources = {
            source_id: Source.model_validate({"content": source.content})
            for source_id, source in contract.sources.items()
        }
        return PackageManifest(
            sources=sources,
            contractTypes={name: contract_type},
            compilers=[compiler],
        )

    def publish_contract(self, address: AddressType):
        address = self._normalize_address(address)
        verifier = SourceVerifier(
            address,
            self.chain_id,
            self.client,
            str(self.config.repository_url),
            verification_timeout=self.config.verification_timeout,
            poll_interval=self.config.poll_interval,
        )
        return verifier.attempt_verification()

    @classmethod
    def supports_chain(cls, chain_id: int) -> bool:
        if cls._chain_client is not None:
            client = cls._chain_client
        else:
            config = cls._load_config()
            key = (
                str(config.api_url),
                config.request_timeout,
                config.retries,
                config.retry_backoff,
                config.chain_cache_ttl,
            )
            if key not in cls._chain_clients:
                cls._chain_clients[key] = SourcifyClient(
                    key[0],
                    timeout=key[1],
                    retries=key[2],
                    retry_backoff=key[3],
                    chain_cache_ttl=key[4],
                )

            client = cls._chain_clients[key]

        try:
            return client.supports_chain(chain_id)
        except SourcifyError:
            return chain_id in KNOWN_SUPPORTED_CHAIN_IDS

    def _normalize_address(self, address: AddressType) -> AddressType:
        if self.conversion_manager.is_type(address, AddressType):
            return address

        return self.conversion_manager.convert(str(address), AddressType)

    @staticmethod
    def _to_contract_type(contract: VerifiedContract) -> ContractType:
        if not contract.compilation:
            raise ValueError("Sourcify response is missing compilation information.")

        compilation = contract.compilation
        source_id = None
        if compilation.fully_qualified_name:
            source_id = compilation.fully_qualified_name.rsplit(":", 1)[0]

        data = {
            "abi": contract.abi,
            "contractName": compilation.name,
            "sourceId": source_id,
            "userdoc": contract.userdoc,
            "devdoc": contract.devdoc,
        }
        if contract.creation_bytecode and contract.creation_bytecode.recompiled_bytecode:
            data["deploymentBytecode"] = {
                "bytecode": contract.creation_bytecode.recompiled_bytecode
            }
        if contract.runtime_bytecode and contract.runtime_bytecode.recompiled_bytecode:
            data["runtimeBytecode"] = {"bytecode": contract.runtime_bytecode.recompiled_bytecode}

        return ContractType.model_validate(data)
