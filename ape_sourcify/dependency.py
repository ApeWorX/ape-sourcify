import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ape import Project
from ape.api.projects import DependencyAPI
from ape.exceptions import ProjectError
from ape.types import AddressType
from ethpm_types import PackageManifest
from hexbytes import HexBytes
from pydantic import field_validator

from ape_sourcify.explorer import Sourcify

_RESERVED_PROJECT_PATHS = frozenset(
    {
        ".ape",
        ".build",
        ".git",
        "__sourcify__",
        "ape-config.json",
        "ape-config.yaml",
        "ape-config.yml",
        "pyproject.toml",
    }
)


class SourcifyDependency(DependencyAPI):
    """An Ape dependency sourced from a Sourcify-verified address."""

    sourcify: str
    ecosystem: str = "ethereum"
    network: str = "mainnet"

    @field_validator("sourcify", mode="before")
    @classmethod
    def normalize_address(cls, value: Any) -> str:
        if isinstance(value, int) and not isinstance(value, bool):
            if value < 0 or value >= 1 << 160:
                raise ValueError("Sourcify dependency address must be a 20-byte hex string.")

            # YAML 1.1 parsers interpret an unquoted 0x-prefixed address as an integer. Preserve
            # its leading zero bytes rather than relying on HexBytes' minimal representation.
            address = f"0x{value:040x}"
        else:
            address = value if isinstance(value, str) else HexBytes(value).hex()

        if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
            raise ValueError("Sourcify dependency address must be a 20-byte hex string.")

        return address

    @field_validator("ecosystem", "network")
    @classmethod
    def validate_network_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
            raise ValueError(
                "Ecosystem and network names may only contain letters, numbers, _ or -."
            )

        return value.lower()

    @property
    def version_id(self) -> str:
        return f"{self.ecosystem}_{self.network}"

    @property
    def package_id(self) -> str:
        return f"sourcify/{self.sourcify.lower()}"

    @property
    def address(self) -> AddressType:
        ecosystem = self.network_manager.get_ecosystem(self.ecosystem)
        return ecosystem.decode_address(self.sourcify)

    @property
    def explorer(self) -> Sourcify:
        ecosystem = self.network_manager.get_ecosystem(self.ecosystem)
        network = ecosystem.get_network(self.network)
        return Sourcify(name="sourcify", network=network)

    @property
    def uri(self) -> str:
        return self.explorer.get_address_url(self.address)

    @property
    def manifest_cache_path(self) -> Path:
        # Ape passes this external manifest path back to LocalProject after fetch(). Seeding it
        # retains Sourcify's target artifact and compiler metadata while the materialized project
        # remains importable from user contracts.
        packages_cache = self.local_project.dependencies.packages_cache
        return packages_cache.get_manifest_path(self.package_id, self.version_id)

    def extract_manifest(self, use_cache: bool = True) -> PackageManifest:
        if manifest := self.explorer.get_manifest(self.address):
            return manifest

        raise ProjectError(
            f"Sourcify dependency '{self.name}' is not verified on {self.ecosystem}:{self.network}."
        )

    def fetch(self, destination: Path):
        manifest = self.extract_manifest(use_cache=False)
        for source_id in manifest.sources or {}:
            posix_path = PurePosixPath(source_id)
            windows_path = PureWindowsPath(source_id)
            path_parts = {
                part.casefold()
                for parts in (posix_path.parts, windows_path.parts)
                for part in parts
            }
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
                or bool(path_parts & _RESERVED_PROJECT_PATHS)
            ):
                raise ProjectError(f"Unsafe source path in Sourcify response: '{source_id}'.")

        # Validate the complete bundle before creating files, then stage it beside the final
        # destination so consumers can never observe an incrementally materialized source tree.
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}-sourcify-", dir=destination.parent)
        )
        try:
            for source_id, source in (manifest.sources or {}).items():
                target = staging / source_id
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(source.content or ""), encoding="utf8")

            target_source_ids = {
                contract_type.source_id
                for contract_type in (manifest.contract_types or {}).values()
                if contract_type.source_id
            }
            excluded_sources = sorted(set(manifest.sources or {}) - target_source_ids)
            remapping_config, remapping_targets = self._get_local_remapping_config(manifest)
            config_override: dict[str, Any] = {
                # Sourcify source IDs may span contracts/, lib/, vendor/, or the project root.
                # Keeping the source root at `.` ensures Ape unpacks every one of them.
                "contracts_folder": ".",
                # Only the verified target has an artifact. Other files remain importable but
                # are not pointlessly compiled when the dependency itself is loaded.
                "compile": {"exclude": excluded_sources},
            }
            config_override.update(remapping_config)
            project = Project.from_manifest(
                manifest,
                config_override=config_override,
            )
            project.config.write_to_disk(staging / "ape-config.yaml")

            # Alias roots are local Ape dependencies. Pin each source root so a nested source
            # directory is not mistaken for the whole contracts folder during unpacking.
            for target_root in remapping_targets.values():
                alias_root = staging / target_root
                alias_project = Project.from_manifest(
                    PackageManifest(),
                    config_override={"contracts_folder": "."},
                )
                alias_project.config.write_to_disk(alias_root / "ape-config.yaml")

            if destination.exists():
                if any(destination.iterdir()):
                    raise ProjectError(f"Dependency destination is not empty: '{destination}'.")

                destination.rmdir()

            staging.replace(destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        # Ape's package cache keeps build artifacts outside the materialized project. Write this
        # atomically so LocalProject loads the ABI, bytecode, sources, and full compiler settings.
        manifest_path = self.manifest_cache_path
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = manifest_path.with_name(f".{manifest_path.name}.tmp")
        try:
            temporary_path.write_text(
                manifest.model_dump_json(by_alias=True, exclude_none=True),
                encoding="utf8",
            )
            temporary_path.replace(manifest_path)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        finally:
            temporary_path.unlink(missing_ok=True)

    def _get_local_remapping_config(
        self,
        manifest: PackageManifest,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Translate simple Solidity remappings into local Ape dependencies."""

        source_ids = {source_id.replace("\\", "/") for source_id in manifest.sources or {}}
        dependency_targets: dict[str, str] = {}
        rewritten_remappings: set[str] = set()
        for compiler in manifest.compilers or []:
            if compiler.name.lower() not in ("solc", "solidity"):
                continue

            for remapping in (compiler.settings or {}).get("remappings") or []:
                key, separator, target = str(remapping).partition("=")
                key = key.rstrip("/\\")
                target = target.replace("\\", "/").removeprefix("./").rstrip("/")
                dependency_name = key.removeprefix("@")
                target_path = PurePosixPath(target)
                if (
                    not separator
                    or not key
                    or not target
                    or not re.fullmatch(r"[a-zA-Z0-9_-]+", dependency_name)
                    or dependency_name.lower() == self.name.lower()
                    or target_path.is_absolute()
                    or ".." in target_path.parts
                    or not any(source_id.startswith(f"{target}/") for source_id in source_ids)
                ):
                    continue

                existing_target = dependency_targets.get(dependency_name)
                if existing_target and existing_target != target:
                    continue

                dependency_targets[dependency_name] = target
                # Keep the dependency project's own resolver from interpreting this alias against
                # its package-cache root. The consumer project receives the real @name mapping
                # when Ape recursively unpacks the local alias dependency into its .cache folder.
                marker = f"__sourcify__/remappings/{dependency_name}"
                rewritten_remappings.add(f"{key}={marker}")

        if not dependency_targets:
            return {}, {}

        dependencies = [
            {"name": name, "local": target} for name, target in sorted(dependency_targets.items())
        ]
        return (
            {
                "dependencies": dependencies,
                "solidity": {"import_remapping": sorted(rewritten_remappings)},
            },
            dependency_targets,
        )
