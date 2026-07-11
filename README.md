# Ape Sourcify

`ape-sourcify` is an [Ape](https://apeworx.io/) explorer plugin backed by
[Sourcify](https://sourcify.dev/). It uses Sourcify API v2 for verified contract lookup,
source manifests, and asynchronous source-code verification.

## Features

- Load a verified contract's name and ABI through Ape's normal `Contract(address)` flow.
- Reconstruct an EthPM manifest containing verified sources, compiler settings, and bytecode.
- Verify locally compiled Solidity and Vyper contracts with compiler-native standard JSON input.
- Treat existing exact matches as idempotent publication success.
- Follow asynchronous verification jobs and surface Sourcify's error code, message, and error ID.
- Discover Sourcify's live supported-chain catalog for custom and adhoc Ape networks.
- Use verified contracts as Ape project dependencies.
- Configure a self-hosted or staging Sourcify server.

The plugin is v2-only. Sourcify's deprecated v1 API is not used.

## Installation

Install the package directly:

```bash
ape plugins install sourcify
```

or with `uv`/`pip`:

```bash
uv pip install ape-sourcify
```

## Contract lookup

Once the plugin is installed, Ape can obtain verified contract types from Sourcify:

```python
from ape import Contract

deposit_contract = Contract("0x00000000219ab540356cBB839Cbe05303d7705Fa")
print(deposit_contract.contract_type.name)
```

Canonical address links use the Sourcify repository UI:

```text
https://repo.sourcify.dev/{chain_id}/{address}
```

Sourcify is not a transaction indexer. For Ape's required transaction-link interface, the plugin
uses the first EIP-3091 explorer in Ape's bundled chain metadata. It raises
`APINotImplementedError` when no transaction explorer is known.

## Source verification

Use Ape's normal `publish=True` deployment option:

```python
contract = account.deploy(project.MyContract, publish=True)
```

or publish an existing locally known deployment:

```python
networks.active_provider.network.publish_contract(contract.address)
```

The plugin reads the local contract type, compiler record, source graph, compiler settings, and
creation transaction from Ape. It submits:

```text
POST /v2/verify/{chain_id}/{address}
GET  /v2/verify/{verification_id}
```

The creation transaction hash is included when Ape has it cached. Sourcify can verify without the
hash, but providing it makes creation-bytecode recovery more reliable.

Publishing sends the complete materialized source graph to Sourcify for permanent public display.
By submitting it, you accept Sourcify's non-exclusive, worldwide, irrevocable, royalty-free source
publication terms described in the [Sourcify API documentation](https://docs.sourcify.dev/docs/api/).
Do not publish source files that you are not authorized to disclose.

Publishing requires the full Solidity compiler version (for example,
`0.8.30+commit.73712a01`) in Ape's build manifest. Recompile the project if the manifest only
contains a short Solidity version.

## Sourcify dependencies

Verified source bundles can be declared directly in `ape-config.yaml`:

```yaml
dependencies:
  - name: deposit-contract
    sourcify: "0x00000000219ab540356cBB839Cbe05303d7705Fa"
    ecosystem: ethereum
    network: mainnet
```

Then install or use the dependency through Ape's normal package manager:

```bash
ape pm install
```

Source paths from the remote bundle are validated before its files and artifact-preserving build
manifest are written to the dependency cache.

The dependency cache preserves Sourcify's ABI, bytecode, sources, and full compiler record without
recompiling the verified target. Simple Solidity directory remappings such as
`@openzeppelin/=lib/openzeppelin-contracts/` are exposed as local Ape dependencies, including
chained remappings. With Ape 0.8, a remapping alias must be a simple name and must not collide with
the dependency's declared `name`; context-qualified and project-root remappings remain available in
the preserved manifest but cannot be used as nested source imports. Remapped bundles are designed
to use their preserved target artifact or to be imported by another project; forcing Ape to
recompile the dependency by itself is an Ape 0.8 limitation and is not supported.

## Configuration

The production defaults require no API key. Override them in `ape-config.yaml` for staging or a
self-hosted Sourcify instance:

```yaml
sourcify:
  api_url: https://staging.sourcify.dev/server
  repository_url: https://repo.sourcify.dev
  request_timeout: 30
  retries: 3
  retry_backoff: 0.5
  verification_timeout: 300
  poll_interval: 2
  chain_cache_ttl: 3600
```

| Setting                | Meaning                                                                    |                       Default |
| ---------------------- | -------------------------------------------------------------------------- | ----------------------------: |
| `api_url`              | Sourcify server base URL                                                   | `https://sourcify.dev/server` |
| `repository_url`       | Contract repository UI base URL                                            |   `https://repo.sourcify.dev` |
| `request_timeout`      | Per-request timeout in seconds                                             |                          `30` |
| `retries`              | Retries for timeouts, connection errors, 429s, and transient 5xx responses |                           `3` |
| `retry_backoff`        | Initial exponential retry delay                                            |                         `0.5` |
| `verification_timeout` | End-to-end job deadline in seconds                                         |                         `300` |
| `poll_interval`        | Verification job polling interval                                          |                           `2` |
| `chain_cache_ttl`      | Supported-chain cache lifetime                                             |                        `3600` |

## Networks and explorer selection

The plugin registers currently supported networks from these Ape ecosystems, including fork
variants:

- Arbitrum
- Avalanche
- Base
- Blast
- BNB Smart Chain
- Ethereum
- Fantom
- Gnosis
- Hyperliquid
- Optimism
- Polygon and Polygon zkEVM
- Sonic

For a connected custom or adhoc network, `supports_chain()` consults Sourcify's live `/chains`
catalog. Chain support is dynamic and can change as Sourcify updates that catalog.

Ape currently chooses the first matching installed explorer; it has no per-command explorer
selector. If multiple explorer plugins claim the same network, selection depends on plugin loading
order. Inspect `networks.active_provider.network.explorer` when several explorer plugins are
installed.

## Development

```bash
uv sync --all-groups
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy ape_sourcify
uv build
uv run --locked --group release twine check dist/*
```

Live production checks are opt-in:

```bash
uv run --locked pytest -m live -o addopts=""
```

## License

Apache-2.0
