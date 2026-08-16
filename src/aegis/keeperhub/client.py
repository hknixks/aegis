"""HTTP client for the KeeperHub REST API.

This client only ever performs authenticated HTTPS requests against
KeeperHub, using an organization API key (`kh_...`) as a Bearer token. It
does not construct, sign, or broadcast transactions — that is entirely
KeeperHub's responsibility as the sole onchain execution layer.

Protocol-action methods below (simulate/execute/status) POST to KeeperHub's
real Direct Execution REST endpoint, `/api/execute/contract-call`
(confirmed against https://docs.keeperhub.com/api/direct-execution and a
live Base Sepolia call) — KeeperHub's REST API has no separate
'protocol-action' endpoint; that abstraction (`aave-v3/repay`, etc.) exists
only on the MCP side (`execute_protocol_action`). `call_protocol_action`'s
`params` argument is therefore already the raw contract-call body
(`contractAddress`, `chainId`, `functionName`, `functionArgs`) — see
aegis.aave, which is what actually knows Aave's Pool address/ABI per
chain and builds that body. `action_type` (e.g. `'aave-v3/repay'`) is kept
only as a human-readable label for logging/errors; it is never sent to
KeeperHub.
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
from aegis.keeperhub.models import (
    ExecutionStatus,
    HealthCheckResult,
    KeeperHubChain,
    KeeperHubUser,
    ProtocolActionExecution,
    ProtocolActionSimulation,
)

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

    def _check_response(self, response: httpx.Response, path: str) -> None:
        if response.status_code in (401, 403):
            raise KeeperHubAuthError(
                f"KeeperHub rejected the configured API key ({response.status_code}) for {path}"
            )
        if response.status_code >= 400:
            raise KeeperHubError(
                f"KeeperHub returned {response.status_code} for {path}: {response.text[:200]}"
            )

    def _get(self, path: str) -> httpx.Response:
        try:
            response = self._http.get(path)
        except httpx.RequestError as exc:
            raise KeeperHubConnectionError(f"Could not reach KeeperHub at {path}: {exc}") from exc
        self._check_response(response, path)
        return response

    def _post(
        self, path: str, *, json: dict[str, object], headers: dict[str, str] | None = None
    ) -> httpx.Response:
        try:
            response = self._http.post(path, json=json, headers=headers)
        except httpx.RequestError as exc:
            raise KeeperHubConnectionError(f"Could not reach KeeperHub at {path}: {exc}") from exc
        self._check_response(response, path)
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

    def call_protocol_action(
        self,
        action_type: str,
        params: dict[str, object],
        *,
        simulate: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        """POST to /api/execute/contract-call.

        `params` is already the full contract-call body (contractAddress,
        chainId, functionName, functionArgs) — see this module's docstring.
        `action_type` (e.g. 'aave-v3/repay') is a label only, for
        logging/errors; KeeperHub's contract-call endpoint has no concept
        of it.
        """
        body: dict[str, object] = dict(params)
        if simulate:
            body["simulate"] = True
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = self._post("/api/execute/contract-call", json=body, headers=headers)
        return response.json()

    def simulate_protocol_action(
        self, action_type: str, params: dict[str, object]
    ) -> ProtocolActionSimulation:
        """Simulate a state-changing protocol action without broadcasting it."""
        raw = self.call_protocol_action(action_type, params, simulate=True)
        return ProtocolActionSimulation.model_validate(raw)

    def execute_protocol_action(
        self, action_type: str, params: dict[str, object], *, idempotency_key: str
    ) -> ProtocolActionExecution:
        """Execute a state-changing protocol action for real. Always pass a
        fresh, deterministic idempotency_key — retrying with the same key
        and arguments returns the original result instead of executing
        again."""
        raw = self.call_protocol_action(action_type, params, idempotency_key=idempotency_key)
        return ProtocolActionExecution.model_validate(raw)

    def get_protocol_action_status(self, execution_id: str) -> ExecutionStatus:
        """GET the on-chain-reconciled status of a previously submitted
        direct execution. `unconfirmed` is not terminal — keep polling."""
        response = self._get(f"/api/execute/{execution_id}/status")
        return ExecutionStatus.model_validate(response.json())

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
