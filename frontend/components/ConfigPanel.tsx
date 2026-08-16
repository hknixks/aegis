import type { DashboardState } from "@/lib/types";

// Read-only. There is no editable settings form here on purpose — every
// value that matters (spending limits, allowed chains/protocols, the
// KeeperHub credential) is server-side configuration this browser is
// never allowed to read or change. See aegis.config.Settings /
// aegis.policy.PolicyEngine.
export function ConfigPanel({ state }: { state: DashboardState | null }) {
  return (
    <section id="settings" className="rounded-lg border border-console-border bg-console-panel p-6" data-testid="config-panel">
      <h2 className="mb-1 text-sm font-semibold uppercase tracking-widest text-console-muted">Configuration</h2>
      <p className="mb-4 max-w-2xl text-xs text-console-muted">
        Aegis has no user-editable settings in this browser. Spending limits, allowed chains and
        protocols, the wallet PolicyEngine will approve, and the KeeperHub credential are all
        server-side configuration — changing them here would defeat the point of a deterministic
        policy gate. This panel only shows what the current run is already telling you.
      </p>
      <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Protocol</dt>
          <dd className="font-mono text-console-text">{state?.protocol ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Network</dt>
          <dd className="font-mono text-console-text">
            {state?.network === "84532" ? "Base Sepolia (84532)" : state?.network ?? "—"}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Mode</dt>
          <dd className="font-mono text-console-text">{state?.mode ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Executed through</dt>
          <dd className="font-mono text-console-text">{state?.execution.executed_through ?? "KeeperHub"}</dd>
        </div>
      </dl>
    </section>
  );
}
