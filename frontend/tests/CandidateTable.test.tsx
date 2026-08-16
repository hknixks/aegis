import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CandidateTable } from "@/components/CandidateTable";
import type { DashboardCandidate } from "@/lib/types";

function candidate(overrides: Partial<DashboardCandidate>): DashboardCandidate {
  return {
    action: "ADD_COLLATERAL",
    asset: "USDC",
    amount: "500.0",
    financial_score: "0.42",
    execution_score: "95",
    combined_score: "0.399",
    eligible: true,
    final_status: null,
    simulation_status: "PASSED",
    rejection_reason: null,
    ...overrides,
  };
}

describe("CandidateTable — candidate action states", () => {
  it("renders every candidate action offered", () => {
    render(
      <CandidateTable
        candidates={[candidate({ action: "ADD_COLLATERAL" }), candidate({ action: "REPAY_DEBT" })]}
      />
    );
    const rows = screen.getAllByTestId("candidate-row");
    expect(rows).toHaveLength(2);
  });

  it("marks a rejected candidate with its rejection reason", () => {
    render(
      <CandidateTable
        candidates={[
          candidate({
            action: "REPAY_DEBT",
            eligible: false,
            final_status: "REJECTED",
            simulation_status: "FAILED",
            rejection_reason: "Simulation would revert: insufficient allowance",
          }),
        ]}
      />
    );
    const row = screen.getByTestId("candidate-row");
    expect(row).toHaveAttribute("data-status", "rejected");
    expect(screen.getByText(/insufficient allowance/)).toBeInTheDocument();
  });

  it("marks the selected candidate distinctly from merely-eligible ones", () => {
    render(
      <CandidateTable
        candidates={[
          candidate({ action: "ADD_COLLATERAL", final_status: "SELECTED", eligible: true }),
          candidate({ action: "REPAY_DEBT", final_status: null, eligible: true }),
        ]}
      />
    );
    const rows = screen.getAllByTestId("candidate-row");
    expect(rows[0]).toHaveAttribute("data-status", "selected");
    expect(rows[1]).toHaveAttribute("data-status", "eligible");
    expect(screen.getByText("Selected")).toBeInTheDocument();
  });
});
