import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Aegis: DeFi Guardian",
  description: "A simple dashboard for the Aegis DeFi safety agent.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-console-bg text-console-text antialiased">{children}</body>
    </html>
  );
}
