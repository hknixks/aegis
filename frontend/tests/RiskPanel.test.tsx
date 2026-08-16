import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RiskPanel } from "@/components/RiskPanel";
import { RiskBadge } from "@/components/RiskBadge";
import { makeState } from "./fixtures";

describe("RiskPanel — position risk states", () => {
  it("renders a SAFE position with the safe badge and no alarm styling", () => {
    const { position } = makeState({ risk_tier: "SAFE" });
    render(<RiskPanel position={position} />);

    const badge = screen.getByTestId("risk-badge");
    expect(badge).toHaveAttribute("data-tier", "SAFE");
    expect(badge).toHaveTextContent("SAFE");
    expect(screen.getByText("1000.0")).toBeInTheDocument();
  });

  it("renders a HIGH risk position distinctly from SAFE (not color alone)", () => {
    render(<RiskBadge tier="HIGH" />);
    const badge = screen.getByTestId("risk-badge");
    expect(badge).toHaveAttribute("data-tier", "HIGH");
    expect(badge).toHaveTextContent("HIGH");
    // distinct symbol, not just a color swap, from SAFE's "●"
    expect(badge.textContent).toContain("▲▲");
  });

  it("shows the health factor gauge with an empty state when the value is unknown", () => {
    const { position } = makeState({
      position: {
        collateral: null,
        debt: null,
        health_factor: null,
        no_debt: false,
        risk_level: null,
        risk_threshold: null,
        timestamp: null,
        last_update: null,
      },
    });
    render(<RiskPanel position={position} />);
    expect(screen.getByTestId("hf-gauge-empty")).toBeInTheDocument();
  });

  it("never shows the raw uint256-max sentinel — shows 'No debt' instead", () => {
    const { position } = makeState({
      position: {
        collateral: "0",
        debt: "0",
        health_factor: null,
        no_debt: true,
        risk_level: "SAFE",
        risk_threshold: "1.2",
        timestamp: "2026-08-15T12:00:00Z",
        last_update: "2026-08-15T12:00:00Z",
      },
    });
    render(<RiskPanel position={position} />);

    expect(screen.getByTestId("health-factor-value")).toHaveTextContent("No debt");
    expect(screen.getByTestId("hf-gauge-no-debt")).toBeInTheDocument();
    // no giant number anywhere in the rendered output
    expect(document.body.textContent).not.toMatch(/\d{10,}/);
  });

  it("shows known-zero collateral and debt as $0, distinct from unknown", () => {
    const { position } = makeState({
      position: {
        collateral: "0",
        debt: "0",
        health_factor: null,
        no_debt: true,
        risk_level: "SAFE",
        risk_threshold: "1.2",
        timestamp: "2026-08-15T12:00:00Z",
        last_update: "2026-08-15T12:00:00Z",
      },
    });
    render(<RiskPanel position={position} />);

    const zeros = screen.getAllByText("$0");
    expect(zeros).toHaveLength(2);
    expect(screen.queryByText(/unknown/i)).not.toBeInTheDocument();
  });

  it("shows unknown collateral and debt distinctly from known-zero", () => {
    const { position } = makeState({
      position: {
        collateral: null,
        debt: null,
        health_factor: null,
        no_debt: false,
        risk_level: null,
        risk_threshold: null,
        timestamp: null,
        last_update: null,
      },
    });
    render(<RiskPanel position={position} />);

    const unknowns = screen.getAllByText("Unknown");
    expect(unknowns).toHaveLength(2);
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
  });
});
