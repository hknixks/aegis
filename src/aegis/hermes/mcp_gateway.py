"""Restricted MCP gateway for Hermes.

Hermes (the LLM decision layer) talks to KeeperHub exclusively through this
gateway, never through a raw MCP session. The gateway enforces, in code —
not just by prompting the model — three independent restrictions:

1. Tool-name allowlist: only read/discovery tools are reachable. No
   simulate/execute/transfer/workflow-management/Tempo tool is ever
   forwarded, regardless of what the model asks for.
2. actionType allowlist for `execute_protocol_action`: KeeperHub's MCP
   server exposes reads and writes through the SAME tool, distinguished
   only by an `actionType` string parameter — there is no separate
   read-only tool for Aave V3. Since tool-name filtering alone can't close
   that gap, this gateway additionally restricts the actionType values
   Hermes may pass to the Aave V3 read actions only.
3. Chain-ID check: any call whose arguments (or nested params) carry a
   network/chain_id/chainId field is checked against this project's
   testnet allowlist and known-mainnet blocklist, so mainnet stays
   unreachable through Hermes even if a call somehow targeted it.

Calls are always sequential: call_tool() takes and dispatches exactly one
call at a time. There is no batch/parallel entry point, so KeeperHub tool
use from Hermes is never parallelized. _LiveMcpSession (the real transport,
below) enforces this at the transport level too, via a lock around every
call — so even if a caller somehow bypassed this gateway's own sequential
contract, the underlying MCP session would still serialize requests.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

from aegis.config import KNOWN_MAINNET_CHAIN_IDS, Settings
from aegis.keeperhub.exceptions import (
    KeeperHubError,
    KeeperHubMcpConnectionError,
    KeeperHubMcpProtocolError,
    KeeperHubMcpTimeoutError,
)

if TYPE_CHECKING:
    from mcp import types as mcp_types

logger = logging.getLogger(__name__)

# Read/discovery only. Nothing in this set can move funds, sign, or
# broadcast a transaction.
ALLOWED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "execute_protocol_action",  # gated further below to read-only actionTypes
        "search_protocol_actions",
        "list_action_schemas",
        "get_spending_limits",
    }
)

# The only actionType values Hermes may pass to execute_protocol_action.
ALLOWED_READ_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "aave-v3/get-user-account-data",
        "aave-v3/get-user-reserve-data",
    }
)

_CHAIN_ID_ARG_NAMES = ("network", "chain_id", "chainId")


class ToolNotAllowedError(Exception):
    """Raised when a call falls outside Hermes's tool, action, or chain allowlist."""


class McpSession(Protocol):
    """The minimal MCP session surface this gateway depends on.

    A real implementation wraps the `mcp` SDK's ClientSession against
    KeeperHub's MCP endpoint (see create_default_session below); tests use
    a fake that implements just this surface, no network involved.
    """

    def list_tools(self) -> list[str]: ...

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]: ...


class HermesMcpGateway:
    """The only way Hermes reaches KeeperHub. Wraps an McpSession and
    enforces the allowlists described in this module's docstring."""

    def __init__(self, session: McpSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def list_tools(self) -> list[str]:
        """Only ever advertise the allowlisted subset, even if the
        underlying session exposes more tools than this."""
        return sorted(name for name in self._session.list_tools() if name in ALLOWED_TOOL_NAMES)

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        if name not in ALLOWED_TOOL_NAMES:
            raise ToolNotAllowedError(f"tool '{name}' is not in Hermes's allowlist")

        if name == "execute_protocol_action":
            action_type = arguments.get("actionType")
            if action_type not in ALLOWED_READ_ACTION_TYPES:
                raise ToolNotAllowedError(
                    f"actionType '{action_type}' is not a read action Hermes may call"
                )

        params = arguments.get("params")
        if isinstance(params, dict):
            self._check_chain_id(params)
        self._check_chain_id(arguments)

        return self._session.call_tool(name, arguments)

    def _check_chain_id(self, payload: dict[str, object]) -> None:
        for key in _CHAIN_ID_ARG_NAMES:
            if key not in payload:
                continue
            try:
                chain_id = int(payload[key])  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if chain_id in KNOWN_MAINNET_CHAIN_IDS:
                raise ToolNotAllowedError(
                    f"chain ID {chain_id} is a known mainnet chain ID; mainnet is disabled"
                )
            if chain_id not in self._settings.aegis_allowed_chain_ids:
                raise ToolNotAllowedError(f"chain ID {chain_id} is not in the allowed chain ID set")


class McpDiagnostics(BaseModel):
    """Connection diagnostics for a _LiveMcpSession, mirroring
    aegis.keeperhub.models.HealthCheckResult's shape for the REST client."""

    reachable: bool
    authenticated: bool
    endpoint: str
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    tool_count: int | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.reachable and self.authenticated


def _call_tool_result_to_dict(result: "mcp_types.CallToolResult") -> dict[str, object]:
    """Best-effort conversion of an MCP CallToolResult into the plain
    dict McpSession.call_tool must return. Prefers structured content
    when the server provides it (already-typed data); otherwise falls
    back to text content blocks, parsed as JSON when they look like it."""
    from mcp import types as mcp_types  # noqa: PLC0415 - see module-level lazy-import note

    if result.structured_content is not None:
        content: object = result.structured_content
    else:
        texts = [block.text for block in result.content if isinstance(block, mcp_types.TextContent)]
        raw: object = texts[0] if len(texts) == 1 else texts
        if isinstance(raw, str):
            try:
                content = json.loads(raw)
            except json.JSONDecodeError:
                content = raw
        else:
            content = raw
    return {"isError": result.is_error, "content": content}


class _LiveMcpSession:
    """Real MCP session against KeeperHub's remote MCP endpoint
    (settings.keeperhub_mcp_url), using the official MCP Python SDK's
    Streamable HTTP client transport — mcp.client.streamable_http's
    streamable_http_client + mcp.ClientSession, the transport/session API
    the SDK itself provides for remote HTTP MCP servers. Every method name
    and field used here (streamable_http_client, create_mcp_http_client,
    ClientSession.initialize/list_tools/call_tool, CallToolResult.content/
    structured_content/is_error, InitializeResult.protocol_version/
    server_info) was confirmed by inspecting the installed `mcp` package
    (see pyproject.toml's "hermes" extra for the pinned version) rather
    than assumed from memory or older SDK docs — see
    tests/hermes/test_live_mcp_session.py, which exercises this class
    against a real local MCP server.

    Authentication mirrors aegis.keeperhub.client.KeeperHubClient's REST
    calls exactly: the same KeeperHub organization API key
    (settings.keeperhub_api_key), sent as an `Authorization: Bearer`
    header on the underlying HTTP transport. No credential is ever read
    from, or written to, this repository — Settings loads it from the
    environment/.env, which is gitignored.

    HermesMcpGateway's McpSession Protocol is synchronous (Hermes's whole
    call path, including AnthropicLlmClient's tool loop, is synchronous);
    the mcp SDK is async-only end to end. Each list_tools()/call_tool()
    call bridges this with a single self-contained `asyncio.run()`: open
    the Streamable HTTP transport, open a ClientSession, initialize, do
    the one request, close — all inside one asyncio Task. This is
    deliberately not a persistent cross-call session kept alive on a
    background event-loop thread: an earlier version of this class tried
    that, and closing a session opened in one task from a different task
    raised anyio's "Attempted to exit cancel scope in a different task
    than it was entered in" — anyio's cancel scopes (which the transport's
    internal task group relies on) are bound to the task that entered
    them. Reconnecting per call avoids that failure mode entirely by
    construction, at the cost of paying the initialize handshake on every
    call — an acceptable trade for Hermes's low-frequency, sequential tool
    use. A lock around every call guarantees at most one MCP request is
    ever in flight at a time either way, preserving this project's
    sequential-only tool-call invariant (see this module's docstring).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._call_lock = threading.Lock()  # serializes calls: sequential execution
        self._last_init_result: "mcp_types.InitializeResult | None" = None

    # -- one self-contained connect/operate/close cycle per call -------------

    async def _connected_session_do(self, coro_factory: "object") -> "object":
        """coro_factory: Callable[[ClientSession], Awaitable[object]]. Opens
        a fresh transport + session, initializes, runs coro_factory(session),
        and tears everything down — all within this single coroutine/task."""
        import contextlib

        import httpx2
        from mcp import ClientSession
        from mcp import types as mcp_types
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        headers = {
            "Authorization": f"Bearer {self._settings.keeperhub_api_key}",
            "Accept": "application/json, text/event-stream",
        }
        timeout = httpx2.Timeout(self._settings.keeperhub_timeout_seconds)
        http_client = create_mcp_http_client(headers=headers, timeout=timeout)
        endpoint = str(self._settings.keeperhub_mcp_url).rstrip("/")

        async with contextlib.AsyncExitStack() as stack:
            read_stream, write_stream = await stack.enter_async_context(
                streamable_http_client(endpoint, http_client=http_client)
            )
            session = await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=self._settings.keeperhub_timeout_seconds,
                    client_info=mcp_types.Implementation(name="aegis-hermes", version="0.1.0"),
                )
            )
            init_result = await session.initialize()
            self._last_init_result = init_result
            logger.info(
                "KeeperHub MCP request against %s (server=%s protocol=%s)",
                self._settings.keeperhub_mcp_url,
                init_result.server_info.name if init_result.server_info else "unknown",
                init_result.protocol_version,
            )
            return await coro_factory(session)  # type: ignore[operator]

    def _run(self, coro_factory: "object") -> "object":
        import asyncio

        import httpx2
        from mcp import MCPError

        timeout = self._settings.keeperhub_timeout_seconds
        with self._call_lock:
            try:
                return asyncio.run(
                    asyncio.wait_for(self._connected_session_do(coro_factory), timeout=timeout)
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:  # noqa: BLE001 - immediately re-classified below
                # anyio's internal task groups (inside streamable_http_client)
                # wrap failures raised within them in a BaseExceptionGroup —
                # e.g. a connection refused surfaces as
                # ExceptionGroup(httpx2.ConnectError). Unwrap to the real
                # cause before classifying it.
                cause = exc
                while isinstance(cause, BaseExceptionGroup) and cause.exceptions:
                    cause = cause.exceptions[0]

                # httpx2.TimeoutException is itself a subclass of
                # httpx2.HTTPError, so it (and asyncio.wait_for's own
                # TimeoutError) must be checked before the broader
                # HTTPError branch below.
                if isinstance(cause, (TimeoutError, httpx2.TimeoutException)):
                    raise KeeperHubMcpTimeoutError(
                        f"KeeperHub MCP request to {self._settings.keeperhub_mcp_url} did not "
                        f"complete within {timeout}s"
                    ) from exc
                if isinstance(cause, MCPError):
                    raise KeeperHubMcpProtocolError(
                        f"KeeperHub MCP server returned an error: {cause}"
                    ) from exc
                if isinstance(cause, httpx2.HTTPError):
                    raise KeeperHubMcpConnectionError(
                        f"Could not reach KeeperHub MCP endpoint {self._settings.keeperhub_mcp_url}: "
                        f"{cause}"
                    ) from exc
                raise

    # -- McpSession protocol --------------------------------------------------

    def list_tools(self) -> list[str]:
        result = self._run(lambda session: session.list_tools())
        return [tool.name for tool in result.tools]  # type: ignore[attr-defined]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = self._run(lambda session: session.call_tool(name, arguments))
        return _call_tool_result_to_dict(result)  # type: ignore[arg-type]

    def diagnostics(self) -> McpDiagnostics:
        """Connection diagnostics: connect, authenticate, and list tools —
        reachability and auth are inseparable for MCP's Streamable HTTP
        transport (the initialize handshake itself fails on a bad API
        key), unlike the REST client's two-stage health check."""
        endpoint = str(self._settings.keeperhub_mcp_url)
        try:
            tools = self._run(lambda session: session.list_tools())
        except KeeperHubError as exc:
            return McpDiagnostics(reachable=False, authenticated=False, endpoint=endpoint, detail=str(exc))

        server_info = self._last_init_result.server_info if self._last_init_result else None
        return McpDiagnostics(
            reachable=True,
            authenticated=True,
            endpoint=endpoint,
            protocol_version=self._last_init_result.protocol_version if self._last_init_result else None,
            server_name=server_info.name if server_info else None,
            server_version=server_info.version if server_info else None,
            tool_count=len(tools.tools),  # type: ignore[attr-defined]
        )


def create_default_session(settings: Settings) -> McpSession:
    """Build the real MCP session against settings.keeperhub_mcp_url —
    KeeperHub's Streamable HTTP MCP endpoint, per the official MCP Python
    SDK's client transport for remote HTTP servers. See _LiveMcpSession's
    docstring for exactly which SDK APIs this uses and how they were
    confirmed."""
    return _LiveMcpSession(settings)
