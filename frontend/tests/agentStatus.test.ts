import { describe, expect, it } from "vitest";
import { deriveAgentStatus } from "@/lib/agentStatus";

describe("deriveAgentStatus — presentational-only mapping of backend stage", () => {
  it("is MONITORING before any run has started", () => {
    expect(deriveAgentStatus(null, false)).toBe("MONITORING");
  });

  it("is ANALYZING while candidates are being scored/simulated", () => {
    expect(deriveAgentStatus("SIMULATED", true)).toBe("ANALYZING");
    expect(deriveAgentStatus("POLICY_CHECK", true)).toBe("ANALYZING");
    expect(deriveAgentStatus("RECOVERY_STARTED", true)).toBe("ANALYZING");
  });

  it("is INTERVENTING while KeeperHub is executing", () => {
    expect(deriveAgentStatus("EXECUTING", true)).toBe("INTERVENTING");
    expect(deriveAgentStatus("EXECUTED", true)).toBe("INTERVENTING");
  });

  it("is VERIFYING while the position is being re-read", () => {
    expect(deriveAgentStatus("VERIFYING", true)).toBe("VERIFYING");
    expect(deriveAgentStatus("REASSESS_RISK", true)).toBe("VERIFYING");
  });

  it("is RESOLVED once the backend reports the run is no longer running, regardless of outcome", () => {
    expect(deriveAgentStatus("RESOLVED", false)).toBe("RESOLVED");
    expect(deriveAgentStatus("UNCERTAIN", false)).toBe("RESOLVED");
    expect(deriveAgentStatus("NO_SAFE_ACTION", false)).toBe("RESOLVED");
  });

  it("never fabricates a status the backend hasn't implied — unknown stages fall back to ANALYZING, not RESOLVED, while still running", () => {
    expect(deriveAgentStatus("SOME_FUTURE_STAGE_NOT_YET_MAPPED", true)).toBe("ANALYZING");
  });
});
