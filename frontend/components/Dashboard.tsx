"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { DashboardState, Mode, StartRunMode } from "@/lib/types";
import { AegisApiError, pollRun, startRun } from "@/lib/api";
import { AppNav } from "./AppNav";
import { ModeToggle } from "./ModeToggle";
import { StatusHeader } from "./StatusHeader";
import { RiskPanel } from "./RiskPanel";
import { CandidateTable } from "./CandidateTable";
import { ExplanationPanel } from "./ExplanationPanel";
import { ExecutionPanel } from "./ExecutionPanel";
import { VerificationPanel } from "./VerificationPanel";
import { AuditTimeline } from "./AuditTimeline";
import { RecoveryVisualization } from "./RecoveryVisualization";
import { ConfigPanel } from "./ConfigPanel";
import { ErrorBanner } from "./ErrorBanner";

// How often the dashboard polls GET /api/runs/{run_id} while a run is
// still in progress. Every value rendered below (status/stage/candidates/
// execution/verification) comes straight from that response — there is no
// client-side progress animation and no assumption that execution
// succeeded ahead of the backend actually reporting it.
const POLL_INTERVAL_MS = 700;

function describeError(err: unknown): string {
  if (err instanceof AegisApiError) return err.message;
  return "Unexpected error contacting the Aegis backend.";
}

export function Dashboard() {
  const [mode, setMode] = useState<Mode>("fixture");
  const [state, setState] = useState<DashboardState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [liveExecutionRunIdInput, setLiveExecutionRunIdInput] = useState("");

  const pollAbortRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    pollAbortRef.current?.abort();
    pollAbortRef.current = null;
  }, []);

  // Every mode — FIXTURE, LIVE_DRY_RUN, and a LIVE_EXECUTION run someone
  // pastes a run_id for — ends up here: poll the SAME endpoint
  // (GET /api/runs/{run_id}) until the backend reports running=false.
  // This is the one place run state reaches the UI.
  const watchRun = useCallback(
    (runId: string) => {
      stopPolling();
      const controller = new AbortController();
      pollAbortRef.current = controller;

      const tick = () => {
        pollRun(runId, controller.signal)
          .then((data) => {
            setState(data);
            setLoading(false);
            if (!data.running) stopPolling();
          })
          .catch((err: unknown) => {
            if (err instanceof DOMException && err.name === "AbortError") return;
            setLoading(false);
            stopPolling();
            setLoadError(describeError(err));
          });
      };

      tick();
      pollTimerRef.current = setInterval(tick, POLL_INTERVAL_MS);
    },
    [stopPolling]
  );

  const startAndWatch = useCallback(
    (startMode: StartRunMode) => {
      stopPolling();
      setLoading(true);
      setLoadError(null);
      setState(null);
      startRun(startMode)
        .then((runId) => watchRun(runId))
        .catch((err: unknown) => {
          setLoading(false);
          setLoadError(describeError(err));
        });
    },
    [stopPolling, watchRun]
  );

  // Switching to FIXTURE or LIVE_DRY_RUN auto-starts a fresh run — "one
  // command / one flow". LIVE_EXECUTION can never be started from here
  // (see lib/api.ts); switching to it just clears the view and waits for
  // a run_id to watch.
  useEffect(() => {
    if (mode === "fixture" || mode === "live_dry_run") {
      startAndWatch(mode);
    } else {
      stopPolling();
      setState(null);
      setLoadError(null);
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  useEffect(() => stopPolling, [stopPolling]);

  const handleWatchLiveExecution = (event: React.FormEvent) => {
    event.preventDefault();
    const runId = liveExecutionRunIdInput.trim();
    if (!runId) return;
    setLoading(true);
    setLoadError(null);
    setState(null);
    watchRun(runId);
  };

  // FIXTURE/LIVE_DRY_RUN "reset": there is no server-side demo state to
  // mutate (build_fixture_components builds fresh MagicMocks every call,
  // and a dry run never touches the wallet), so discarding the current
  // run_id/state client-side and starting a brand new run IS the reset —
  // it never attempts to reverse anything on-chain, and there is
  // deliberately no such control for LIVE_EXECUTION at all.
  const handleReset = () => {
    if (mode === "fixture" || mode === "live_dry_run") startAndWatch(mode);
  };

  return (
    <>
      <AppNav state={state} />
      <div className="mx-auto flex max-w-6xl flex-col gap-6 p-4 sm:p-6 lg:p-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-xl text-sm text-console-muted">
          Aegis does not simply choose the financially best action — it chooses the best action
          that can also be executed safely.
        </p>
        <ModeToggle mode={mode} onChange={setMode} />
      </div>

      {mode === "live_execution" && (
        <form
          onSubmit={handleWatchLiveExecution}
          data-testid="live-execution-run-id-form"
          className="flex flex-wrap items-center gap-2 rounded-lg border border-console-border bg-console-panel p-4"
        >
          <label htmlFor="live-execution-run-id" className="text-xs uppercase tracking-widest text-console-muted">
            Run ID (from <code>aegis live-demo --confirm</code>)
          </label>
          <input
            id="live-execution-run-id"
            data-testid="live-execution-run-id-input"
            value={liveExecutionRunIdInput}
            onChange={(event) => setLiveExecutionRunIdInput(event.target.value)}
            placeholder="e.g. 3f9a2e10-..."
            className="min-w-[280px] flex-1 rounded border border-console-border bg-transparent px-2 py-1 font-mono text-sm text-console-text"
          />
          <button
            type="submit"
            data-testid="live-execution-watch-button"
            className="rounded border border-console-border px-3 py-1 text-xs font-semibold uppercase tracking-widest text-console-text hover:bg-console-border/30"
          >
            Watch Run
          </button>
        </form>
      )}

      {(mode === "fixture" || mode === "live_dry_run") && (
        <button
          type="button"
          onClick={handleReset}
          data-testid="reset-demo-button"
          className="self-start rounded border border-console-border px-3 py-1 text-xs font-semibold uppercase tracking-widest text-console-muted hover:text-console-text"
        >
          {mode === "fixture" ? "Reset / Re-run Fixture Demo" : "Refresh Live Dry Run"}
        </button>
      )}

      {loading && !state && (
        <div
          className="rounded-lg border border-console-border bg-console-panel p-6 text-sm text-console-muted"
          data-testid="loading-state"
        >
          Loading Aegis state…
        </div>
      )}

      {loadError && <ErrorBanner message={loadError} />}

      {!loadError && state && state.error && <ErrorBanner message={state.error} />}

      {state && !state.error && (
        <>
          <StatusHeader state={state} />
          {state.candidates.length === 0 ? (
            <div
              className="rounded-lg border border-console-border bg-console-panel p-6 text-sm text-console-muted"
              data-testid="no-position-data"
            >
              {state.running
                ? `Run in progress — stage: ${state.stage ?? "starting"}…`
                : "No position data available yet."}
            </div>
          ) : (
            <>
              <div id="risk-overview">
                <RiskPanel position={state.position} />
              </div>
              <CandidateTable candidates={state.candidates} />
              <RecoveryVisualization steps={state.recovery_steps} />
              <ExplanationPanel explanation={state.explanation} />
              <ExecutionPanel execution={state.execution} />
              <VerificationPanel verification={state.verification} />
              <div id="audit-timeline">
                <AuditTimeline events={state.audit_timeline} />
              </div>
            </>
          )}
        </>
      )}

      <ConfigPanel state={state} />
      </div>
    </>
  );
}
