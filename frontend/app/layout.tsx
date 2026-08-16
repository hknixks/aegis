import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Aegis — Execution-Aware DeFi Guardian",
  description: "Operations console for the Aegis autonomous DeFi risk agent.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-console-bg text-console-text antialiased">{children}</body>
    </html>
  );
}
