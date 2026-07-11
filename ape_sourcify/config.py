from ape.api import PluginConfig
from pydantic import AnyHttpUrl, Field


class SourcifyConfig(PluginConfig):
    """Configuration for the Sourcify API and repository UI."""

    api_url: AnyHttpUrl = AnyHttpUrl("https://sourcify.dev/server")
    repository_url: AnyHttpUrl = AnyHttpUrl("https://repo.sourcify.dev")
    request_timeout: float = Field(default=30.0, gt=0)
    retries: int = Field(default=3, ge=0)
    retry_backoff: float = Field(default=0.5, ge=0)
    verification_timeout: float = Field(default=300.0, gt=0)
    poll_interval: float = Field(default=2.0, gt=0)
    chain_cache_ttl: float = Field(default=3600.0, ge=0)
