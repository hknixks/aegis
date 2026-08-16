import Link from "next/link";
import { ArchitectureDiagram } from "@/components/landing/ArchitectureDiagram";

const HOW_IT_WORKS = ["Detect", "Analyze", "Evaluate", "Simulate", "Execute", "Verify"];

const FEATURES = [
  {
    title: "Execution-aware decisions",
    detail: "Aegis scores every option two ways: how much it helps, and how likely it is to work. A good idea that cannot be done safely never wins.",
  },
  {
    title: "Simulation before execution",
    detail: "Aegis tests every action for real before it runs it. If the test fails, Aegis will not try it, no matter how good it looks.",
  },
  {
    title: "Policy controls",
    detail: "Clear rules decide what is allowed: which chain, which action, which wallet, and how much. No model can change these rules.",
  },
  {
    title: "Recovery and re-planning",
    detail: "If one option is rejected, Aegis does not stop. It checks fresh data and tries the next best option, within safe limits.",
  },
  {
    title: "Onchain verification",
    detail: "Aegis never assumes an action worked. It reads the position again after acting and checks that the risk actually went down.",
  },
  {
    title: "Full audit trail",
    detail: "Every step, every score, and every reason is recorded under one run ID. This is the same data shown on the dashboard.",
  },
];

export default function LandingPage() {
  return (
    <div className="flex flex-col">
      {/* Hero */}
      <section className="border-b border-console-border">
        <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-20 sm:px-6 sm:py-28 lg:px-8">
          <p className="font-mono text-4xl font-bold tracking-tight text-console-text sm:text-6xl">AEGIS</p>
          <h1 className="max-w-2xl text-2xl font-semibold text-console-text sm:text-3xl">
            Execution-Aware DeFi Guardian
          </h1>
          <p className="max-w-2xl text-base text-console-muted sm:text-lg">
            Aegis watches your DeFi position. If it gets risky, Aegis picks the safest fix, not
            just the best one on paper, and carries it out through KeeperHub.
          </p>
          <div className="flex flex-wrap gap-3 pt-2">
            <Link
              href="/app"
              data-testid="launch-cta"
              className="rounded bg-console-text px-5 py-2.5 text-sm font-semibold uppercase tracking-widest text-console-bg hover:opacity-90"
            >
              Launch Aegis
            </Link>
            <a
              href="#how-it-works"
              data-testid="how-it-works-cta"
              className="rounded border border-console-border px-5 py-2.5 text-sm font-semibold uppercase tracking-widest text-console-text hover:bg-console-panel"
            >
              View how it works
            </a>
          </div>
        </div>
      </section>

      {/* Problem / Solution */}
      <section className="border-b border-console-border">
        <div className="mx-auto grid max-w-5xl gap-8 px-4 py-16 sm:grid-cols-2 sm:px-6 lg:px-8">
          <div>
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-console-muted">Problem</h2>
            <p className="text-lg text-console-text">Most agents can decide what to do.</p>
            <p className="text-lg text-console-text">The hard part is doing it safely onchain.</p>
          </div>
          <div>
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-console-muted">Solution</h2>
            <p className="text-lg text-console-text">
              Aegis checks two things before it acts: will this help, and can it be done safely?
              If a good-looking fix cannot be done safely, Aegis will not use it.
            </p>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how-it-works" className="border-b border-console-border scroll-mt-4">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:px-6 lg:px-8">
          <h2 className="mb-6 text-xs font-semibold uppercase tracking-widest text-console-muted">How it works</h2>
          <ol className="flex flex-wrap items-center gap-x-2 gap-y-3 font-mono text-sm text-console-text sm:text-base">
            {HOW_IT_WORKS.map((step, index) => (
              <li key={step} className="flex items-center gap-2">
                <span className="rounded border border-console-border bg-console-panel px-3 py-1.5">{step}</span>
                {index < HOW_IT_WORKS.length - 1 && <span className="text-console-muted">→</span>}
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Why KeeperHub */}
      <section className="border-b border-console-border">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:px-6 lg:px-8">
          <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest text-console-muted">Why KeeperHub</h2>
          <div className="flex flex-col gap-2 text-lg text-console-text">
            <p>Hermes can read a position and suggest a decision.</p>
            <p>Aegis&apos;s own rules decide what is actually allowed.</p>
            <p>KeeperHub does the onchain work: it runs the test, sends the transaction, and reports the result, every time.</p>
          </div>
          <p className="mt-4 max-w-2xl text-sm text-console-muted">
            Aegis never holds a private key. It never signs or sends a transaction on its own.
            Every action goes through KeeperHub.
          </p>
        </div>
      </section>

      {/* Features */}
      <section className="border-b border-console-border">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:px-6 lg:px-8">
          <h2 className="mb-6 text-xs font-semibold uppercase tracking-widest text-console-muted">Features</h2>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((feature) => (
              <div key={feature.title} className="rounded-lg border border-console-border bg-console-panel p-5">
                <h3 className="mb-2 text-sm font-semibold text-console-text">{feature.title}</h3>
                <p className="text-xs text-console-muted">{feature.detail}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Architecture */}
      <section>
        <div className="mx-auto max-w-5xl px-4 py-16 sm:px-6 lg:px-8">
          <h2 className="mb-6 text-xs font-semibold uppercase tracking-widest text-console-muted">Architecture</h2>
          <ArchitectureDiagram />
        </div>
      </section>

      <footer className="border-t border-console-border">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-8 text-xs text-console-muted sm:px-6 lg:px-8">
          <span>Aegis only works on Base Sepolia. There is no path to mainnet.</span>
          <Link href="/app" className="text-console-text hover:underline">
            Launch Aegis →
          </Link>
        </div>
      </footer>
    </div>
  );
}
