const STATUS_META: Record<string, { symbol: string; className: string }> = {
  eligible: { symbol: "✓", className: "text-risk-safe border-risk-safe/40 bg-risk-safe/10" },
  rejected: { symbol: "✕", className: "text-risk-critical border-risk-critical/40 bg-risk-critical/10" },
  selected: { symbol: "★", className: "text-risk-safe border-risk-safe/40 bg-risk-safe/10" },
  pending: { symbol: "…", className: "text-console-muted border-console-border bg-console-panel" },
  uncertain: { symbol: "?", className: "text-risk-atrisk border-risk-atrisk/40 bg-risk-atrisk/10" },
};

export function StatusPill({
  kind,
  label,
}: {
  kind: keyof typeof STATUS_META;
  label: string;
}) {
  const meta = STATUS_META[kind];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${meta.className}`}
      data-testid="status-pill"
      data-kind={kind}
    >
      <span aria-hidden="true">{meta.symbol}</span>
      {label}
    </span>
  );
}
