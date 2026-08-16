"use client";

import { useCallback, useEffect, useState } from "react";

// Your browser wallet extension (MetaMask etc.) injects this object. We
// only ever call read-only methods on it here (list accounts). Nothing
// in this file can sign a message, sign a transaction, or send a
// transaction. Connecting a wallet is for display only - it does not
// change which wallet Aegis watches or acts on. That wallet is set on
// the server (see AEGIS_EXPECTED_WALLET_ADDRESS).
type Eip1193Provider = {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
  on?: (event: string, handler: (...args: unknown[]) => void) => void;
  removeListener?: (event: string, handler: (...args: unknown[]) => void) => void;
};

declare global {
  interface Window {
    ethereum?: Eip1193Provider;
  }
}

export function useConnectedWallet() {
  const [address, setAddress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const hasProvider = typeof window !== "undefined" && !!window.ethereum;

  const connect = useCallback(async () => {
    if (!window.ethereum) {
      setError("No wallet found. Please install a wallet extension like MetaMask.");
      return;
    }
    setConnecting(true);
    setError(null);
    try {
      const accounts = (await window.ethereum.request({ method: "eth_requestAccounts" })) as string[];
      setAddress(accounts[0] ?? null);
    } catch {
      setError("Could not connect. Please try again.");
    } finally {
      setConnecting(false);
    }
  }, []);

  const disconnect = useCallback(() => {
    // This only clears what this page remembers. Most wallet extensions
    // do not yet support revoking a connection from the site side. To
    // fully disconnect, remove this site from your wallet's settings.
    setAddress(null);
  }, []);

  useEffect(() => {
    if (!window.ethereum?.on) return;
    const handleAccountsChanged = (...args: unknown[]) => {
      const accounts = args[0] as string[];
      setAddress(accounts[0] ?? null);
    };
    window.ethereum.on("accountsChanged", handleAccountsChanged);
    return () => window.ethereum?.removeListener?.("accountsChanged", handleAccountsChanged);
  }, []);

  return { address, connecting, error, hasProvider, connect, disconnect };
}
