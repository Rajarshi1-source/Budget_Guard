import { Suspense } from "react";
import { api } from "@/lib/api";

export const metadata = { title: "Policy" };

export default function PolicyPage() {
  return (
    <main>
      <h1>Freeze policy versions</h1>
      <p className="muted">Each version merged only after its backtest passed in CI. The report is the one posted on the pull request.</p>
      <Suspense fallback={<p className="muted">Loading…</p>}><Versions /></Suspense>
    </main>
  );
}

async function Versions() {
  const versions = await api.policies();
  return versions.map((v) => (
    <section key={v.version}>
      <h2><code>{v.version.slice(0, 7)}</code> {v.active && <span className="badge">active</span>}</h2>
      <p className="muted">Created {v.created_at.slice(0, 10)}</p>
      {v.backtest_markdown ? <pre>{v.backtest_markdown}</pre> : <p className="muted">No backtest stored.</p>}
    </section>
  ));
}
