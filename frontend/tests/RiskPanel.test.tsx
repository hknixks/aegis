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
        risk_level: null,
        risk_threshold: null,
        timestamp: null,
        last_update: null,
      },
    });
    render(<RiskPanel position={position} />);
    expect(screen.getByTestId("hf-gauge-empty")).toBeInTheDocument();
  });
});
