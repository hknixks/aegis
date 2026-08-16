import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConnectWallet } from "@/components/ConnectWallet";

// Purely presentational now — the actual wallet connection lives in
// lib/wallet.ts's useConnectedWallet, tested separately in wallet.test.ts.
// This only covers what the component renders/triggers given props.
describe("ConnectWallet", () => {
  it("shows a connect button, never signs or sends anything on its own", () => {
    render(
      <ConnectWallet
        address={null}
        connecting={false}
        error={null}
        hasProvider={true}
        connect={vi.fn()}
        disconnect={vi.fn()}
        aegisWallet="0xWallet"
      />
    );
    expect(screen.getByTestId("connect-wallet-button")).toHaveTextContent(/connect wallet/i);
  });

  it("calls connect when clicked", () => {
    const connect = vi.fn();
    render(
      <ConnectWallet
        address={null}
        connecting={false}
        error={null}
        hasProvider={true}
        connect={connect}
        disconnect={vi.fn()}
      />
    );
    fireEvent.click(screen.getByTestId("connect-wallet-button"));
    expect(connect).toHaveBeenCalledTimes(1);
  });

  it("shows the connected address and a disconnect control", () => {
    render(
      <ConnectWallet
        address="0xAbC1230000000000000000000000000000dEf9AB"
        connecting={false}
        error={null}
        hasProvider={true}
        connect={vi.fn()}
        disconnect={vi.fn()}
        aegisWallet="0xAbC1230000000000000000000000000000dEf9AB"
      />
    );
    expect(screen.getByTestId("connect-wallet-connected")).toHaveTextContent("0xAbC1");
    expect(screen.getByTestId("disconnect-wallet-button")).toBeInTheDocument();
    expect(screen.queryByTestId("wallet-mismatch-note")).not.toBeInTheDocument();
  });

  it("flags when the connected wallet does not match the wallet Aegis is showing", () => {
    render(
      <ConnectWallet
        address="0xAbC1230000000000000000000000000000dEf9AB"
        connecting={false}
        error={null}
        hasProvider={true}
        connect={vi.fn()}
        disconnect={vi.fn()}
        aegisWallet="0xSomeOtherWallet0000000000000000000000"
      />
    );
    expect(screen.getByTestId("wallet-mismatch-note")).toBeInTheDocument();
  });

  it("shows a plain message when no wallet extension is installed, instead of failing silently", () => {
    render(
      <ConnectWallet
        address={null}
        connecting={false}
        error={null}
        hasProvider={false}
        connect={vi.fn()}
        disconnect={vi.fn()}
      />
    );
    expect(screen.getByText(/no wallet extension found/i)).toBeInTheDocument();
  });

  it("shows a connection error", () => {
    render(
      <ConnectWallet
        address={null}
        connecting={false}
        error="Could not connect. Please try again."
        hasProvider={true}
        connect={vi.fn()}
        disconnect={vi.fn()}
      />
    );
    expect(screen.getByTestId("connect-wallet-error")).toHaveTextContent(/could not connect/i);
  });
});
