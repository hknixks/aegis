const LAYERS = [
  { label: "Decision Engine", detail: "Financial + execution scoring, deterministic" },
  { label: "Policy Engine", detail: "Hard allowlist gate — chain, protocol, wallet, amount" },
  { label: "Simulation", detail: "Mandatory, real, before anything is proposed for execution" },
  { label: "KeeperHub", detail: "The only onchain execution layer — simulate, execute, verify" },
  { label: "Base Sepolia", detail: "Public testnet" },
  { label: "Verification & Audit", detail: "Fresh position re-read, one shared run ID" },
];

export function ArchitectureDiagram() {
  return (
    <div className="rounded-lg border border-console-border bg-console-panel p-6" data-testid="architecture-diagram">
      <ol className="flex flex-col gap-2">
        {LAYERS.map((layer, index) => (
          <li key={layer.label} className="flex items-start gap-3">
            <span className="mt-0.5 font-mono text-xs text-console-muted">{String(index + 1).padStart(2, "0")}</span>
            <div className="flex flex-col">
              <span className="font-mono text-sm font-semibold text-console-text">{layer.label}</span>
              <span className="text-xs text-console-muted">{layer.detail}</span>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
