"""Exceptions raised by the KeeperHub integration layer."""

from __future__ import annotations


class KeeperHubError(Exception):
    """Base class for all KeeperHub integration errors."""


class KeeperHubConnectionError(KeeperHubError):
    """Raised when KeeperHub could not be reached (network/timeout)."""


class KeeperHubAuthError(KeeperHubError):
    """Raised when KeeperHub rejects the configured API key (HTTP 401/403)."""


class MainnetBlockedError(KeeperHubError):
    """Raised when an operation targets a chain ID outside the configured
    testnet allowlist. This project must not interact with mainnet."""


class KeeperHubMcpConnectionError(KeeperHubError):
    """Raised when KeeperHub's MCP endpoint could not be reached
    (network/transport failure, not a timeout)."""


class KeeperHubMcpTimeoutError(KeeperHubError):
    """Raised when a KeeperHub MCP request did not complete within the
    configured timeout."""


class KeeperHubMcpProtocolError(KeeperHubError):
    """Raised when KeeperHub's MCP server returned a JSON-RPC-level error
    (mcp.MCPError) — reachable and possibly authenticated, but the
    request itself was rejected."""
