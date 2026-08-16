"use client";

import { useCallback, useEffect, useState } from "react";

// Your browser wallet extension (MetaMask etc.) injects this object. We
// only ever call read-only methods on it here (list accounts, read the
// connected chain). Nothing in this file can sign a message, sign a
// transaction, or send a transaction.
//
// USER WALLET vs EXECUTION AUTHORITY: connecting a wallet here tells
// Aegis which position to READ and monitor (see Dashboard.tsx, which
// sends this address as the `wallet` field of POST /api/runs). It never
// grants Aegis permission to act on this wallet's behalf — that is
// decided entirely server-side by the KeeperHub-authorized execution
// wallet (Settings.aegis_expected_wallet_address) and PolicyEngine's own
// wallet-pin check. Connecting a wallet that isn't the authorized one
// still lets you see what Aegis would do; it can never make Aegis
// execute for you.
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
  const [chainId, setChainId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const hasProvider = typeof window !== "undefined" && !!window.ethereum;

  const readChainId = useCallback(async () => {
    if (!window.ethereum) return;
    try {
      const id = (await window.ethereum.request({ method: "eth_chainId" })) as string;
      // Decimal chain id, matching how aegis.api reports network/chain_id
      // (e.g. "84532" for Base Sepolia) — eth_chainId itself returns hex.
      setChainId(String(parseInt(id, 16)));
    } catch {
      setChainId(null);
    }
  }, []);

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
      await readChainId();
    } catch {
      setError("Could not connect. Please try again.");
    } finally {
      setConnecting(false);
    }
  }, [readChainId]);

  const disconnect = useCallback(() => {
    // This only clears what this page remembers. Most wallet extensions
    // do not yet support revoking a connection from the site side. To
    // fully disconnect, remove this site from your wallet's settings.
    setAddress(null);
    setChainId(null);
  }, []);

  useEffect(() => {
    if (!window.ethereum?.on) return;
    const handleAccountsChanged = (...args: unknown[]) => {
      const accounts = args[0] as string[];
      setAddress(accounts[0] ?? null);
    };
    const handleChainChanged = (...args: unknown[]) => {
      const id = args[0] as string;
      setChainId(String(parseInt(id, 16)));
    };
    window.ethereum.on("accountsChanged", handleAccountsChanged);
    window.ethereum.on("chainChanged", handleChainChanged);
    return () => {
      window.ethereum?.removeListener?.("accountsChanged", handleAccountsChanged);
      window.ethereum?.removeListener?.("chainChanged", handleChainChanged);
    };
  }, []);

  return { address, chainId, connecting, error, hasProvider, connect, disconnect };
}
