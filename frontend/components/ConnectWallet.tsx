"use client";

import { useConnectedWallet } from "@/lib/wallet";

// Connecting a wallet here only shows its address. It never signs or
// sends anything, and it does not change which wallet Aegis watches.
// That wallet is set on the server and shown next to this button.
export function ConnectWallet({ aegisWallet }: { aegisWallet?: string | null }) {
  const { address, connecting, error, hasProvider, connect, disconnect } = useConnectedWallet();

  if (address) {
    const matches = aegisWallet ? address.toLowerCase() === aegisWallet.toLowerCase() : null;
    return (
      <div className="flex items-center gap-2" data-testid="connect-wallet-connected">
        <span className="rounded border border-console-border px-2 py-1 font-mono text-xs text-console-text" title={address}>
          {address.slice(0, 6)}...{address.slice(-4)}
        </span>
        {matches === false && (
          <span className="text-xs text-risk-atrisk" data-testid="wallet-mismatch-note">
            Aegis is watching a different wallet
          </span>
        )}
        <button
          type="button"
          onClick={disconnect}
          data-testid="disconnect-wallet-button"
          className="text-xs text-console-muted hover:text-console-text"
        >
          Disconnect
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={connect}
        disabled={connecting}
        data-testid="connect-wallet-button"
        className="rounded border border-console-border px-2 py-1 font-mono text-xs uppercase tracking-widest text-console-text hover:bg-console-panel disabled:opacity-50"
      >
        {connecting ? "Connecting..." : "Connect Wallet"}
      </button>
      {error && (
        <span className="text-xs text-risk-high" data-testid="connect-wallet-error">
          {error}
        </span>
      )}
      {!hasProvider && !error && <span className="text-xs text-console-muted">No wallet extension found</span>}
    </div>
  );
}
