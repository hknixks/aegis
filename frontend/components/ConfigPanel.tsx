import type { DashboardState } from "@/lib/types";

// Read-only, on purpose. Every value that matters (spending limits,
// allowed chains and protocols, the KeeperHub key) is set on the server.
// This browser is never allowed to read or change them. See
// aegis.config.Settings and aegis.policy.PolicyEngine.
export function ConfigPanel({ state }: { state: DashboardState | null }) {
  return (
    <section id="settings" className="rounded-lg border border-console-border bg-console-panel p-6" data-testid="config-panel">
      <h2 className="mb-1 text-sm font-semibold uppercase tracking-widest text-console-muted">Configuration</h2>
      <p className="mb-4 max-w-2xl text-xs text-console-muted">
        This page cannot change any settings. Spending limits, which chains and actions are
        allowed, and the KeeperHub key are all set on the server. This keeps the safety rules
        honest. This panel just shows what the current run already tells you.
      </p>
      <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Protocol</dt>
          <dd className="font-mono text-console-text">{state?.protocol ?? "N/A"}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Network</dt>
          <dd className="font-mono text-console-text">
            {state?.network === "84532" ? "Base Sepolia (84532)" : state?.network ?? "N/A"}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Mode</dt>
          <dd className="font-mono text-console-text">{state?.mode ?? "N/A"}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Executed through</dt>
          <dd className="font-mono text-console-text">{state?.execution.executed_through ?? "KeeperHub"}</dd>
        </div>
      </dl>
    </section>
  );
}
