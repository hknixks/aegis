import type { DashboardVerification } from "@/lib/types";
import { RiskBadge } from "./RiskBadge";

export function VerificationPanel({ verification }: { verification: DashboardVerification }) {
  const hasAfter = verification.after_health_factor !== null;

  return (
    <section
      className="rounded-lg border border-console-border bg-console-panel p-6"
      data-testid="verification-panel"
    >
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-console-muted">
        Verification
      </h2>
      <div className="grid grid-cols-2 gap-6">
        <div>
          <div className="mb-2 text-[11px] uppercase tracking-widest text-console-muted">Before</div>
          <div className="font-mono text-2xl text-console-text">
            {verification.before_health_factor ?? "—"}
          </div>
          <div className="mt-1">
            <RiskBadge tier={verification.before_risk} />
          </div>
        </div>
        <div>
          <div className="mb-2 text-[11px] uppercase tracking-widest text-console-muted">After</div>
          <div className="font-mono text-2xl text-console-text">
            {verification.after_health_factor ?? "pending"}
          </div>
          <div className="mt-1">
            <RiskBadge tier={verification.after_risk} />
          </div>
        </div>
      </div>

      <div className="mt-5 flex flex-col gap-2 border-t border-console-border pt-4">
        {hasAfter && (
          <div className="flex items-center gap-2 text-sm" data-testid="risk-reduced">
            <span aria-hidden="true">{verification.risk_reduced ? "✓" : "✕"}</span>
            Risk Reduced: {verification.risk_reduced ? "Yes" : "No"}
          </div>
        )}
        {verification.incident_resolved ? (
          <div className="flex items-center gap-2 text-sm font-semibold text-risk-safe" data-testid="incident-resolved">
            <span aria-hidden="true">✓</span>
            Incident Resolved
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm font-semibold text-risk-atrisk" data-testid="incident-not-resolved">
            <span aria-hidden="true">↻</span>
            Incident Not Resolved — Re-Planning
          </div>
        )}
      </div>
    </section>
  );
}
