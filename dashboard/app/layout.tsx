import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { SessionBadge } from "@/components/SessionBadge";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "BudgetGuard", template: "%s · BudgetGuard" },
  description: "Error-budget admission control: freeze explanations, decision records, overrides and policy backtests.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="top">
          <Link href="/" className="brand">BudgetGuard</Link>
          <nav aria-label="Main">
            <Link href="/">Services</Link>
            <Link href="/decisions">Decisions</Link>
            <Link href="/policy">Policy</Link>
          </nav>
          <Suspense fallback={null}><SessionBadge /></Suspense>
        </header>
        {children}
      </body>
    </html>
  );
}
