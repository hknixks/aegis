"""Configuration management for Aegis, sourced entirely from environment variables.

Aegis never reads or stores a private key. The only credential it holds is a
KeeperHub organization API key, used to authenticate to KeeperHub's REST API —
the sole onchain execution layer for this project.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Well-known mainnet chain IDs. Kept as a defensive default so that if a
# future execution feature is added, it fails closed on mainnet unless the
# operator explicitly opts a chain into AEGIS_ALLOWED_CHAIN_IDS.
KNOWN_MAINNET_CHAIN_IDS: frozenset[int] = frozenset(
    {
        1,  # Ethereum
        8453,  # Base
        42161,  # Arbitrum One
        10,  # Optimism
        137,  # Polygon
        56,  # BNB Smart Chain
        43114,  # Avalanche C-Chain
    }
)

# Default allowlist: public testnets only.
DEFAULT_ALLOWED_CHAIN_IDS: tuple[int, ...] = (
    11155111,  # Ethereum Sepolia
    84532,  # Base Sepolia
    421614,  # Arbitrum Sepolia
)


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables or a .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    keeperhub_api_key: str = Field(
        ...,
        description="KeeperHub organization API key (kh_ prefix). Never a private key.",
    )
    keeperhub_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("https://app.keeperhub.com"),
        description="KeeperHub REST API base URL. Paths already include /api.",
    )
    keeperhub_mcp_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("https://app.keeperhub.com/mcp"),
        description="KeeperHub MCP server endpoint, reserved for later phases.",
    )
    keeperhub_timeout_seconds: float = Field(default=10.0, gt=0)

    aegis_allowed_chain_ids: Annotated[tuple[int, ...], NoDecode] = Field(
        default=DEFAULT_ALLOWED_CHAIN_IDS
    )
    aegis_log_level: str = Field(default="INFO")
    aegis_log_format: str = Field(default="text")

    @field_validator("keeperhub_api_key")
    @classmethod
    def _validate_api_key_shape(cls, value: str) -> str:
        if not value.startswith("kh_"):
            raise ValueError(
                "KEEPERHUB_API_KEY must be a KeeperHub organization API key "
                "(starts with 'kh_'); private keys are never accepted here."
            )
        return value

    @field_validator("aegis_allowed_chain_ids", mode="before")
    @classmethod
    def _parse_chain_ids(cls, value: object) -> tuple[int, ...]:
        if isinstance(value, str):
            ids = tuple(int(part.strip()) for part in value.split(",") if part.strip())
        else:
            ids = tuple(int(v) for v in value)  # type: ignore[arg-type]
        mainnet_hits = KNOWN_MAINNET_CHAIN_IDS & set(ids)
        if mainnet_hits:
            raise ValueError(
                f"AEGIS_ALLOWED_CHAIN_IDS includes known mainnet chain IDs {sorted(mainnet_hits)}. "
                "This project must not interact with mainnet."
            )
        return ids

    @field_validator("aegis_log_format")
    @classmethod
    def _validate_log_format(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"text", "json"}:
            raise ValueError("AEGIS_LOG_FORMAT must be 'text' or 'json'")
        return normalized


@lru_cache
def get_settings() -> Settings:
    """Return process-wide cached settings, loaded once from the environment."""
    return Settings()  # type: ignore[call-arg]
