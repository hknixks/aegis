import type { DashboardExplanation } from "@/lib/types";

// Every sentence and number here comes straight from
// DashboardExplanation, which is built server-side by
// aegis.decision_engine.build_explanation from already-computed scores —
// see src/aegis/api.py. This component never invents wording or numbers;
// it only lays out fields the backend already decided.
export function ExplanationPanel({ explanation }: { explanation: DashboardExplanation | null }) {
  if (!explanation) {
    return (
      <section
        className="rounded-lg border border-console-border bg-console-panel p-6"
        data-testid="explanation-panel"
      >
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-widest text-console-muted">
          Why Aegis Chose This
        </h2>
        <p className="text-sm text-console-muted">No action has been selected yet.</p>
      </section>
    );
  }

  return (
    <section
      className="rounded-lg border border-risk-safe/30 bg-console-panel p-6"
      data-testid="explanation-panel"
    >
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-widest text-console-muted">
        Why Aegis Chose This
      </h2>
      <p className="mb-4 text-base leading-relaxed text-console-text" data-testid="selection-reason">
        {explanation.selection_reason}
      </p>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Selected</dt>
          <dd className="font-mono text-console-text">{explanation.selected_action.replace("_", " ")}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Expected Risk Reduction</dt>
          <dd className="font-mono text-console-text">{explanation.expected_risk_reduction}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Execution Confidence</dt>
          <dd className="font-mono text-console-text">{explanation.execution_score}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-widest text-console-muted">Combined Score</dt>
          <dd className="font-mono text-console-text">{explanation.combined_score ?? "—"}</dd>
        </div>
      </dl>
      {explanation.rejected_candidates.length > 0 && (
        <div className="mt-4 border-t border-console-border pt-4">
          <div className="mb-2 text-[11px] uppercase tracking-widest text-console-muted">
            Considered and rejected
          </div>
          <ul className="flex flex-col gap-1">
            {explanation.rejected_candidates.map((decision) => (
              <li key={decision} className="text-sm text-console-muted">
                <span className="font-mono text-console-text">{decision.replace("_", " ")}</span>
                {" — "}
                {explanation.rejection_reasons[decision]}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
