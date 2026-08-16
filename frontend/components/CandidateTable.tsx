import type { DashboardCandidate } from "@/lib/types";
import { StatusPill } from "./StatusPill";

function statusFor(candidate: DashboardCandidate): { kind: "eligible" | "rejected" | "selected"; label: string } {
  if (candidate.final_status === "SELECTED") return { kind: "selected", label: "Selected" };
  if (!candidate.eligible) return { kind: "rejected", label: "Rejected" };
  return { kind: "eligible", label: "Eligible" };
}

function ScoreCell({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex flex-col items-center">
      <span className="text-[10px] uppercase tracking-widest text-console-muted">{label}</span>
      <span className="font-mono text-lg text-console-text">{value ?? "N/A"}</span>
    </div>
  );
}

export function CandidateTable({ candidates }: { candidates: DashboardCandidate[] }) {
  return (
    <section
      className="rounded-lg border border-console-border bg-console-panel p-6"
      data-testid="candidate-table"
    >
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-console-muted">
        Candidate Actions
      </h2>
      <div className="flex flex-col gap-3">
        {candidates.map((candidate) => {
          const status = statusFor(candidate);
          return (
            <div
              key={candidate.action}
              className={`rounded-md border p-4 ${
                status.kind === "selected"
                  ? "border-risk-safe/50 bg-risk-safe/5"
                  : status.kind === "rejected"
                    ? "border-console-border/60 opacity-80"
                    : "border-console-border"
              }`}
              data-testid="candidate-row"
              data-action={candidate.action}
              data-status={status.kind}
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="font-semibold text-console-text">
                    {candidate.action.replace("_", " ")}
                  </div>
                  {candidate.amount && (
                    <div className="font-mono text-sm text-console-muted">
                      {candidate.amount} {candidate.asset ?? ""}
                    </div>
                  )}
                </div>
                <div className="flex gap-6">
                  <ScoreCell label="Financial" value={candidate.financial_score} />
                  <ScoreCell label="Execution" value={candidate.execution_score} />
                  <ScoreCell label="Combined" value={candidate.combined_score} />
                </div>
                <StatusPill kind={status.kind} label={status.label} />
              </div>
              {candidate.rejection_reason && (
                <p className="mt-3 border-t border-console-border pt-3 text-sm text-console-muted">
                  <span className="font-semibold text-risk-critical">Reason: </span>
                  {candidate.rejection_reason}
                </p>
              )}
              <div className="mt-2 text-[11px] uppercase tracking-widest text-console-muted">
                Simulation: {candidate.simulation_status}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
