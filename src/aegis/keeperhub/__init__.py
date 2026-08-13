"""KeeperHub integration layer.

KeeperHub is the ONLY onchain execution layer for Aegis. This package talks
to KeeperHub's REST API over HTTPS using an organization API key; it never
constructs, signs, or broadcasts a transaction itself, and it never handles
a private key.

Phase 1 exposes read-only/introspection calls only (current user, chain
catalog, health check). Execution endpoints (transfer, contract-call,
check-and-execute) are intentionally not wrapped yet — that is Phase 2 work
and must go through KeeperHub's own simulate-gated API.
"""

from aegis.keeperhub.client import KeeperHubClient
from aegis.keeperhub.exceptions import (
    KeeperHubAuthError,
    KeeperHubConnectionError,
    KeeperHubError,
    MainnetBlockedError,
)

__all__ = [
    "KeeperHubClient",
    "KeeperHubError",
    "KeeperHubAuthError",
    "KeeperHubConnectionError",
    "MainnetBlockedError",
]
