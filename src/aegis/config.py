"""Configuration management for Aegis, sourced entirely from environment variables.

Aegis never reads or stores a private key. The only credential it holds is a
KeeperHub organization API key, used to authenticate to KeeperHub's REST API —
the sole onchain execution layer for this project.
"""

from __future__ import annotations

from decimal import Decimal
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

# Closed set of decisions the policy engine will approve. Anything Hermes
# (the LLM decision layer) proposes outside this set is rejected, not
# clamped.
DEFAULT_ALLOWED_DECISIONS: tuple[str, ...] = ("DO_NOTHING", "REPAY_DEBT", "ADD_COLLATERAL")

# Closed set of KeeperHub protocol actions the policy engine will approve.
# Scoped to Aave V3 read/repay/supply only for this phase — no Morpho, no
# market-wide trading.
DEFAULT_ALLOWED_PROTOCOL_ACTIONS: tuple[str, ...] = (
    "aave-v3/get-user-account-data",
    "aave-v3/get-user-reserve-data",
    "aave-v3/repay",
    "aave-v3/supply",
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

    aegis_allowed_decisions: Annotated[tuple[str, ...], NoDecode] = Field(
        default=DEFAULT_ALLOWED_DECISIONS,
        description="Closed set of Decision values the policy engine approves.",
    )
    aegis_allowed_protocol_actions: Annotated[tuple[str, ...], NoDecode] = Field(
        default=DEFAULT_ALLOWED_PROTOCOL_ACTIONS,
        description="Closed set of KeeperHub protocol actionType strings the policy engine approves.",
    )
    aegis_expected_wallet_address: str | None = Field(
        default=None,
        description=(
            "If set, the policy engine rejects any intent whose on_behalf_of address does not "
            "match this wallet. Unset means the wallet-pin rule is not enforced — set this for "
            "any real deployment."
        ),
    )
    aegis_max_tx_amount: Decimal = Field(
        default=Decimal("100"),
        gt=0,
        description="Blunt, asset-agnostic cap on Intent.amount. Refine per-asset later.",
    )
    aegis_health_factor_threshold: Decimal = Field(
        default=Decimal("1.5"),
        gt=0,
        description="Aave V3 health factor (human units) below which a position is AT_RISK.",
    )
    aegis_debt_asset: str | None = Field(
        default=None,
        description="Asset address/symbol the pipeline proposes repaying when a position is AT_RISK.",
    )
    aegis_collateral_asset: str | None = Field(
        default=None,
        description="Asset address/symbol the pipeline proposes supplying when a position is AT_RISK.",
    )
    aegis_available_balance: Decimal | None = Field(
        default=None,
        description=(
            "Known available wallet balance (base-currency-equivalent units, see "
            "aegis.decision_engine's units note) the pipeline checks candidate amounts against. "
            "Unset means the balance-sufficiency execution signal is not evaluated."
        ),
    )

    anthropic_api_key: str | None = Field(
        default=None,
        description=(
            "Anthropic API key for Hermes's real LLM client. Never a KeeperHub or wallet "
            "credential. Only required to construct AnthropicLlmClient; not needed to run tests."
        ),
    )
    aegis_hermes_model: str = Field(
        default="claude-sonnet-5",
        description="Anthropic model id Hermes uses for READ/ANALYZE/DECIDE.",
    )
    aegis_autonomous_execution_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for real (non-simulated) execution from the Aegis loop. Defaults to "
            "False: the loop always simulates a policy-approved intent but stops before "
            "executing until this is explicitly turned on."
        ),
    )
    aegis_audit_log_path: str = Field(
        default="./aegis_runs.jsonl",
        description=(
            "JSON-lines file every aegis.demo_orchestrator run appends its audit trail to. "
            "Shared across processes (CLI and the dashboard API) so a run started by one "
            "(e.g. `aegis live-demo --confirm`) can still be polled by the other."
        ),
    )

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

    @field_validator("aegis_allowed_decisions", "aegis_allowed_protocol_actions", mode="before")
    @classmethod
    def _parse_str_tuple(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value)  # type: ignore[arg-type]


@lru_cache
def get_settings() -> Settings:
    """Return process-wide cached settings, loaded once from the environment."""
    return Settings()  # type: ignore[call-arg]
