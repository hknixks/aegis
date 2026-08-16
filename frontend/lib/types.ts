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
  before_risk: string | null;
  after_health_factor: string | null;
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
  risk_level: string | null;
  risk_threshold: string | null;
  timestamp: string | null;
  last_update: string | null;
}

export interface DashboardState {
  mode: Mode;
  run_id: string | null;
  running: boolean;
  stage: string | null;
  generated_at: string;
  status: string;
  incident_state: string;
  network: string;
  chain_id: number;
  wallet: string | null;
  protocol: string;
  health_factor: string | null;
  risk_level: string | null;
  risk_tier: "SAFE" | "AT_RISK" | "HIGH" | "CRITICAL" | null;
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
