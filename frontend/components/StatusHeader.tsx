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
      <div className="grid grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-7">
        <Field label="Status" value={state.status} />
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
        <Field label="Health Factor" value={state.health_factor ?? "N/A"} />
        <Field label="Risk" value={<RiskBadge tier={state.risk_tier} />} />
        <Field label="Incident" value={state.incident_state} />
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
