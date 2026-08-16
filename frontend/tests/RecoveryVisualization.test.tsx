import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RecoveryVisualization } from "@/components/RecoveryVisualization";

describe("RecoveryVisualization — recovery state", () => {
  it("renders nothing when there was no recovery (a single, first-try candidate)", () => {
    const { container } = render(
      <RecoveryVisualization
        steps={[{ action: "ADD_COLLATERAL", amount: "500.0", simulation_status: "PASSED", outcome: "selected", reason: null }]}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the rejected attempt, the re-planning transition, and the eventual selection", () => {
    render(
      <RecoveryVisualization
        steps={[
          {
            action: "REPAY_DEBT",
            amount: "300.0",
            simulation_status: "FAILED",
            outcome: "rejected",
            reason: "Simulation would revert: insufficient allowance",
          },
          {
            action: "ADD_COLLATERAL",
            amount: "500.0",
            simulation_status: "PASSED",
            outcome: "selected",
            reason: null,
          },
        ]}
      />
    );
    const steps = screen.getAllByTestId("recovery-step");
    expect(steps).toHaveLength(2);
    expect(steps[0]).toHaveAttribute("data-outcome", "rejected");
    expect(steps[1]).toHaveAttribute("data-outcome", "selected");
    expect(screen.getByText(/insufficient allowance/)).toBeInTheDocument();
    expect(screen.getByText(/checked other options/)).toBeInTheDocument();
  });
});
