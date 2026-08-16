// Presentational-only, like aegis.api's own _display_risk_tier: a coarse,
// 5-state summary of the backend's own `stage` string, for the nav bar's
// at-a-glance indicator. Never fed back into any decision — the detailed
// StatusHeader still shows the exact backend-reported stage/status text.
// Never invents a state the backend hasn't actually reported.
export type AgentStatus = "MONITORING" | "ANALYZING" | "INTERVENTING" | "VERIFYING" | "RESOLVED";

const ANALYZING_STAGES = new Set([
  "DETECTED",
  "ANALYZED",
  "EVALUATING",
  "CANDIDATE_SELECTED",
  "ALTERNATIVE_SELECTED",
  "SIMULATED",
  "SIMULATION_FAILED",
  "RECOVERY_STARTED",
  "POLICY_CHECK",
  "POLICY_REJECTED",
  "READY_TO_EXECUTE",
  "HERMES_CONSULTED",
  "PIPELINE_REPLANNING",
]);

const INTERVENTING_STAGES = new Set(["EXECUTING", "EXECUTED"]);

const VERIFYING_STAGES = new Set(["VERIFYING", "VERIFIED", "STATUS_CHECKED", "REASSESS_RISK", "ONCHAIN_STATE_INSPECTED"]);

const RESOLVED_STAGES = new Set([
  "RESOLVED",
  "RUN_RESOLVED",
  "PIPELINE_RESOLVED",
  "RUN_STOPPED",
  "PIPELINE_STOPPED",
  "NO_SAFE_ACTION",
  "FAILED",
  "UNCERTAIN",
  "STOPPED",
]);

export function deriveAgentStatus(stage: string | null, running: boolean): AgentStatus {
  if (!stage) return "MONITORING";
  if (!running) {
    // A terminal poll: RESOLVED covers every finished outcome (including
    // FAILED/UNCERTAIN/NO_SAFE_ACTION) — the exact outcome is still shown
    // verbatim elsewhere (StatusHeader's Status/Stage fields), this pill
    // is only ever "is the loop still active or not".
    return "RESOLVED";
  }
  if (INTERVENTING_STAGES.has(stage)) return "INTERVENTING";
  if (VERIFYING_STAGES.has(stage)) return "VERIFYING";
  if (RESOLVED_STAGES.has(stage)) return "RESOLVED";
  if (ANALYZING_STAGES.has(stage)) return "ANALYZING";
  return "ANALYZING";
}
