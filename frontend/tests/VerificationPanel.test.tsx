import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { VerificationPanel } from "@/components/VerificationPanel";
import { makeState } from "./fixtures";

describe("VerificationPanel — verification result", () => {
  it("shows the before/after health factors and confirms the incident was resolved", () => {
    const { verification } = makeState({
      verification: {
        before_health_factor: "1.09",
        before_risk: "AT_RISK",
        after_health_factor: "1.74",
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
        before_risk: "AT_RISK",
        after_health_factor: "1.15",
        after_risk: "AT_RISK",
        risk_reduced: true,
        incident_resolved: false,
      },
    });
    render(<VerificationPanel verification={verification} />);
    expect(screen.getByTestId("incident-not-resolved")).toHaveTextContent(/Re-Planning/);
  });
});
