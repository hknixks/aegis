import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { VerificationPanel } from "@/components/VerificationPanel";
import { makeState } from "./fixtures";

describe("VerificationPanel — verification result", () => {
  it("shows the before/after health factors and confirms the incident was resolved", () => {
    const { verification } = makeState({
      verification: {
        before_health_factor: "1.09",
        before_no_debt: false,
        before_risk: "AT_RISK",
        after_health_factor: "1.74",
        after_no_debt: false,
        after_risk: "SAFE",
        risk_reduced: true,
        incident_resolved: true,
      },
    });
    render(<VerificationPanel verification={verification} />);
    expect(screen.getByText("1.09")).toBeInTheDocument();
    expect(screen.getByText("1.74")).toBeInTheDocument();
    expect(screen.getByTestId("risk-reduced")).toHaveTextContent("Yes");
    expect(screen.getByTestId("incident-resolved")).toBeInTheDocument();
  });

  it("shows re-planning state when the incident is not yet resolved", () => {
    const { verification } = makeState({
      verification: {
        before_health_factor: "1.09",
        before_no_debt: false,
        before_risk: "AT_RISK",
        after_health_factor: "1.15",
        after_no_debt: false,
        after_risk: "AT_RISK",
        risk_reduced: true,
        incident_resolved: false,
      },
    });
    render(<VerificationPanel verification={verification} />);
    expect(screen.getByTestId("incident-not-resolved")).toHaveTextContent(/Trying Again/);
  });

  it("shows 'No debt' instead of the raw sentinel when the position had no debt", () => {
    const { verification } = makeState({
      verification: {
        before_health_factor: null,
        before_no_debt: true,
        before_risk: "SAFE",
        after_health_factor: null,
        after_no_debt: true,
        after_risk: "SAFE",
        risk_reduced: null,
        incident_resolved: false,
      },
    });
    render(<VerificationPanel verification={verification} />);
    const noDebtValues = screen.getAllByText("No debt");
    expect(noDebtValues).toHaveLength(2);
  });
});
