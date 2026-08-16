import Link from "next/link";
import type { DashboardState } from "@/lib/types";
import { deriveAgentStatus } from "@/lib/agentStatus";
import { ConnectWallet } from "./ConnectWallet";

const STATUS_META: Record<string, { label: string; className: string }> = {
  MONITORING: { label: "Monitoring", className: "text-console-muted border-console-border" },
  ANALYZING: { label: "Analyzing", className: "text-risk-atrisk border-risk-atrisk/40" },
  INTERVENTING: { label: "Intervening", className: "text-risk-high border-risk-high/40" },
  VERIFYING: { label: "Verifying", className: "text-risk-atrisk border-risk-atrisk/40" },
  RESOLVED: { label: "Resolved", className: "text-risk-safe border-risk-safe/40" },
};

const NAV_LINKS = [
  { href: "#top", label: "Dashboard" },
  { href: "#risk-overview", label: "Positions" },
  { href: "#audit-timeline", label: "Activity" },
  { href: "#settings", label: "Settings" },
];

export function AppNav({ state }: { state: DashboardState | null }) {
  const agentStatus = deriveAgentStatus(state?.stage ?? null, state?.running ?? false);
  const meta = STATUS_META[agentStatus];
  const network = state?.network === "84532" ? "Base Sepolia" : state?.network || "Base Sepolia";

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
          <span
            className="rounded border border-console-border px-2 py-1 font-mono text-console-muted"
            data-testid="wallet-indicator"
            title="The wallet Aegis is watching, set on the server"
          >
            {state?.wallet ? `Watching: ${state.wallet.slice(0, 6)}…${state.wallet.slice(-4)}` : "No wallet"}
          </span>
          <span
            className={`rounded border px-2 py-1 font-mono font-semibold uppercase tracking-widest ${meta.className}`}
            data-testid="agent-status-pill"
          >
            {meta.label}
          </span>
          <ConnectWallet aegisWallet={state?.wallet} />
        </div>
      </div>
    </header>
  );
}
