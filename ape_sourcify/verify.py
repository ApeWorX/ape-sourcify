import copy
import re
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ape.exceptions import ApeException
from ape.logging import logger
from ape.types import AddressType, ContractType
from ape.utils import ManagerAccessMixin, cached_property, get_full_extension
from ethpm_types import Compiler, PackageManifest

from ape_sourcify.client import SourcifyClient
from ape_sourcify.exceptions import (
    ContractAlreadyVerifiedError,
    ContractVerificationError,
    SourcifyAPIError,
    SourcifyTimeoutError,
)
from ape_sourcify.types import VerificationJob

_SOLIDITY_VERSION_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+(?:-nightly\.\d{4}\.\d+\.\d+)?\+commit\.[a-f0-9]{8}$"
)
_LANGUAGE_NAMES = {
    "fe": "Fe",
    "solc": "Solidity",
    "solidity": "Solidity",
    "vyper": "Vyper",
    "yul": "Yul",
}


class SourceVerifier(ManagerAccessMixin):
    """Build and submit a compiler-native Sourcify verification request."""

    def __init__(
        self,
        address: AddressType,
        chain_id: int,
        client: SourcifyClient,
        repository_url: str,
        *,
        verification_timeout: float = 300.0,
        poll_interval: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.address = address
        self.chain_id = chain_id
        self.client = client
        self.repository_url = repository_url.rstrip("/")
        self.verification_timeout = verification_timeout
        self.poll_interval = poll_interval
        self._sleep = sleep
        self._clock = clock

    @cached_property
    def contract_type(self) -> ContractType:
        try:
            instance = self.chain_manager.contracts.instance_at(
                self.address,
                fetch_from_explorer=False,
                detect_proxy=False,
            )
        except Exception as err:
            raise ContractVerificationError(
                f"Unable to find a local contract type for '{self.address}'."
            ) from err

        return instance.contract_type

    @cached_property
    def manifest(self) -> PackageManifest:
        return self.local_project.extract_manifest()

    @cached_property
    def compiler(self) -> Compiler:
        if not (name := self.contract_type.name):
            raise ContractVerificationError("The local contract type has no name.")

        if compiler := self.manifest.get_contract_compiler(name):
            return compiler

        raise ContractVerificationError(
            f"Compiler information for contract '{name}' is missing from the project manifest."
        )

    @property
    def source_id(self) -> str:
        if source_id := self.contract_type.source_id:
            return source_id

        raise ContractVerificationError(
            f"The local contract type '{self.contract_type.name or self.address}' has no source ID."
        )

    @property
    def contract_identifier(self) -> str:
        if name := self.contract_type.name:
            return f"{self.source_id}:{name}"

        raise ContractVerificationError("The local contract type has no name.")

    @property
    def compiler_version(self) -> str:
        version = str(self.compiler.version).removeprefix("v")
        if self.language == "Solidity" and not _SOLIDITY_VERSION_PATTERN.fullmatch(version):
            raise ContractVerificationError(
                "Sourcify requires the full Solidity compiler version, including its commit hash; "
                f"the Ape manifest contains '{version}'. Recompile the project before publishing."
            )

        return version

    @property
    def language(self) -> str:
        if self.source_id.endswith(".yul"):
            return "Yul"

        name = self.compiler.name.lower()
        if language := _LANGUAGE_NAMES.get(name):
            return language

        raise ContractVerificationError(f"Unsupported compiler language '{self.compiler.name}'.")

    @cached_property
    def creation_transaction_hash(self) -> str | None:
        try:
            creation = self.chain_manager.contracts.get_creation_metadata(self.address)
        except ApeException:
            return None

        return creation.txn_hash.lower() if creation else None

    def build_standard_json_input(self) -> dict[str, Any]:
        settings = copy.deepcopy(self.compiler.settings or {})

        # Local search paths are an Ape/Vyper implementation detail. All files sent to Sourcify
        # are inlined below, so remote compilation must not receive host paths.
        settings.pop("search_paths", None)
        settings.pop("searchPaths", None)

        storage_layout_overrides = settings.pop("storage_layout_overrides", None)
        if storage_layout_overrides is None:
            storage_layout_overrides = settings.pop("storageLayoutOverrides", None)

        source_ids = self._collect_source_ids()
        # Vyper requires outputSelection and Solidity uses it to request the artifacts Sourcify
        # compares. Ape manifests may retain entries for every contract compiled in the project,
        # so constrain it to this contract's import graph instead of forwarding unrelated names.
        output_selection = settings.get("outputSelection")
        if isinstance(output_selection, dict):
            settings["outputSelection"] = {
                source_id: selection
                for source_id, selection in output_selection.items()
                if source_id == "*" or source_id in source_ids
            }
        elif self.language == "Vyper":
            settings["outputSelection"] = {self.source_id: ["*"]}

        sources: dict[str, dict[str, str]] = {}
        missing: list[str] = []
        empty: list[str] = []
        for source_id in sorted(source_ids):
            self._validate_source_id(source_id)
            content = self._get_source_content(source_id)
            if content is None:
                missing.append(source_id)
            elif content == "":
                empty.append(source_id)
            else:
                sources[source_id] = {"content": content}

        if missing:
            missing_str = ", ".join(missing)
            raise ContractVerificationError(
                f"Unable to materialize required source file(s): {missing_str}."
            )

        if empty:
            empty_str = ", ".join(empty)
            raise ContractVerificationError(
                f"Sourcify does not accept empty source files; unable to publish: {empty_str}."
            )

        result: dict[str, Any] = {
            "language": self.language,
            "sources": sources,
            "settings": settings,
        }
        if storage_layout_overrides is not None:
            result["storage_layout_overrides"] = storage_layout_overrides

        return result

    def attempt_verification(self) -> VerificationJob | None:
        standard_json = self.build_standard_json_input()
        deadline = self._clock() + self.verification_timeout

        # Duplicate submissions do not expose the existing job ID. Wait until the earlier job
        # completes and retry: success becomes an idempotent 409, while failure permits a new job.
        while True:
            try:
                verification_id = self.client.submit_verification(
                    self.chain_id,
                    str(self.address),
                    std_json_input=standard_json,
                    compiler_version=self.compiler_version,
                    contract_identifier=self.contract_identifier,
                    creation_transaction_hash=self.creation_transaction_hash,
                )
                break
            except ContractAlreadyVerifiedError as err:
                logger.success(f"Contract is already verified by Sourcify.\n{self.contract_url}")
                logger.debug(str(err))
                return None
            except SourcifyAPIError as err:
                if err.custom_code != "duplicate_verification_request":
                    raise ContractVerificationError(
                        f"Sourcify rejected the verification request: {err}"
                    ) from err

                if self._clock() >= deadline:
                    raise SourcifyTimeoutError(
                        "Timed out waiting for an existing Sourcify verification job "
                        f"for '{self.address}'."
                    ) from err

                logger.debug("A Sourcify verification is already running; waiting to retry.")
                self._sleep(self.poll_interval)

        return self._wait_for_verification(verification_id, deadline)

    @property
    def contract_url(self) -> str:
        return f"{self.repository_url}/{self.chain_id}/{self.address}"

    def _wait_for_verification(self, verification_id: str, deadline: float) -> VerificationJob:
        while self._clock() < deadline:
            job = self.client.get_verification_job(verification_id)
            if not job.is_job_completed:
                logger.debug(f"Sourcify verification '{verification_id}' is pending.")
                self._sleep(self.poll_interval)
                continue

            if job.contract and job.contract.match is not None:
                logger.success(f"Contract verification successful!\n{self.contract_url}")
                return job

            if error := job.error:
                raise ContractVerificationError(
                    error.message,
                    custom_code=error.custom_code,
                    error_id=error.error_id,
                    error_data=error.model_dump(mode="json", by_alias=True),
                )

            raise ContractVerificationError(
                "Sourcify completed the verification job without a bytecode match."
            )

        raise SourcifyTimeoutError(
            f"Sourcify verification '{verification_id}' did not finish within "
            f"{self.verification_timeout:g} seconds."
        )

    def _collect_source_ids(self) -> set[str]:
        source_path = self._resolve_source_path(self.source_id)
        if not source_path.is_file():
            raise ContractVerificationError(
                f"Unable to inspect the import graph because '{self.source_id}' is not a "
                "local source file."
            )

        extension = get_full_extension(source_path)
        compiler_api = self.compiler_manager.registered_compilers.get(extension)
        if not compiler_api:
            raise ContractVerificationError(
                f"No compiler plugin is installed for '{extension}', so the source import "
                "graph cannot be resolved safely."
            )

        try:
            import_map = compiler_api.get_imports((source_path,), project=self.local_project)
        except Exception as err:
            raise ContractVerificationError(
                f"Unable to resolve the source import graph for '{self.source_id}': {err}"
            ) from err

        source_ids = {self.source_id}
        source_ids.update(import_map)
        for imports in import_map.values():
            source_ids.update(imports)

        return {
            source_id for source_id in source_ids if not self._is_compiler_builtin_source(source_id)
        }

    def _get_source_content(self, source_id: str) -> str | None:
        # Prefer the original file. EthPM's Source model normalizes a missing final newline,
        # which changes compiler metadata and prevents exact matches.
        path = self._resolve_source_path(source_id)
        if path.is_file():
            return path.read_text(encoding="utf8")

        manifest_source = (self.manifest.sources or {}).get(source_id)
        if manifest_source is not None:
            return str(manifest_source.content or "")

        for dependency in self.local_project.dependencies:
            dependency_project = dependency.project
            dependency_path = self._resolve_source_path(source_id, root=dependency_project.path)
            if dependency_path.is_file():
                return dependency_path.read_text(encoding="utf8")

            dependency_source = (dependency_project.manifest.sources or {}).get(source_id)
            if dependency_source is not None:
                return str(dependency_source.content or "")

        return None

    def _resolve_source_path(self, source_id: str, *, root: Path | None = None) -> Path:
        self._validate_source_id(source_id)
        source_root = (root or self.local_project.path).resolve()
        path = (source_root / source_id).resolve()
        if not path.is_relative_to(source_root):
            raise ContractVerificationError(
                f"Refusing to publish source path '{source_id}' because it resolves outside "
                f"'{source_root}'."
            )

        return path

    def _is_compiler_builtin_source(self, source_id: str) -> bool:
        if self.language != "Vyper":
            return False

        normalized = source_id.replace("\\", "/")
        return normalized.endswith(".json") and normalized.startswith(("vyper/", "ethereum/"))

    @staticmethod
    def _validate_source_id(source_id: str) -> None:
        posix_path = PurePosixPath(source_id)
        windows_path = PureWindowsPath(source_id)
        if (
            not source_id
            or "\x00" in source_id
            or posix_path == PurePosixPath(".")
            or windows_path == PureWindowsPath(".")
            or posix_path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in posix_path.parts
            or ".." in windows_path.parts
        ):
            raise ContractVerificationError(
                f"Refusing to publish unsafe source path '{source_id}'."
            )
