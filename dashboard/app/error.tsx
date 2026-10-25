"use client";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main>
      <h1>BudgetGuard API unavailable</h1>
      <p className="muted">The gate keeps deciding without the dashboard; this page only explains decisions. ({error.digest ?? "no digest"})</p>
      <button type="button" onClick={reset}>Try again</button>
    </main>
  );
}
