"""HTTP client for the KeeperHub REST API.

This client only ever performs authenticated HTTPS requests against
KeeperHub, using an organization API key (`kh_...`) as a Bearer token. It
does not construct, sign, or broadcast transactions — that is entirely
KeeperHub's responsibility as the sole onchain execution layer.

Phase 1 scope: read-only/introspection endpoints only.
"""

from __future__ import annotations

import logging

import httpx

from aegis.config import Settings
from aegis.keeperhub.exceptions import (
    KeeperHubAuthError,
    KeeperHubConnectionError,
    KeeperHubError,
)
from aegis.keeperhub.models import HealthCheckResult, KeeperHubChain, KeeperHubUser

logger = logging.getLogger(__name__)


class KeeperHubClient:
    """Thin, read-only wrapper around the KeeperHub REST API."""

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            base_url=str(settings.keeperhub_base_url).rstrip("/"),
            timeout=settings.keeperhub_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.keeperhub_api_key}",
                "Accept": "application/json",
            },
        )

    def __enter__(self) -> "KeeperHubClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def _get(self, path: str) -> httpx.Response:
        try:
            response = self._http.get(path)
        except httpx.RequestError as exc:
            raise KeeperHubConnectionError(f"Could not reach KeeperHub at {path}: {exc}") from exc

        if response.status_code in (401, 403):
            raise KeeperHubAuthError(
                f"KeeperHub rejected the configured API key ({response.status_code}) for {path}"
            )
        if response.status_code >= 400:
            raise KeeperHubError(
                f"KeeperHub returned {response.status_code} for {path}: {response.text[:200]}"
            )
        return response

    def get_current_user(self) -> KeeperHubUser:
        """GET /api/user — validates the API key and returns org/user identity."""
        response = self._get("/api/user")
        return KeeperHubUser.model_validate(response.json())

    def list_chains(self) -> list[KeeperHubChain]:
        """GET /api/chains — public chain catalog, used as a reachability probe."""
        response = self._get("/api/chains")
        payload = response.json()
        items = payload["items"] if isinstance(payload, dict) and "items" in payload else payload
        return [KeeperHubChain.model_validate(item) for item in items]

    def health_check(self) -> HealthCheckResult:
        """Verify KeeperHub is reachable and the API key is valid.

        Two stages, so failures are diagnosable:
        1. Reachability — GET /api/chains (no auth required).
        2. Authentication — GET /api/user (requires a valid kh_ API key).
        """
        base_url = str(self._settings.keeperhub_base_url)

        try:
            chains = self.list_chains()
        except KeeperHubError as exc:
            logger.warning("KeeperHub reachability check failed: %s", exc)
            return HealthCheckResult(
                reachable=False, authenticated=False, base_url=base_url, detail=str(exc)
            )

        try:
            user = self.get_current_user()
        except KeeperHubAuthError as exc:
            logger.warning("KeeperHub authentication failed: %s", exc)
            return HealthCheckResult(
                reachable=True,
                authenticated=False,
                base_url=base_url,
                chain_count=len(chains),
                detail=str(exc),
            )
        except KeeperHubError as exc:
            logger.warning("KeeperHub user lookup failed: %s", exc)
            return HealthCheckResult(
                reachable=True,
                authenticated=False,
                base_url=base_url,
                chain_count=len(chains),
                detail=str(exc),
            )

        return HealthCheckResult(
            reachable=True,
            authenticated=True,
            base_url=base_url,
            chain_count=len(chains),
            user_id=user.id,
        )
