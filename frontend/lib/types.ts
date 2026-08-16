// Mirrors src/aegis/api.py's DashboardState pydantic schema exactly.
// This file has no logic of its own — it exists so the frontend never
// has to guess the backend's shape. If the backend schema changes, this
// file changes with it; the two must never drift silently.

// Matches aegis.demo_orchestrator.DemoMode. "live_execution" is never
// startable from this app (POST /api/runs only accepts "fixture" |
// "live_dry_run" — see StartRunMode below) but a run started by
// `aegis live-demo --confirm` reports this mode when polled by run_id.
export type Mode = "fixture" | "live_dry_run" | "live_execution";

// The only two modes this frontend can ever ask the backend to start.
export type StartRunMode = "fixture" | "live_dry_run";

// The USER WALLET: whichever address a browser wallet extension reported
// via eth_requestAccounts (see lib/wallet.ts) — public address only,
// read-only, used only to pick which position to monitor. This is never
// an execution identity and carries no signature, session, or delegation
// of any kind. Mirrors aegis.api.UserWallet.
export interface UserWallet {
  address: string;
  chain: string;
  connected: boolean;
}

// Where DashboardState.wallet actually came from — "connected" (this
// USER WALLET), "dev_default" (the server's AEGIS_EXPECTED_WALLET_ADDRESS,
// used because no wallet was connected), or "fixture" (FIXTURE mode's
// fixed demo wallet, unrelated to any real wallet — see
// aegis.demo_orchestrator.start_run's FIXTURE branch). Never inferred
// from the address string itself.
export type WalletSource = "connected" | "dev_default" | "fixture";

export interface DashboardCandidate {
  action: string;
  asset: string | null;
  amount: string | null;
  financial_score: string;
  execution_score: string;
  combined_score: string | null;
  eligible: boolean;
  final_status: string | null;
  simulation_status: string;
  rejection_reason: string | null;
}

export interface DashboardExplanation {
  selected_action: string;
  financial_score: string;
  execution_score: string;
  combined_score: string | null;
  expected_risk_reduction: string;
  rejected_candidates: string[];
  rejection_reasons: Record<string, string>;
  selection_reason: string;
}

export type ExecutionStatus =
  | "not_attempted"
  | "pending"
  | "completed"
  | "failed"
  | "uncertain";

export interface DashboardExecution {
  simulation_status: string;
  would_revert: boolean | null;
  gas_estimate: string | null;
  policy_approved: boolean | null;
  execution_status: ExecutionStatus;
  execution_id: string | null;
  transaction_hash: string | null;
  explorer_url: string | null;
  executed_through: "KeeperHub";
}

export interface DashboardVerification {
  before_health_factor: string | null;
  before_no_debt: boolean;
  before_risk: string | null;
  after_health_factor: string | null;
  after_no_debt: boolean | null;
  after_risk: string | null;
  risk_reduced: boolean | null;
  incident_resolved: boolean;
}

export interface DashboardAuditEvent {
  run_id: string;
  timestamp: string;
  stage: string;
  detail: Record<string, unknown>;
}

export interface DashboardRecoveryStep {
  action: string;
  amount: string | null;
  simulation_status: string;
  outcome: "rejected" | "selected";
  reason: string | null;
}

export interface DashboardPosition {
  collateral: string | null;
  debt: string | null;
  health_factor: string | null;
  no_debt: boolean;
  risk_level: string | null;
  risk_threshold: string | null;
  timestamp: string | null;
  last_update: string | null;
}

// What Aegis is doing right now, independent of outcome.
export type SystemStatus = "MONITORING" | "ANALYZING" | "INTERVENING" | "VERIFYING";

// The position's own risk tier — distinct from RiskTier below only in
// that this is the raw backend enum name; kept as the same union.
export type PositionState = "NO_POSITION" | "SAFE" | "AT_RISK" | "HIGH" | "CRITICAL";

// Whether there is (or was) a real risk incident, and what happened to it.
// NO_ACTIVE_INCIDENT covers both "never at risk" and "no read yet" — a
// SAFE or no-debt position must never be reported as RESOLVED, since
// there was nothing to resolve.
export type IncidentState =
  | "NO_ACTIVE_INCIDENT"
  | "ACTIVE"
  | "RECOVERING"
  | "RESOLVED"
  | "FAILED"
  | "UNCERTAIN";

// What kind of run this was and whether it actually broadcast anything.
export type RunStateValue =
  | "RUNNING"
  | "DRY_RUN_COMPLETE"
  | "EXECUTION_COMPLETE"
  | "FAILED"
  | "STOPPED";

export interface DashboardState {
  mode: Mode;
  run_id: string | null;
  running: boolean;
  stage: string | null;
  generated_at: string;
  status: string;
  system_status: SystemStatus;
  incident_state: IncidentState;
  run_state: RunStateValue;
  network: string;
  chain_id: number;
  wallet: string | null;
  wallet_source: WalletSource;
  protocol: string;
  health_factor: string | null;
  no_debt: boolean;
  risk_level: string | null;
  risk_tier: PositionState | null;
  position: DashboardPosition;
  candidates: DashboardCandidate[];
  explanation: DashboardExplanation | null;
  execution: DashboardExecution;
  verification: DashboardVerification;
  audit_timeline: DashboardAuditEvent[];
  recovery_steps: DashboardRecoveryStep[];
  rounds: number;
  error: string | null;
}
