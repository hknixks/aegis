import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Dashboard } from "@/components/Dashboard";
import { makeState } from "./fixtures";

function jsonResponse(body: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

// Simulates the real backend contract: POST /api/runs starts a run and
// returns a run_id; GET /api/runs/{run_id} reports its state. Returning
// running: false means the dashboard's poll loop stops after exactly one
// GET, keeping these tests synchronous and deterministic.
function makeRunsBackend(stateOverrides: Partial<ReturnType<typeof makeState>> = {}) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return jsonResponse({ run_id: "run-1", mode: "fixture" });
    }
    return jsonResponse(makeState({ running: false, ...stateOverrides }));
  });
}

describe("Dashboard — mode selection talks to the right backend endpoint", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = makeRunsBackend();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fixture mode (default) starts a run via POST /api/runs and clearly labels itself as demo data", async () => {
    render(<Dashboard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const [postUrl, postInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(postUrl).toContain("/api/runs");
    expect(postInit.method).toBe("POST");
    expect(JSON.parse(postInit.body as string)).toEqual({ mode: "fixture" });
    expect(screen.getByTestId("demo-data-banner")).toHaveTextContent(/DEMO DATA/i);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [getUrl] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(getUrl).toContain("/api/runs/run-1");
  });

  it("live dry run mode starts a run with mode=live_dry_run and labels itself accordingly", async () => {
    render(<Dashboard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByTestId("mode-toggle-live-dry-run"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    const [, postInit] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(JSON.parse(postInit.body as string)).toEqual({ mode: "live_dry_run" });
    expect(screen.getByTestId("live-dry-run-banner")).toHaveTextContent(/LIVE DRY RUN/i);
  });

  it("live execution mode never starts a run automatically — it waits for a run_id to watch", async () => {
    render(<Dashboard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const callsBeforeSwitch = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByTestId("mode-toggle-live-execution"));

    expect(screen.getByTestId("live-execution-banner")).toHaveTextContent(/LIVE EXECUTION/i);
    expect(screen.getByTestId("live-execution-run-id-form")).toBeInTheDocument();
    // no new request until the operator submits a run_id
    expect(fetchMock.mock.calls.length).toBe(callsBeforeSwitch);

    fireEvent.change(screen.getByTestId("live-execution-run-id-input"), { target: { value: "cli-run-42" } });
    fireEvent.click(screen.getByTestId("live-execution-watch-button"));

    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(callsBeforeSwitch + 1));
    const [getUrl] = fetchMock.mock.calls[callsBeforeSwitch] as [string, RequestInit];
    expect(getUrl).toContain("/api/runs/cli-run-42");
  });

  it("displays the run_id and stage from the backend response", async () => {
    fetchMock = makeRunsBackend({ run_id: "run-1", stage: "RESOLVED" });
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard />);

    await screen.findByTestId("run-id-value");
    expect(screen.getByTestId("run-id-value")).toHaveTextContent("run-1");
    expect(screen.getByTestId("stage-value")).toHaveTextContent("RESOLVED");
  });
});

describe("Dashboard — reset", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("re-runs the same mode via a fresh POST /api/runs, not by mutating the current run", async () => {
    const fetchMock = makeRunsBackend();
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByTestId("reset-demo-button"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    const [, postInit] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(postInit.method).toBe("POST");
  });
});

describe("Dashboard — KeeperHub / backend unavailable", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a structured error banner, not a raw stack trace, when the backend reports it can't reach KeeperHub", async () => {
    vi.stubGlobal(
      "fetch",
      makeRunsBackend({
        status: "Error",
        error: "KeeperHubClientError: request to KeeperHub did not complete",
      })
    );
    render(<Dashboard />);

    const banner = await screen.findByTestId("error-banner");
    expect(banner).toHaveTextContent("request to KeeperHub did not complete");
    expect(banner.textContent).not.toMatch(/Bearer|api[_-]?key/i);
  });

  it("shows an error banner when the backend itself is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.reject(new TypeError("network error")))
    );
    render(<Dashboard />);

    const banner = await screen.findByTestId("error-banner");
    expect(banner).toHaveTextContent(/could not reach/i);
  });
});

describe("Dashboard — no secret ever reaches the client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("never attaches an Authorization header or api key to its backend requests", async () => {
    const fetchMock = makeRunsBackend();
    vi.stubGlobal("fetch", fetchMock);

    render(<Dashboard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    for (const call of fetchMock.mock.calls) {
      const [, init] = call as [string, RequestInit | undefined];
      const headers = (init?.headers ?? {}) as Record<string, string>;
      expect(Object.keys(headers).some((h) => /authorization|api[_-]?key/i.test(h))).toBe(false);
    }
  });

  it("never renders a KeeperHub secret even if it somehow appeared in an audit detail", async () => {
    const secret = "kh_supersecretvalue123";
    vi.stubGlobal(
      "fetch",
      makeRunsBackend({
        candidates: [
          {
            action: "ADD_COLLATERAL",
            asset: "USDC",
            amount: "500.0",
            financial_score: "0.42",
            execution_score: "95",
            combined_score: "0.399",
            eligible: true,
            final_status: "SELECTED",
            simulation_status: "PASSED",
            rejection_reason: null,
          },
        ],
        audit_timeline: [
          {
            run_id: "run-1",
            timestamp: "2026-08-15T12:00:00Z",
            stage: "EXECUTED",
            // The backend is contracted to never put a secret here; this
            // test proves the frontend also does not go out of its way
            // to surface arbitrary detail values that shouldn't be shown.
            detail: { recovery_decision: "selected ADD_COLLATERAL" },
          },
        ],
      })
    );

    render(<Dashboard />);
    await screen.findByTestId("audit-timeline");
    expect(document.body.textContent).not.toContain(secret);
  });
});
