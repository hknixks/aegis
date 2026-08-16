"use client";

// Purely presentational — the actual wallet connection state lives in
// Dashboard.tsx (via lib/wallet.ts's useConnectedWallet), which is also
// what drives POST /api/runs's `wallet` field. This component only
// renders that shared state and triggers connect/disconnect; it never
// signs or sends anything, and never grants execution authority (see
// lib/wallet.ts's docstring).
export function ConnectWallet({
  address,
  connecting,
  error,
  hasProvider,
  connect,
  disconnect,
  aegisWallet,
}: {
  address: string | null;
  connecting: boolean;
  error: string | null;
  hasProvider: boolean;
  connect: () => void;
  disconnect: () => void;
  aegisWallet?: string | null;
}) {
  if (address) {
    const matches = aegisWallet ? address.toLowerCase() === aegisWallet.toLowerCase() : null;
    return (
      <div className="flex items-center gap-2" data-testid="connect-wallet-connected">
        <span className="rounded border border-console-border px-2 py-1 font-mono text-xs text-console-text" title={address}>
          {address.slice(0, 6)}...{address.slice(-4)}
        </span>
        {matches === false && (
          <span className="text-xs text-risk-atrisk" data-testid="wallet-mismatch-note">
            Aegis is showing a different wallet's position
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
