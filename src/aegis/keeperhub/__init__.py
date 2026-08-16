"""KeeperHub integration layer.

KeeperHub is the ONLY onchain execution layer for Aegis. This package talks
to KeeperHub's REST API over HTTPS using an organization API key; it never
constructs, signs, or broadcasts a transaction itself, and it never handles
a private key.

Read/introspection calls (current user, chain catalog, health check) and
protocol-action calls (simulate, execute, status) are both exposed here.
Every write path requires an explicit simulate=True call first — this
client does not enforce that ordering itself; aegis.execution does.
"""

from aegis.keeperhub.client import KeeperHubClient
from aegis.keeperhub.exceptions import (
    KeeperHubAuthError,
    KeeperHubConnectionError,
    KeeperHubError,
    KeeperHubMcpConnectionError,
    KeeperHubMcpProtocolError,
    KeeperHubMcpTimeoutError,
    MainnetBlockedError,
)

__all__ = [
    "KeeperHubClient",
    "KeeperHubError",
    "KeeperHubAuthError",
    "KeeperHubConnectionError",
    "KeeperHubMcpConnectionError",
    "KeeperHubMcpProtocolError",
    "KeeperHubMcpTimeoutError",
    "MainnetBlockedError",
]
