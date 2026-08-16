import Link from "next/link";
import type { DashboardState, SystemStatus } from "@/lib/types";
import { ConnectWallet } from "./ConnectWallet";

// Purely a label/color lookup for the backend's own system_status — never
// a re-derivation of it. See aegis.api._system_status for the source of
// truth this mirrors.
const SYSTEM_STATUS_META: Record<SystemStatus, { label: string; className: string }> = {
  MONITORING: { label: "Monitoring", className: "text-console-muted border-console-border" },
  ANALYZING: { label: "Analyzing", className: "text-risk-atrisk border-risk-atrisk/40" },
  INTERVENING: { label: "Intervening", className: "text-risk-high border-risk-high/40" },
  VERIFYING: { label: "Verifying", className: "text-risk-atrisk border-risk-atrisk/40" },
};

// What the WATCHING badge says the wallet actually is, source-aware —
// mirrors aegis.api.DashboardState.wallet_source. Never presents dev/demo
// data as if it were the visitor's own wallet.
const WALLET_SOURCE_LABEL: Record<DashboardState["wallet_source"], string> = {
  connected: "Your wallet",
  dev_default: "Dev default wallet",
  fixture: "Demo wallet",
};

const NAV_LINKS = [
  { href: "#top", label: "Dashboard" },
  { href: "#risk-overview", label: "Positions" },
  { href: "#audit-timeline", label: "Activity" },
  { href: "#settings", label: "Settings" },
];

export interface ConnectedWalletState {
  address: string | null;
  connecting: boolean;
  error: string | null;
  hasProvider: boolean;
  connect: () => void;
  disconnect: () => void;
}

export function AppNav({ state, wallet }: { state: DashboardState | null; wallet: ConnectedWalletState }) {
  const meta = SYSTEM_STATUS_META[state?.system_status ?? "MONITORING"];
  const network = state?.network === "84532" ? "Base Sepolia" : state?.network || "Base Sepolia";
  const sourceLabel = state ? WALLET_SOURCE_LABEL[state.wallet_source] : null;

  return (
    <header
      id="top"
      className="sticky top-0 z-10 border-b border-console-border bg-console-bg/95 backdrop-blur"
      data-testid="app-nav"
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6 lg:px-8">
        <Link href="/" className="font-mono text-lg font-bold tracking-tight text-console-text">
          AEGIS
        </Link>

        <nav className="flex flex-wrap items-center gap-4 text-sm text-console-muted" aria-label="Primary">
          {NAV_LINKS.map((link) => (
            <a key={link.href} href={link.href} className="hover:text-console-text">
              {link.label}
            </a>
          ))}
        </nav>

        <div className="ml-auto flex flex-wrap items-center gap-3 text-xs">
          <span
            className="rounded border border-console-border px-2 py-1 font-mono uppercase tracking-widest text-console-muted"
            data-testid="network-indicator"
          >
            {network}
          </span>
          {/* The wallet Aegis is currently monitoring — your connected
              wallet if you connected one, otherwise a labeled dev/demo
              fallback. This is a READ target, never an execution grant:
              see ConnectWallet's own docstring for why. */}
          <div
            className="flex items-center gap-1.5 rounded border border-console-border px-2 py-1 font-mono"
            data-testid="wallet-indicator"
            title="The wallet Aegis is reading a position for. Connecting your own wallet does not grant it permission to execute anything."
          >
            <span className="h-1.5 w-1.5 rounded-full bg-risk-safe" aria-hidden="true" />
            <span className="uppercase tracking-widest text-console-muted">Watching</span>
            <span className="text-console-text">
              {state?.wallet ? `${state.wallet.slice(0, 6)}…${state.wallet.slice(-4)}` : "No wallet"}
            </span>
            {sourceLabel && (
              <span className="text-[10px] normal-case text-console-muted" data-testid="wallet-source-label">
                ({sourceLabel})
              </span>
            )}
          </div>
          <span
            className={`rounded border px-2 py-1 font-mono font-semibold uppercase tracking-widest ${meta.className}`}
            data-testid="agent-status-pill"
          >
            {meta.label}
          </span>
          {/* Connecting here changes which wallet gets monitored (see
              Dashboard.tsx) — it never grants Aegis permission to execute
              anything on this wallet's behalf. That stays gated entirely
              server-side, independent of what's connected in the browser. */}
          <div className="flex items-center gap-1.5 border-l border-console-border pl-3">
            <span
              className="hidden text-console-muted sm:inline"
              data-testid="connect-wallet-caption"
              title="Connects your wallet so Aegis can monitor your own position. This never grants permission to execute anything."
            >
              Monitor only
            </span>
            <ConnectWallet
              address={wallet.address}
              connecting={wallet.connecting}
              error={wallet.error}
              hasProvider={wallet.hasProvider}
              connect={wallet.connect}
              disconnect={wallet.disconnect}
              aegisWallet={state?.wallet}
            />
          </div>
        </div>
      </div>
    </header>
  );
}
