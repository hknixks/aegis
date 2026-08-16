import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppNav } from "@/components/AppNav";
import { makeState } from "./fixtures";

const noWallet = {
  address: null,
  connecting: false,
  error: null,
  hasProvider: true,
  connect: vi.fn(),
  disconnect: vi.fn(),
};

describe("AppNav", () => {
  it("shows the network, the watched wallet, and the backend's own system_status", () => {
    render(
      <AppNav
        state={makeState({
          network: "84532",
          wallet: "0xDEM0000000000000000000000000000000000000",
          system_status: "MONITORING",
          running: false,
        })}
        wallet={noWallet}
      />
    );

    expect(screen.getByTestId("network-indicator")).toHaveTextContent("Base Sepolia");
    expect(screen.getByTestId("wallet-indicator")).toHaveTextContent("Watching");
    expect(screen.getByTestId("wallet-indicator")).toHaveTextContent("0xDEM0");
    expect(screen.getByTestId("agent-status-pill")).toHaveTextContent(/monitoring/i);
  });

  it("shows a neutral, non-fabricated state before any run has started", () => {
    render(<AppNav state={null} wallet={noWallet} />);
    expect(screen.getByTestId("agent-status-pill")).toHaveTextContent(/monitoring/i);
    expect(screen.getByTestId("wallet-indicator")).toHaveTextContent(/no wallet/i);
  });

  it("shows INTERVENING only when the backend itself reports system_status INTERVENING", () => {
    render(<AppNav state={makeState({ system_status: "INTERVENING", running: true })} wallet={noWallet} />);
    expect(screen.getByTestId("agent-status-pill")).toHaveTextContent(/intervening/i);
  });

  it("never shows RESOLVED as a system status — a finished run goes back to MONITORING", () => {
    // This is the nav-pill counterpart of the dashboard's own bug: a run
    // that finished (running: false) must not have its system status
    // conflated with whether an incident was resolved.
    render(
      <AppNav
        state={makeState({ running: false, system_status: "MONITORING", incident_state: "RESOLVED" })}
        wallet={noWallet}
      />
    );
    expect(screen.getByTestId("agent-status-pill")).toHaveTextContent(/monitoring/i);
    expect(screen.getByTestId("agent-status-pill")).not.toHaveTextContent(/resolved/i);
  });

  it("labels the connect-wallet control as monitor only, never implying it controls execution", () => {
    render(<AppNav state={makeState()} wallet={noWallet} />);
    expect(screen.getByTestId("connect-wallet-caption")).toHaveTextContent(/monitor only/i);
    expect(screen.getByTestId("connect-wallet-button")).toBeInTheDocument();
  });

  it("labels the watched wallet as your own when wallet_source is 'connected'", () => {
    render(
      <AppNav
        state={makeState({ wallet: "0xAbC1230000000000000000000000000000dEf9AB", wallet_source: "connected" })}
        wallet={{ ...noWallet, address: "0xAbC1230000000000000000000000000000dEf9AB" }}
      />
    );
    expect(screen.getByTestId("wallet-source-label")).toHaveTextContent(/your wallet/i);
  });

  it("labels the watched wallet as a dev default when no wallet is connected", () => {
    render(<AppNav state={makeState({ wallet_source: "dev_default" })} wallet={noWallet} />);
    expect(screen.getByTestId("wallet-source-label")).toHaveTextContent(/dev default/i);
  });

  it("labels the watched wallet as demo data in fixture mode", () => {
    render(<AppNav state={makeState({ wallet_source: "fixture" })} wallet={noWallet} />);
    expect(screen.getByTestId("wallet-source-label")).toHaveTextContent(/demo wallet/i);
  });

  it("exposes Dashboard/Positions/Activity/Settings navigation", () => {
    render(<AppNav state={null} wallet={noWallet} />);
    const nav = screen.getByRole("navigation", { name: /primary/i });
    expect(nav).toHaveTextContent("Dashboard");
    expect(nav).toHaveTextContent("Positions");
    expect(nav).toHaveTextContent("Activity");
    expect(nav).toHaveTextContent("Settings");
  });
});
