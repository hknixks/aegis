import type { DashboardRecoveryStep } from "@/lib/types";

export function RecoveryVisualization({ steps }: { steps: DashboardRecoveryStep[] }) {
  if (steps.length <= 1) return null; // nothing to recover from — only show when it's meaningful

  return (
    <section
      className="rounded-lg border border-console-border bg-console-panel p-6"
      data-testid="recovery-visualization"
    >
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-console-muted">
        Recovery
      </h2>
      <div className="flex flex-col items-start gap-2">
        {steps.map((step, index) => (
          <div key={`${step.action}-${index}`} className="flex w-full flex-col gap-2">
            <div
              className={`w-full rounded-md border p-3 ${
                step.outcome === "selected"
                  ? "border-risk-safe/50 bg-risk-safe/5"
                  : "border-risk-critical/40 bg-risk-critical/5"
              }`}
              data-testid="recovery-step"
              data-outcome={step.outcome}
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-console-text">
                  {step.action.replace("_", " ")}
                  {step.amount ? ` — ${step.amount}` : ""}
                </span>
                <span className="text-xs font-semibold uppercase tracking-widest">
                  {step.outcome === "selected" ? (
                    <span className="text-risk-safe">Simulation: Passed</span>
                  ) : (
                    <span className="text-risk-critical">Simulation: Failed</span>
                  )}
                </span>
              </div>
              {step.reason && <p className="mt-1 text-xs text-console-muted">{step.reason}</p>}
            </div>
            {index < steps.length - 1 && (
              <div className="pl-3 text-xs text-console-muted">
                ↓ Aegis re-evaluated alternatives
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
