from typing import Any

from evmchains import PUBLIC_CHAIN_META

# Explicit registrations make Sourcify available before a provider is connected.
# Dynamic/custom networks are handled by Sourcify.supports_chain().
NETWORK_CHAIN_IDS: dict[str, dict[str, int]] = {
    "arbitrum": {"mainnet": 42161, "sepolia": 421614},
    "avalanche": {"mainnet": 43114, "fuji": 43113},
    "base": {"mainnet": 8453, "sepolia": 84532},
    "blast": {"mainnet": 81457, "sepolia": 168587773},
    "bsc": {"mainnet": 56, "testnet": 97},
    "ethereum": {"mainnet": 1, "sepolia": 11155111, "holesky": 17000, "hoodi": 560048},
    "fantom": {"opera": 250},
    "gnosis": {"mainnet": 100, "chiado": 10200},
    "hyperliquid": {"mainnet": 999, "sepolia": 998},
    "optimism": {"mainnet": 10, "sepolia": 11155420},
    "polygon": {"mainnet": 137, "amoy": 80002},
    "polygon-zkevm": {"mainnet": 1101, "cardona": 2442},
    "sonic": {"mainnet": 146},
}
NETWORKS: dict[str, tuple[str, ...]] = {
    ecosystem: tuple(networks) for ecosystem, networks in NETWORK_CHAIN_IDS.items()
}


# Used as an offline fallback when the production /chains endpoint is unavailable.
KNOWN_SUPPORTED_CHAIN_IDS = frozenset(
    chain_id for networks in NETWORK_CHAIN_IDS.values() for chain_id in networks.values()
)


def _network_name(network_name: str) -> str:
    return network_name.removesuffix("-fork")


def get_chain_metadata(ecosystem_name: str, network_name: str) -> dict[str, Any] | None:
    """Return bundled EIP-155/EIP-3091 data for an Ape network."""

    return PUBLIC_CHAIN_META.get(ecosystem_name, {}).get(_network_name(network_name))


def find_chain_metadata(chain_id: int) -> dict[str, Any] | None:
    for networks in PUBLIC_CHAIN_META.values():
        for metadata in networks.values():
            if metadata.get("chainId") == chain_id:
                return metadata

    return None


def get_block_explorer_url(chain_id: int, ecosystem_name: str, network_name: str) -> str | None:
    metadata = get_chain_metadata(ecosystem_name, network_name) or find_chain_metadata(chain_id)
    if not metadata:
        return None

    for explorer in metadata.get("explorers") or []:
        if url := explorer.get("url"):
            return str(url).rstrip("/")

    return None
