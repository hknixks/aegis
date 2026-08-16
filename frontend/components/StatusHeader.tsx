import type { DashboardState } from "@/lib/types";
import { RiskBadge } from "./RiskBadge";

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-widest text-console-muted">{label}</span>
      <span className="font-mono text-lg text-console-text">{value}</span>
    </div>
  );
}

// Four distinct concepts, each shown on its own — never collapsed into a
// single "Resolved". See aegis.api._system_status/_incident_state/
// _run_state for the backend source of truth these mirror verbatim.
const INCIDENT_META: Record<string, string> = {
  NO_ACTIVE_INCIDENT: "text-console-muted border-console-border",
  ACTIVE: "text-risk-atrisk border-risk-atrisk/40",
  RECOVERING: "text-risk-atrisk border-risk-atrisk/40",
  RESOLVED: "text-risk-safe border-risk-safe/40",
  FAILED: "text-risk-critical border-risk-critical/40",
  UNCERTAIN: "text-risk-high border-risk-high/40",
};

const RUN_META: Record<string, string> = {
  RUNNING: "text-risk-atrisk border-risk-atrisk/40",
  DRY_RUN_COMPLETE: "text-console-muted border-console-border",
  EXECUTION_COMPLETE: "text-risk-safe border-risk-safe/40",
  FAILED: "text-risk-critical border-risk-critical/40",
  STOPPED: "text-console-muted border-console-border",
};

function StatePill({
  value,
  palette,
  testId,
}: {
  value: string;
  palette: Record<string, string>;
  testId?: string;
}) {
  const className = palette[value] ?? "text-console-muted border-console-border";
  return (
    <span
      className={`inline-flex w-fit items-center rounded border px-2 py-0.5 font-mono text-sm font-semibold uppercase tracking-wide ${className}`}
      data-testid={testId}
    >
      {value.replace(/_/g, " ")}
    </span>
  );
}

export function StatusHeader({ state }: { state: DashboardState }) {
  return (
    <header
      className="rounded-lg border border-console-border bg-console-panel p-6"
      data-testid="status-header"
    >
      <div className="mb-6 flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold text-console-text">Aegis</h1>
          <p className="text-sm text-console-muted">Execution-Aware DeFi Guardian</p>
        </div>
        <span className="rounded border border-console-border px-3 py-1 text-xs font-semibold uppercase tracking-widest text-console-muted">
          {state.status}
        </span>
      </div>

      {/* The four separated states, each its own field — this is the fix
          for collapsing everything into a single "Resolved" label. */}
      <div className="mb-6 grid grid-cols-2 gap-4 border-b border-console-border pb-6 sm:grid-cols-4" data-testid="state-rows">
        <Field label="System" value={<StatePill value={state.system_status} palette={{}} testId="system-status-value" />} />
        <Field label="Position" value={<RiskBadge tier={state.risk_tier} />} />
        <Field
          label="Incident"
          value={<StatePill value={state.incident_state} palette={INCIDENT_META} testId="incident-state-value" />}
        />
        <Field label="Run" value={<StatePill value={state.run_state} palette={RUN_META} testId="run-state-value" />} />
      </div>

      <div className="grid grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-5">
        <Field label="Network" value={state.network === "84532" ? "Base Sepolia" : state.network} />
        <Field
          label="Wallet"
          value={
            state.wallet ? (
              <span title={state.wallet}>
                {state.wallet.slice(0, 6)}…{state.wallet.slice(-4)}
              </span>
            ) : (
              "N/A"
            )
          }
        />
        <Field label="Protocol" value={state.protocol} />
        <Field
          label="Health Factor"
          value={
            <span data-testid="status-header-health-factor">
              {state.no_debt ? "No debt" : state.health_factor ?? "N/A"}
            </span>
          }
        />
        <Field label="Risk Level" value={state.risk_level ?? "N/A"} />
      </div>
      <div className="mt-6 flex flex-wrap items-center gap-4 border-t border-console-border pt-4">
        <Field
          label="Run ID"
          value={
            state.run_id ? (
              <span className="text-sm" title={state.run_id} data-testid="run-id-value">
                {state.run_id}
              </span>
            ) : (
              "N/A"
            )
          }
        />
        <Field
          label="Stage"
          value={
            <span data-testid="stage-value" className={state.running ? "text-risk-atrisk" : ""}>
              {state.stage ?? "N/A"}
              {state.running && <span className="ml-1 animate-pulse">●</span>}
            </span>
          }
        />
      </div>
    </header>
  );
}
