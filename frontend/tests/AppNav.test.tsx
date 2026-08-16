import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppNav } from "@/components/AppNav";
import { makeState } from "./fixtures";

describe("AppNav", () => {
  it("shows the network, wallet, and a status derived from the backend's own stage — never invented", () => {
    render(<AppNav state={makeState({ network: "84532", wallet: "0xDEM0000000000000000000000000000000000000", stage: "RESOLVED", running: false })} />);

    expect(screen.getByTestId("network-indicator")).toHaveTextContent("Base Sepolia");
    expect(screen.getByTestId("wallet-indicator")).toHaveTextContent("0xDEM0");
    expect(screen.getByTestId("agent-status-pill")).toHaveTextContent(/resolved/i);
  });

  it("shows a neutral, non-fabricated state before any run has started", () => {
    render(<AppNav state={null} />);
    expect(screen.getByTestId("agent-status-pill")).toHaveTextContent(/monitoring/i);
    expect(screen.getByTestId("wallet-indicator")).toHaveTextContent(/no wallet/i);
  });

  it("shows INTERVENTING only while the backend itself reports an EXECUTING/EXECUTED stage", () => {
    render(<AppNav state={makeState({ stage: "EXECUTING", running: true })} />);
    expect(screen.getByTestId("agent-status-pill")).toHaveTextContent(/intervening/i);
  });

  it("exposes Dashboard/Positions/Activity/Settings navigation", () => {
    render(<AppNav state={null} />);
    const nav = screen.getByRole("navigation", { name: /primary/i });
    expect(nav).toHaveTextContent("Dashboard");
    expect(nav).toHaveTextContent("Positions");
    expect(nav).toHaveTextContent("Activity");
    expect(nav).toHaveTextContent("Settings");
  });
});
