import type { DashboardExecution } from "@/lib/types";
import { StatusPill } from "./StatusPill";

const EXECUTION_LABEL: Record<string, { kind: "eligible" | "rejected" | "pending" | "uncertain"; label: string }> = {
  not_attempted: { kind: "pending", label: "Not Attempted" },
  pending: { kind: "pending", label: "Pending" },
  completed: { kind: "eligible", label: "Completed" },
  failed: { kind: "rejected", label: "Failed" },
  uncertain: { kind: "uncertain", label: "Uncertain" },
};

export function ExecutionPanel({ execution }: { execution: DashboardExecution }) {
  const simPassed = execution.simulation_status === "PASSED";
  const status = EXECUTION_LABEL[execution.execution_status] ?? EXECUTION_LABEL.not_attempted;

  return (
    <section
      className="rounded-lg border border-console-border bg-console-panel p-6"
      data-testid="execution-panel"
    >
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-console-muted">
          Execution
        </h2>
        <span className="rounded border border-console-border px-2 py-0.5 text-[11px] uppercase tracking-widest text-console-muted">
          Executed through KeeperHub
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <div>
          <div className="text-[11px] uppercase tracking-widest text-console-muted">Simulation</div>
          <div className="font-mono text-console-text" data-testid="simulation-result">
            {execution.simulation_status === "NOT_APPLICABLE"
              ? "N/A"
              : execution.simulation_status === "SKIPPED"
                ? "Skipped"
                : simPassed
                  ? "✓ Passed"
                  : "✕ Failed"}
          </div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-widest text-console-muted">Gas Estimate</div>
          <div className="font-mono text-console-text">{execution.gas_estimate ?? "unknown"}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-widest text-console-muted">Policy</div>
          <div className="font-mono text-console-text">
            {execution.policy_approved === null
              ? "N/A"
              : execution.policy_approved
                ? "✓ Approved"
                : "✕ Rejected"}
          </div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-widest text-console-muted">Execution</div>
          <StatusPill kind={status.kind} label={status.label} />
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-widest text-console-muted">
            KeeperHub Execution ID
          </div>
          <div className="font-mono text-sm text-console-text" data-testid="execution-id">
            {execution.execution_id ?? "N/A"}
          </div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-widest text-console-muted">Transaction Hash</div>
          <div className="break-all font-mono text-sm text-console-text" data-testid="transaction-hash">
            {execution.transaction_hash ?? "N/A"}
          </div>
        </div>
      </div>

      {execution.explorer_url && (
        <a
          href={execution.explorer_url}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-4 inline-block rounded border border-console-border px-3 py-1.5 text-sm text-console-text hover:border-risk-safe/50"
          data-testid="explorer-link"
        >
          Open on Base Sepolia ↗
        </a>
      )}
    </section>
  );
}
