# Aegis — Execution-Aware DeFi Guardian

Aegis is an execution-aware DeFi guardian that chooses the safest viable
intervention, executes it through KeeperHub, and verifies that the risk
was actually reduced.

**Agents decide. KeeperHub executes and provides the reliable last mile.**

## Problem

Most "AI DeFi agent" demos stop at the decision: a model looks at some
numbers and says "repay debt." An agent that stops there can still fail,
in two specific ways:

1. **Nobody checks whether that action can actually be executed safely.**
   The financially optimal action might fail simulation, exceed a
   spending limit, target the wrong network, or simply revert onchain —
   and a naive agent finds this out only after it's too late to react.
2. **Nobody verifies the outcome.** The agent assumes its action worked
   because a transaction was submitted, without ever reading the position
   again to confirm the risk it was trying to fix is actually gone.

An agent can make the right DeFi decision and still fail if that decision
cannot safely execute onchain.

## Solution

Aegis is built around closing both gaps. For every incident, it generates
multiple candidate interventions and scores each on two independent axes
— **Financial Effectiveness** (how much risk the action reduces, net of
its capital cost) and **Execution Feasibility** (how likely it is to
actually succeed: policy compliance, simulation result, balance
sufficiency). The two scores combine multiplicatively, so a financially
brilliant action with a broken execution path can never outrank a
modestly good action that will actually go through.

> **Aegis does not simply choose the financially best action. It chooses
> the best action that can also be executed safely.**

Then: **Simulate → reject unsafe actions → select the best executable
action → execute through KeeperHub → verify the result.** Every
rejection is recorded with its specific reason, never silently dropped.

## How It Works

```
Detect  →  Analyze  →  Score  →  Simulate  →  Execute  →  Verify  →  Recover
```

1. **Detect** — read the live Aave V3 position through KeeperHub.
2. **Analyze** — compute the health factor and classify risk (SAFE / AT_RISK).
3. **Score** — generate every candidate (`REPAY_DEBT`, `ADD_COLLATERAL`,
   `DO_NOTHING`), score each on Financial Effectiveness and Execution
   Feasibility.
4. **Simulate** — run the leading candidate through KeeperHub's real
   simulation before ever proposing it for execution. Mandatory, not
   optional — a candidate whose simulation fails or would revert is
   marked ineligible and can never be selected.
5. **Execute** — once PolicyEngine approves the selected candidate and it
   carries a passing simulation, execute it through KeeperHub with a
   fresh idempotency key.
6. **Verify** — poll KeeperHub's status endpoint to a terminal,
   on-chain-reconciled result, then re-read the position from scratch and
   recompute risk. Never inferred from the submission response.
7. **Recover** — if a candidate fails simulation, execution, or policy,
   Aegis re-plans: the next-best remaining candidate is tried through the
   same gates, up to a bounded number of rounds. An execution whose
   outcome genuinely can't be determined is marked `UNCERTAIN` and never
   auto-retried — Aegis stops for operator attention rather than risking
   a double-spend.

## Why KeeperHub

**"Simulated through KeeperHub." "Executed through KeeperHub." "KeeperHub
Execution ID." "Verified Transaction."** KeeperHub is not decorative — it
is the only onchain execution layer this project ever uses. Aegis never
holds a private key and never constructs, signs, or broadcasts a
transaction itself.

**Hermes decides. Aegis policy controls what is allowed. KeeperHub
handles the onchain execution and reliability layer.**

- **Hermes** (`aegis.hermes`) is Aegis's LLM-driven READ → ANALYZE →
  DECIDE layer. Given a position summary, it returns exactly one
  `Intent` — a closed-schema proposal with no callable and no way to
  reach onchain execution on its own. Its only tool access
  (`HermesMcpGateway`) is read/discovery-only by construction: it
  allowlists tool names, restricts `execute_protocol_action` to two
  read-only Aave `actionType`s, and independently blocks any mainnet
  chain ID — enforced in code, not prompting.
- **KeeperHub's REST API** (`aegis.keeperhub.client.KeeperHubClient`) is
  the real write path: `simulate`, `execute`, and poll status. This is
  what `SimulationService`, `ExecutionService`, and `VerificationService`
  call — the *only* path from a decision to an onchain effect is
  `PolicyEngine → SimulationService → ExecutionService →
  KeeperHubClient → KeeperHub REST API`.

An LLM deciding "repay debt" is not the hard part. Reliably getting that
decision onto a real chain — with simulation, gas handling, retries,
idempotency, and a wallet Aegis never has to hold a key for — is. Aegis
delegates that entire "last mile" to KeeperHub so its own code never has
to become a wallet, a signer, or a broadcaster.

**Today, the pipeline that actually runs (CLI and dashboard alike) scores
and selects with a deterministic decision engine, not Hermes** — a
deliberate choice, not a shortfall: everything from POLICY CHECK onward
is plain, testable Python with no LLM anywhere in that call path, so
nothing about whether a transaction is safe to send ever depends on
trusting a model's judgment. Hermes is fully implemented and
independently tested (`aegis.hermes`, `tests/hermes/`) as the
LLM-driven analysis layer, and is the natural next integration point —
see [Limitations](#limitations).

## Architecture

```
                    ┌───────────────┐
                    │   Frontend    │   Next.js dashboard — visualization only.
                    │  (Next.js)    │   Cannot execute, hold keys, or bypass
                    └───────┬───────┘   anything below it.
                            │  HTTP GET/POST (start + poll a run — never execute)
                    ┌───────▼───────┐
                    │ Aegis Backend │   FastAPI (aegis.api) + CLI (aegis.cli),
                    │  (FastAPI/CLI)│   both thin callers of ONE orchestrator:
                    └───────┬───────┘   aegis.demo_orchestrator.start_run.
                            │
              ┌─────────────┼─────────────┐
              │             │             │
      ┌───────▼──────┐      │     ┌───────▼────────┐
      │    Hermes     │      │     │ Decision Engine │  Deterministic,
      │ (LLM, reads   │      │     │ (aegis.decision_ │  wired into the
      │  only, via    │      │     │    engine)        │  live pipeline.
      │ HermesMcpGateway)    │     └───────┬────────┘  Scores + selects.
      └───────────────┘      │             │
      Independently tested;   │     ┌───────▼────────┐
      not yet wired into      │     │ Policy Engine  │  Hard, deterministic
      run_pipeline.           │     │ (aegis.policy)  │  gate. No LLM here.
                              │     └───────┬────────┘
                              │             │
                              │     ┌───────▼────────┐
                              │     │  Simulation    │  Must pass before
                              │     │ (SimulationService)│ execution is even
                              │     └───────┬────────┘  considered.
                              │             │
                              └─────►┌──────▼──────────┐
                                     │   KeeperHub     │  The ONLY onchain
                                     │  (REST + MCP)   │  execution layer.
                                     └──────┬──────────┘
                                            │
                                     ┌──────▼──────────┐
                                     │   Base Sepolia   │
                                     └──────┬──────────┘
                                            │
                                     ┌──────▼──────────┐
                                     │  Verification    │  Fresh onchain read,
                                     │ (VerificationService)│ never assumed.
                                     └──────┬──────────┘
                                            │
                                     ┌──────▼──────────┐
                                     │   Audit Trail    │  Every stage, every
                                     │  (aegis.audit)    │  score, every reason,
                                     └──────┬──────────┘  one shared run_id.
                                            │
                                     back to Frontend (read-only, polled)
```

There is exactly one path from a decision to an onchain effect:
`PolicyEngine → SimulationService → ExecutionService → KeeperHubClient →
KeeperHub REST API`. The dashboard never touches that path either — it
only calls `aegis.demo_orchestrator.start_run` through two read-only/
non-executing modes (`GET`/`POST /api/runs`, FIXTURE and LIVE_DRY_RUN
only); there is no HTTP endpoint anywhere that can trigger a real
transaction. See [Safety Model](#safety-model).

### Simulation, execution, verification, recovery flows

- **Simulation** (mandatory): `SimulationService.simulate()` calls
  KeeperHub's real simulation for the candidate's protocol action. A
  candidate whose simulation fails or reports `wouldRevert` is marked
  ineligible with a recorded reason and is never selectable.
  `ExecutionService` structurally refuses to execute any candidate
  without an attached passing simulation.
- **Execution**: `ExecutionService.execute()` calls KeeperHub's real
  execution endpoint with a fresh idempotency key. Real execution only
  ever happens if `AEGIS_AUTONOMOUS_EXECUTION_ENABLED=true`; otherwise
  every run stops right after simulation.
- **Verification**: `VerificationService.verify()` polls KeeperHub's
  status endpoint to a terminal, onchain-reconciled state, then Aegis
  re-reads the position — a fresh read, never the predicted outcome —
  and recomputes risk. Only if the health factor has genuinely recovered
  is the incident marked `RESOLVED`.
- **Recovery**: if a candidate's simulation, execution, or policy check
  fails, Aegis re-plans with the next-best remaining candidate, up to
  `max_rounds`. Distinct failure categories (`SIMULATION_FAILURE`,
  `POLICY_REJECTION`, `EXECUTION_FAILURE`, `EXECUTION_TIMEOUT`,
  `EXECUTION_UNCERTAIN`, `VERIFICATION_FAILURE`, `RISK_NOT_RESOLVED`) each
  drive different behavior. `UNCERTAIN` never auto-retries.

## Safety Model

- **No private key, ever.** Aegis authenticates to KeeperHub with an
  organization API key (`kh_...`) only; `Settings` rejects anything that
  looks like a private key in that field. There is no signing code, no
  raw-RPC-write code, anywhere in this project (verified: no
  `eth_sendRawTransaction`/`web3.py`/private-key handling exists in `src/`).
- **Chain restrictions.** Mainnet is structurally unreachable, checked
  independently in three places: `PolicyEngine`, `HermesMcpGateway`, and
  `Settings`' own validators (`KNOWN_MAINNET_CHAIN_IDS`) — a mainnet
  chain ID can't even be *configured*, let alone reached.
- **Tool allowlists.** `HermesMcpGateway` allowlists MCP tool names,
  restricts `execute_protocol_action` to two read-only Aave `actionType`s,
  and independently checks any chain ID argument, in code.
- **Action allowlists.** `PolicyEngine` rejects any `protocol_action` or
  `decision` outside the configured closed sets
  (`AEGIS_ALLOWED_PROTOCOL_ACTIONS` / `AEGIS_ALLOWED_DECISIONS`).
- **Policy engine.** A pure, deterministic function of `(Intent,
  Settings)` — no network calls, no LLM, no randomness. Re-checks
  everything an `Intent`'s own validator already enforces, on the
  assumption upstream validation might not have run. A rejection is
  always a hard reject; the engine never rewrites or clamps a proposed
  amount.
- **Simulation requirement.** Mandatory and structurally enforced —
  `ExecutionService` cannot execute a candidate without a passing
  simulation result attached to it.
- **Spending limits.** `AEGIS_MAX_TX_AMOUNT` caps any single proposed
  amount; `PolicyEngine` hard-rejects anything over it.
- **Uncertain execution handling.** Every write carries a fresh
  idempotency key, so retrying a call never double-executes. Runs are
  idempotent by run ID — re-invoking a completed run returns its recorded
  result instead of re-running it. An outcome that genuinely can't be
  determined (e.g. `execute()` raised before an execution ID was ever
  obtained, or a verification timeout that's still non-terminal on a
  post-timeout check) is marked `UNCERTAIN` — a hard stop, never
  auto-retried, since guessing by trying something else could stack a
  second real transaction on an unresolved first one.
- **No secret ever reaches a response body or a log line.** Exceptions
  from KeeperHub calls are reduced to their type name before being
  returned to any client.
- **The dashboard is a read-only visualization layer with its own
  process boundary**, and cannot start a real execution — `POST
  /api/runs`'s request schema only accepts `"fixture"` or
  `"live_dry_run"` (a real, non-simulated transaction is CLI-only,
  requiring `aegis live-demo --confirm` at a terminal, gated by a
  9-check preflight, `AEGIS_AUTONOMOUS_EXECUTION_ENABLED=true`, and
  `--confirm`, all three).

Every one of the above is exercised directly by the test suite (`tests/`)
— see [Test results](#test-results) below.

## Live Demo

**No real, non-simulated transaction has been executed on Base Sepolia
in this project yet.** The configured demo wallet currently has no open
Aave V3 position (zero collateral, zero debt) on Base Sepolia, so there
is nothing for a real intervention to act on until a position is opened.
`aegis live-demo --confirm` is fully implemented, gated, and tested end
to end against the real KeeperHub REST API in dry-run form — it is one
funded testnet position and one `--confirm` away from a real transaction,
not blocked on any remaining code.

Once a real transaction is executed, this section will carry the run ID,
the KeeperHub execution ID, the transaction hash, and the explorer link,
straight from that run's audit trail.

## Demo

Three modes, one authoritative entrypoint
(`aegis.demo_orchestrator.start_run`) behind all of them:

| Mode | What it does | Can it broadcast? | CLI | Dashboard |
|---|---|---|---|---|
| **FIXTURE** | Canned KeeperHub responses; deterministic incident (`REPAY_DEBT` fails simulation → re-plan → `ADD_COLLATERAL` succeeds) | Never — structurally, a `MagicMock` KeeperHub client | `aegis fixture-demo` | ✅ auto-starts (default mode) |
| **LIVE DRY RUN** | Real KeeperHub reads/simulation/PolicyEngine | Never — `dry_run=True` forced unconditionally | `aegis live-dry-run` | ✅ auto-starts |
| **LIVE EXECUTION** | The one real, non-simulated Base Sepolia transaction | Only after preflight + `AEGIS_AUTONOMOUS_EXECUTION_ENABLED=true` + `--confirm` | `aegis live-demo --confirm` | 👁 view-only (paste the printed run ID) |

Backend:

```bash
pip install -e ".[api]"
uvicorn aegis.api:app --reload
```

Frontend:

```bash
cd frontend && npm install && npm run dev
# open http://localhost:3000
```

CLI:

```bash
aegis fixture-demo    # MODE 1 — no network access at all
aegis live-dry-run    # MODE 2 — real KeeperHub reads/simulation, never executes
aegis live-demo --confirm   # MODE 3 — real transaction (see Safety Model's gates)
```

### Hackathon demo script

1. **FIXTURE DEMO** — `aegis fixture-demo`, or open the dashboard
   (default mode). Narrate the complete decision/recovery story live:
   `REPAY_DEBT` fails simulation, Aegis re-plans, picks `ADD_COLLATERAL`,
   executes, verifies, resolves — the "⚠ DEMO DATA" banner stays up the
   whole time.
2. **LIVE DRY RUN** — switch the dashboard to Live Dry Run (or
   `aegis live-dry-run`). Show the real configured Aave V3 Base Sepolia
   position and real KeeperHub simulation results.
3. **LIVE EXECUTION** — `aegis live-demo --confirm` in a terminal;
   paste the printed run ID into the dashboard's Live Execution mode and
   watch it resolve live, with the real transaction hash and explorer
   link.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# edit .env — at minimum set KEEPERHUB_API_KEY to a real kh_... organization key
```

Optional extras:

```bash
pip install -e ".[hermes]"  # AnthropicLlmClient + real KeeperHub MCP session
pip install -e ".[api]"     # the dashboard's FastAPI backend
```

**KeeperHub authentication**: `KEEPERHUB_API_KEY` — an organization API
key (`kh_...`), never a private key. Run `aegis health` first — it checks
KeeperHub reachability and authentication before you touch anything else.

**Hermes setup** (optional — not required for the pipeline that actually
runs today, see [Why KeeperHub](#why-keeperhub)): set `ANTHROPIC_API_KEY`
and install the `hermes` extra to exercise `AnthropicLlmClient` +
`HermesMcpGateway`'s real, read-only MCP session against KeeperHub.

**Frontend setup**: `cd frontend && npm install`. The only backend
contact point is `NEXT_PUBLIC_AEGIS_API_URL` (default
`http://localhost:8000`) — a plain URL, never a secret; no KeeperHub
credential is ever defined as a browser-visible environment variable.

**Backend setup**: `pip install -e ".[api]"` then
`uvicorn aegis.api:app --reload`.

See [`.env.example`](.env.example) for the full, commented list of
environment variables. The ones that matter most:

| Variable | Purpose |
|---|---|
| `KEEPERHUB_API_KEY` | Organization API key (`kh_...`). Required. Never a private key. |
| `KEEPERHUB_BASE_URL` | KeeperHub REST base URL. |
| `KEEPERHUB_MCP_URL` | KeeperHub MCP endpoint (Hermes's read-only session). |
| `AEGIS_ALLOWED_CHAIN_IDS` | Testnet chain ID allowlist. Known mainnet IDs are rejected outright. |
| `AEGIS_EXPECTED_WALLET_ADDRESS` | The only wallet PolicyEngine will approve an `on_behalf_of` for. |
| `AEGIS_DEBT_ASSET` / `AEGIS_COLLATERAL_ASSET` | Assets the pipeline proposes repaying/supplying. |
| `AEGIS_MAX_TX_AMOUNT` | Blunt cap on any single proposed amount. |
| `AEGIS_HEALTH_FACTOR_THRESHOLD` | Health factor below which a position is `AT_RISK`. |
| `AEGIS_AUTONOMOUS_EXECUTION_ENABLED` | Master switch. `false` by default — every run stops after simulation until this is explicitly `true`. |
| `AEGIS_AUDIT_LOG_PATH` | JSON-lines audit file every run appends to (default `./aegis_runs.jsonl`); shared across processes so the dashboard can poll a CLI-started run. |
| `ANTHROPIC_API_KEY` | Hermes's real LLM client. Never a KeeperHub or wallet credential. |
| `NEXT_PUBLIC_AEGIS_API_URL` (frontend) | Plain URL to the Aegis backend. Never a secret. |

No secret is defined in this README or committed to the repository —
`.env` is gitignored; only `.env.example` (no real values) is tracked.

### Testnet setup

Aegis only ever operates on public testnets — **Base Sepolia (chain ID
84532)** by default; Ethereum Sepolia and Arbitrum Sepolia are also
allowlisted but unused by the current demo scenario. You'll need:

1. A KeeperHub account with an organization API key and a configured
   wallet integration that has some Base Sepolia ETH for gas.
2. An Aave V3 position on Base Sepolia (supply collateral, borrow against
   it) so there's a real health factor to monitor. KeeperHub's own
   workflow templates can set this up.
3. `AEGIS_EXPECTED_WALLET_ADDRESS`, `AEGIS_DEBT_ASSET`, and
   `AEGIS_COLLATERAL_ASSET` set to match that position.

### How to verify a transaction

Aegis never asks you to trust its own report. After a real execution:

1. The CLI/dashboard prints the **KeeperHub execution ID**, the
   **transaction hash**, and an **explorer URL**
   (`https://sepolia.basescan.org/tx/<hash>`).
2. `VerificationService` itself already polled KeeperHub's status
   endpoint to a terminal, onchain-reconciled state before reporting
   success — this isn't inferred from the submission response.
3. Open the explorer URL yourself and confirm the transaction exists,
   succeeded, and touched the expected Aave V3 pool contract on Base
   Sepolia.
4. Cross-check the audit trail (`aegis.audit`) — every stage from
   `DETECTED` through `RESOLVED` is recorded with the scores and reasons
   behind it, all under one run ID.

### Tests

```bash
pytest              # backend
cd frontend && npm test   # frontend
```

All HTTP calls in the backend test suite are mocked with `respx`; no live
network access or real credentials are required. A small, clearly-marked
subset (`test_pipeline_live.py`, `test_live_mcp_session.py`) exercises the
real KeeperHub REST/MCP integration and needs a configured `.env` — not
part of the default safety net, skipped automatically when credentials
aren't present.

## Limitations

- **No live transaction executed yet.** See [Live Demo](#live-demo).
- **Hermes is not wired into the live pipeline.** It is fully
  implemented and independently tested, but `run_pipeline` (the CLI and
  dashboard's shared entrypoint) scores and selects with the
  deterministic `decision_engine`, not `HermesAgent`. Wiring Hermes's
  `Intent` output into `run_with_recovery` is the natural next step —
  everything downstream of an `Intent` (PolicyEngine, simulation,
  execution, verification) is already protocol/decision-source-agnostic.
- **No price oracle / per-asset decimals conversion.** Candidate amounts
  and capital costs are computed in Aave's base-currency units directly,
  not converted to a specific asset's native decimals — a real
  repay/supply amount is likely wrong-magnitude until this is added.
  Mandatory simulation is the safety net that catches this before
  anything broadcasts.
- **Position risk isn't re-checked immediately before execution.** A run
  reads the position once at the start and reuses that snapshot through
  any recovery rounds, re-reading fresh only after execution. KeeperHub's
  own real-time simulation is still the final gate before anything
  executes.
- **Binary risk classification.** `aegis.risk.RiskLevel` is `SAFE` /
  `AT_RISK` only; the dashboard's 4-tier gradient is presentational only.
- **Two-candidate action set, Base Sepolia only, Aave V3 only.** Only
  `REPAY_DEBT` and `ADD_COLLATERAL` (plus `DO_NOTHING`); one Aave V3
  position per run; no portfolio view, no cross-protocol migration.
- **Run registry is in-process, in-memory.** Restarting the dashboard API
  forgets any run it started (the run's own audit trail on disk
  survives; only the live/`running` polling view does not).
- **`aegis run` is a legacy command** that executes for real behind only
  `AEGIS_AUTONOMOUS_EXECUTION_ENABLED`, with neither a mandatory
  preflight check nor `--confirm` — weaker guards than `aegis live-demo
  --confirm`'s. Kept for backward compatibility, not recommended.

## Future Work

- Wire Hermes's `Intent` output into `run_with_recovery` as an
  alternative to (or ensemble with) the deterministic decision engine.
- Per-asset decimals conversion for real amounts.
- Persist the run registry (replace in-memory `RunHandle` dict) so a
  dashboard restart doesn't lose in-flight run visibility.
- Additional protocols/actions beyond Aave V3 repay/supply (Morpho,
  collateral-swap, cross-protocol migration) — the execution-aware
  scoring and safety model are already protocol-agnostic.
- Portfolio-level view across multiple positions/wallets.
