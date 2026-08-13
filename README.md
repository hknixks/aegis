# Aegis

Autonomous DeFi risk management agent. Aegis detects risky lending
positions, decides on a defensive action, executes that action through
**KeeperHub**, and verifies the result onchain.

## Architecture rule

**KeeperHub is the only onchain execution layer.** Aegis never holds a
private key and never constructs, signs, or broadcasts a transaction
itself. All execution is delegated to KeeperHub's REST API (and, in a
later phase, its MCP server) using an organization API key.

## Phase 1 scope

This phase ships the scaffold only, not the agent:

- Clean Python project (`src/aegis` layout)
- Configuration from environment variables (`aegis/config.py`)
- A read-only KeeperHub integration layer (`aegis/keeperhub/`) — current
  user lookup, chain catalog, health check. No execute-* wrappers yet.
- Structured logging (`aegis/logging_config.py`)
- A health-check CLI command: `aegis health`

Decision-making, risk detection, and any onchain execution wrappers are
out of scope for Phase 1.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# edit .env and set KEEPERHUB_API_KEY to a real kh_... organization key
```

## Usage

```bash
aegis health
```

This checks, in order:
1. **Reachability** — `GET /api/chains` (no auth required)
2. **Authentication** — `GET /api/user` (requires a valid `kh_` API key)

Exit code `0` means both passed; `1` means something failed, with detail
printed to stdout.

## Configuration

All configuration lives in environment variables — see [`.env.example`](.env.example)
for the full list and defaults. Notable ones:

| Variable | Purpose |
|---|---|
| `KEEPERHUB_API_KEY` | Organization API key (`kh_...`). Required. Never a private key. |
| `KEEPERHUB_BASE_URL` | KeeperHub REST base URL. Defaults to `https://app.keeperhub.com`. |
| `KEEPERHUB_MCP_URL` | KeeperHub MCP endpoint, reserved for a later phase. |
| `AEGIS_ALLOWED_CHAIN_IDS` | Testnet chain ID allowlist. Config rejects known mainnet IDs outright. |
| `AEGIS_LOG_LEVEL` / `AEGIS_LOG_FORMAT` | Logging verbosity and output format (`text` or `json`). |

## Safety guardrails baked into this phase

- No private key anywhere in config or code — only a KeeperHub API key.
- `Settings` rejects any `AEGIS_ALLOWED_CHAIN_IDS` entry that is a known
  mainnet chain ID, so mainnet is structurally unreachable even before
  any execution logic exists.
- The KeeperHub client (`aegis/keeperhub/client.py`) exposes only
  read/introspection calls. There is no code path in this repo that
  builds, signs, or sends a transaction — that stays entirely inside
  KeeperHub.

## Tests

```bash
pytest
```

All HTTP calls in tests are mocked with `respx`; no live network access
or real credentials are required to run the suite.

## Future phases (not implemented yet)

- Wrap KeeperHub's Direct Execution API (`/api/execute/*`) with
  `simulate: true` support, gated by `AEGIS_ALLOWED_CHAIN_IDS`.
- Add a KeeperHub MCP client using `KEEPERHUB_MCP_URL` so the agent can
  be driven as MCP tools.
- Risk detection for lending positions and the decision layer that picks
  a defensive action.
- Onchain result verification after a KeeperHub execution completes.
