const LAYERS = [
  { label: "Decision Engine", detail: "Scores every option on cost and safety. Always the same rules." },
  { label: "Policy Engine", detail: "A checklist of what is allowed: chain, action, wallet, and amount." },
  { label: "Simulation", detail: "Every action is tested for real before it runs, no exceptions." },
  { label: "KeeperHub", detail: "The only place a transaction is tested, sent, and checked." },
  { label: "Base Sepolia", detail: "A public test network. No real money is involved." },
  { label: "Verification and Audit", detail: "Reads the position again after acting. One run ID, full history." },
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
