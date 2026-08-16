import type { Mode } from "@/lib/types";

const MODES: { mode: Mode; label: string; testId: string }[] = [
  { mode: "fixture", label: "Fixture Demo", testId: "mode-toggle-fixture" },
  { mode: "live_dry_run", label: "Live Dry Run", testId: "mode-toggle-live-dry-run" },
  { mode: "live_execution", label: "Live Execution", testId: "mode-toggle-live-execution" },
];

export function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  return (
    <div className="flex items-center gap-3" data-testid="mode-toggle">
      <div className="flex overflow-hidden rounded border border-console-border">
        {MODES.map((m) => (
          <button
            key={m.mode}
            type="button"
            onClick={() => onChange(m.mode)}
            data-testid={m.testId}
            aria-pressed={mode === m.mode}
            className={`px-3 py-1.5 text-xs font-semibold uppercase tracking-widest transition-colors ${
              mode === m.mode
                ? m.mode === "live_execution"
                  ? "bg-risk-critical/20 text-risk-critical"
                  : m.mode === "live_dry_run"
                    ? "bg-risk-safe/20 text-risk-safe"
                    : "bg-risk-atrisk/20 text-risk-atrisk"
                : "text-console-muted hover:text-console-text"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
      {mode === "fixture" && (
        <span
          className="rounded border border-risk-atrisk/40 bg-risk-atrisk/10 px-2 py-1 text-[11px] font-bold uppercase tracking-widest text-risk-atrisk"
          data-testid="demo-data-banner"
        >
          ⚠ DEMO DATA: Not Real Blockchain Activity
        </span>
      )}
      {mode === "live_dry_run" && (
        <span
          className="rounded border border-risk-safe/40 bg-risk-safe/10 px-2 py-1 text-[11px] font-bold uppercase tracking-widest text-risk-safe"
          data-testid="live-dry-run-banner"
        >
          ● LIVE DRY RUN: Real Backend Data, Never Sends
        </span>
      )}
      {mode === "live_execution" && (
        <span
          className="rounded border border-risk-critical/40 bg-risk-critical/10 px-2 py-1 text-[11px] font-bold uppercase tracking-widest text-risk-critical"
          data-testid="live-execution-banner"
        >
          ▲ LIVE EXECUTION: A Real Base Sepolia Transaction
        </span>
      )}
    </div>
  );
}
