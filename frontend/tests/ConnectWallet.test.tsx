import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConnectWallet } from "@/components/ConnectWallet";

// Stub only window.ethereum, never window itself - replacing the whole
// window object breaks React's own use of the real DOM in jsdom.
function stubProvider(accounts: string[]) {
  const request = vi.fn().mockResolvedValue(accounts);
  Object.defineProperty(window, "ethereum", {
    value: { request, on: vi.fn(), removeListener: vi.fn() },
    configurable: true,
  });
  return request;
}

function removeProvider() {
  Object.defineProperty(window, "ethereum", { value: undefined, configurable: true });
}

describe("ConnectWallet", () => {
  afterEach(() => {
    removeProvider();
  });

  it("shows a connect button, never signs or sends anything on its own", () => {
    render(<ConnectWallet aegisWallet="0xWallet" />);
    expect(screen.getByTestId("connect-wallet-button")).toHaveTextContent(/connect wallet/i);
  });

  it("requests accounts (read-only) and shows the connected address", async () => {
    const request = stubProvider(["0xAbC1230000000000000000000000000000dEf9"]);
    render(<ConnectWallet aegisWallet="0xWallet" />);

    fireEvent.click(screen.getByTestId("connect-wallet-button"));

    await waitFor(() => expect(screen.getByTestId("connect-wallet-connected")).toBeInTheDocument());
    // eth_requestAccounts only - never a signing or sending method
    expect(request).toHaveBeenCalledWith({ method: "eth_requestAccounts" });
    expect(request).not.toHaveBeenCalledWith(expect.objectContaining({ method: expect.stringMatching(/sign|send/i) }));
  });

  it("flags when the connected wallet does not match the wallet Aegis is watching", async () => {
    stubProvider(["0xAbC1230000000000000000000000000000dEf9"]);
    render(<ConnectWallet aegisWallet="0xSomeOtherWallet0000000000000000000000" />);

    fireEvent.click(screen.getByTestId("connect-wallet-button"));

    await waitFor(() => expect(screen.getByTestId("wallet-mismatch-note")).toBeInTheDocument());
  });

  it("shows a plain message when no wallet extension is installed, instead of failing silently", () => {
    removeProvider();
    render(<ConnectWallet aegisWallet="0xWallet" />);
    expect(screen.getByText(/no wallet extension found/i)).toBeInTheDocument();
  });
});
