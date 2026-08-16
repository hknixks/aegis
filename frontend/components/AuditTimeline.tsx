import type { DashboardAuditEvent } from "@/lib/types";

// Renders exactly the events aegis.audit.AuditLogger recorded — this
// component performs no filtering-into-a-narrative, no re-derivation of
// what "should" have happened. If a stage is missing here, it's because
// the backend never recorded it, not because the frontend guessed wrong.
function explanationFor(event: DashboardAuditEvent): string | null {
  const decision = event.detail["recovery_decision"];
  if (typeof decision === "string" && decision.length > 0) return decision;
  const reason = event.detail["failure_reason"];
  if (typeof reason === "string" && reason.length > 0) return reason;
  return null;
}

export function AuditTimeline({ events }: { events: DashboardAuditEvent[] }) {
  return (
    <section
      className="rounded-lg border border-console-border bg-console-panel p-6"
      data-testid="audit-timeline"
    >
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-console-muted">
        Audit Timeline
      </h2>
      {events.length === 0 ? (
        <p className="text-sm text-console-muted">No audit events recorded yet.</p>
      ) : (
        <ol className="flex flex-col">
          {events.map((event, index) => (
            <li key={`${event.run_id}-${index}`} className="relative pb-4 pl-6" data-testid="audit-event">
              {index < events.length - 1 && (
                <span className="absolute left-[5px] top-3 h-full w-px bg-console-border" aria-hidden="true" />
              )}
              <span className="absolute left-0 top-1.5 h-2.5 w-2.5 rounded-full border border-console-text bg-console-panel" aria-hidden="true" />
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="font-mono text-sm font-semibold text-console-text">{event.stage}</span>
                <span className="text-[11px] text-console-muted">
                  {new Date(event.timestamp).toLocaleTimeString()}
                </span>
              </div>
              {explanationFor(event) && (
                <p className="mt-1 text-xs text-console-muted">{explanationFor(event)}</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
