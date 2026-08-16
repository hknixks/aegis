import type { DashboardPosition } from "@/lib/types";
import { RiskBadge } from "./RiskBadge";

function HealthFactorGauge({ value, threshold }: { value: number | null; threshold: number | null }) {
  if (value === null) {
    return <div className="h-2 w-full rounded bg-console-border" data-testid="hf-gauge-empty" />;
  }
  // Visual scale: 0 -> 2.0x threshold. Clamped, purely illustrative.
  const max = threshold ? threshold * 2 : 2;
  const pct = Math.max(4, Math.min(100, (value / max) * 100));
  const thresholdPct = threshold ? Math.min(100, (threshold / max) * 100) : null;
  return (
    <div className="relative h-2 w-full rounded bg-console-border" data-testid="hf-gauge">
      <div
        className="h-2 rounded bg-gradient-to-r from-risk-critical via-risk-atrisk to-risk-safe"
        style={{ width: `${pct}%` }}
      />
      {thresholdPct !== null && (
        <div
          className="absolute top-[-2px] h-3 w-px bg-console-text"
          style={{ left: `${thresholdPct}%` }}
          title={`Threshold: ${threshold}`}
        />
      )}
    </div>
  );
}

export function RiskPanel({ position }: { position: DashboardPosition }) {
  const hf = position.health_factor !== null ? Number(position.health_factor) : null;
  const threshold = position.risk_threshold !== null ? Number(position.risk_threshold) : null;

  return (
    <section
      className="rounded-lg border border-console-border bg-console-panel p-6"
      data-testid="risk-panel"
    >
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-console-muted">
        Position Risk
      </h2>
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <div className="text-[11px] uppercase tracking-widest text-console-muted">Collateral</div>
          <div className="font-mono text-console-text">{position.collateral ?? "unknown"}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-widest text-console-muted">Debt</div>
          <div className="font-mono text-console-text">{position.debt ?? "unknown"}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-widest text-console-muted">Threshold</div>
          <div className="font-mono text-console-text">{position.risk_threshold ?? "—"}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-widest text-console-muted">Last Update</div>
          <div className="font-mono text-xs text-console-text">
            {position.last_update ? new Date(position.last_update).toLocaleString() : "—"}
          </div>
        </div>
      </div>

      <div className="mb-2 flex items-center justify-between">
        <span className="font-mono text-2xl text-console-text">{position.health_factor ?? "—"}</span>
        <RiskBadge tier={position.risk_level as string | null} />
      </div>
      <HealthFactorGauge value={hf} threshold={threshold} />
    </section>
  );
}
