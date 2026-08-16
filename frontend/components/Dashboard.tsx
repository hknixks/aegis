"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DashboardState, Mode, StartRunMode, UserWallet } from "@/lib/types";
import { AegisApiError, pollRun, startRun } from "@/lib/api";
import { useConnectedWallet } from "@/lib/wallet";
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

  // The single source of truth for the connected USER WALLET — passed to
  // AppNav for display, and to startRun so LIVE_DRY_RUN monitors this
  // wallet's own position instead of the server's dev-default one. See
  // lib/wallet.ts's docstring: connecting here never grants execution
  // authority, only picks what gets read.
  const connectedWallet = useConnectedWallet();
  const userWallet: UserWallet | null = useMemo(
    () =>
      connectedWallet.address
        ? { address: connectedWallet.address, chain: connectedWallet.chainId ?? "", connected: true }
        : null,
    [connectedWallet.address, connectedWallet.chainId]
  );

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
      // FIXTURE ignores wallet regardless (see aegis.demo_orchestrator.
      // start_run's FIXTURE branch) — only pass it for LIVE_DRY_RUN, so a
      // connected wallet never gets paired with FIXTURE's canned data.
      startRun(startMode, startMode === "live_dry_run" ? userWallet : null)
        .then((runId) => watchRun(runId))
        .catch((err: unknown) => {
          setLoading(false);
          setLoadError(describeError(err));
        });
    },
    [stopPolling, watchRun, userWallet]
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

  // Connecting/disconnecting/switching the browser wallet while already
  // watching a live position re-reads against the newly connected wallet
  // — FIXTURE's canned data never changes with it (excluded above), so
  // this only ever re-triggers a real LIVE_DRY_RUN read.
  useEffect(() => {
    if (mode === "live_dry_run") {
      startAndWatch(mode);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectedWallet.address]);

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
      <AppNav state={state} wallet={connectedWallet} />
      <div className="mx-auto flex max-w-6xl flex-col gap-6 p-4 sm:p-6 lg:p-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-xl text-sm text-console-muted">
          Aegis does not just pick the action that looks best. It picks the action that also
          works safely.
        </p>
        <ModeToggle mode={mode} onChange={setMode} />
      </div>

      {mode === "live_dry_run" && !userWallet && (
        <div
          className="rounded-lg border border-console-border bg-console-panel p-4 text-sm text-console-muted"
          data-testid="connect-wallet-prompt"
        >
          Showing the dev-default wallet.{" "}
          <span className="text-console-text">Connect your own wallet</span> (top right) to monitor your own
          position instead.
        </div>
      )}

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
          {state.risk_tier === "NO_POSITION" ? (
            <div
              className="rounded-lg border border-console-border bg-console-panel p-6 text-sm text-console-muted"
              data-testid="no-position-data"
            >
              {state.running
                ? `Working. Current step: ${state.stage ?? "starting"}...`
                : "No position data yet."}
            </div>
          ) : state.incident_state === "NO_ACTIVE_INCIDENT" ? (
            // Aegis's differentiator (candidate actions, scores, why-this-
            // was-chosen) only exists when there was something to decide
            // between. A safe/no-debt position never had that decision to
            // make, so this shows a clean, honest state instead of
            // fabricating candidates or claiming an incident was resolved.
            <>
              <div id="risk-overview">
                <RiskPanel position={state.position} />
              </div>
              <div
                className="rounded-lg border border-risk-safe/40 bg-risk-safe/5 p-6"
                data-testid="safe-no-incident-panel"
              >
                <div className="text-sm font-semibold uppercase tracking-widest text-risk-safe">
                  {state.no_debt ? "Safe. No outstanding debt." : "Safe."}
                </div>
                <p className="mt-2 text-sm text-console-muted">
                  No intervention required. Aegis is monitoring this position.
                </p>
              </div>
              {state.audit_timeline.length > 0 && (
                <div id="audit-timeline">
                  <AuditTimeline events={state.audit_timeline} />
                </div>
              )}
            </>
          ) : (
            // An incident exists (or existed): show the full execution-aware
            // flow — current risk, actions considered, the selection Aegis
            // made and why, the KeeperHub execution, and verification.
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
