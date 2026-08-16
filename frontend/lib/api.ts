import type { DashboardState, StartRunMode, UserWallet } from "./types";

// The ONLY server this dashboard ever talks to. It is aegis.api's
// read-only FastAPI backend — never KeeperHub, never a blockchain RPC
// endpoint, never anything that can sign or broadcast. See
// src/aegis/api.py's module docstring for the guarantee this depends on:
// POST /api/runs only ever accepts "fixture" | "live_dry_run" — there is
// no request this file can make that starts a real transaction.
// NEXT_PUBLIC_AEGIS_API_URL is a plain URL, not a secret — no KeeperHub
// credential is ever defined as a Next.js environment variable.
const API_BASE_URL = process.env.NEXT_PUBLIC_AEGIS_API_URL || "http://localhost:8000";

export class AegisApiError extends Error {}

export async function startRun(
  mode: StartRunMode,
  wallet?: UserWallet | null,
  signal?: AbortSignal
): Promise<string> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // wallet is omitted (not just null) when nothing is connected, or
      // for FIXTURE mode, which ignores it either way — see
      // aegis.demo_orchestrator.start_run's FIXTURE branch.
      body: JSON.stringify(wallet && wallet.connected ? { mode, wallet } : { mode }),
      signal,
      cache: "no-store",
    });
  } catch {
    throw new AegisApiError("Could not reach the Aegis backend API.");
  }
  if (!response.ok) {
    throw new AegisApiError(`Aegis backend returned ${response.status}.`);
  }
  const data = (await response.json()) as { run_id: string };
  return data.run_id;
}

export async function pollRun(runId: string, signal?: AbortSignal): Promise<DashboardState> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/runs/${encodeURIComponent(runId)}`, {
      signal,
      cache: "no-store",
    });
  } catch {
    throw new AegisApiError("Could not reach the Aegis backend API.");
  }
  if (response.status === 404) {
    throw new AegisApiError(`No run found with id ${runId}.`);
  }
  if (!response.ok) {
    throw new AegisApiError(`Aegis backend returned ${response.status}.`);
  }
  return (await response.json()) as DashboardState;
}
