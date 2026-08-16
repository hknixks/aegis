// Risk state must never be communicated by color alone — each tier gets
// its own text label AND symbol, with color as a secondary reinforcement.
const TIER_META: Record<string, { symbol: string; className: string }> = {
  SAFE: { symbol: "●", className: "text-risk-safe border-risk-safe/40 bg-risk-safe/10" },
  AT_RISK: { symbol: "▲", className: "text-risk-atrisk border-risk-atrisk/40 bg-risk-atrisk/10" },
  HIGH: { symbol: "▲▲", className: "text-risk-high border-risk-high/40 bg-risk-high/10" },
  CRITICAL: { symbol: "✕", className: "text-risk-critical border-risk-critical/40 bg-risk-critical/10" },
};

export function RiskBadge({ tier }: { tier: string | null | undefined }) {
  const key = tier && TIER_META[tier] ? tier : "AT_RISK";
  const meta = TIER_META[key];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-semibold tracking-wide ${meta.className}`}
      data-testid="risk-badge"
      data-tier={tier ?? "UNKNOWN"}
    >
      <span aria-hidden="true">{meta.symbol}</span>
      {tier ?? "UNKNOWN"}
    </span>
  );
}
