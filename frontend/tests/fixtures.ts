import type { DashboardState } from "@/lib/types";

// A complete, valid DashboardState used as a base for every test. Individual
// tests override only the fields relevant to the scenario under test, so
// each test's intent is legible from its overrides alone.
export function makeState(overrides: Partial<DashboardState> = {}): DashboardState {
  return {
    mode: "fixture",
    run_id: "run-1",
    running: false,
    stage: "RUN_RESOLVED",
    generated_at: "2026-08-15T12:00:00Z",
    status: "Resolved",
    incident_state: "RESOLVED",
    network: "84532",
    chain_id: 84532,
    wallet: "0xDEM0000000000000000000000000000000000000",
    protocol: "aave-v3",
    health_factor: "1.74",
    risk_level: "SAFE",
    risk_tier: "SAFE",
    position: {
      collateral: "1000.0",
      debt: "400.0",
      health_factor: "1.74",
      risk_level: "SAFE",
      risk_threshold: "1.2",
      timestamp: "2026-08-15T12:00:00Z",
      last_update: "2026-08-15T12:00:00Z",
    },
    candidates: [],
    explanation: null,
    execution: {
      simulation_status: "NOT_APPLICABLE",
      would_revert: null,
      gas_estimate: null,
      policy_approved: null,
      execution_status: "not_attempted",
      execution_id: null,
      transaction_hash: null,
      explorer_url: null,
      executed_through: "KeeperHub",
    },
    verification: {
      before_health_factor: null,
      before_risk: null,
      after_health_factor: null,
      after_risk: null,
      risk_reduced: null,
      incident_resolved: false,
    },
    audit_timeline: [],
    recovery_steps: [],
    rounds: 0,
    error: null,
    ...overrides,
  };
}
