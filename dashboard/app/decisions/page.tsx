import Link from "next/link";
import { Suspense } from "react";
import { api } from "@/lib/api";
import { pct } from "@/lib/format";

export const metadata = { title: "Decisions" };

export default function DecisionsPage({ searchParams }: { searchParams: Promise<{ cursor?: string; service?: string }> }) {
  return (
    <main>
      <h1>Decisions</h1>
      <Suspense fallback={<p className="muted">Loading…</p>}>
        <DecisionTable searchParams={searchParams} />
      </Suspense>
    </main>
  );
}

async function DecisionTable({ searchParams }: { searchParams: Promise<{ cursor?: string; service?: string }> }) {
  const { cursor, service } = await searchParams;
  const page = await api.decisions(cursor, service);
  return (
    <>
      <table>
        <thead><tr><th>When (UTC)</th><th>Service</th><th>Commit</th><th>Verdict</th><th>Reason</th><th>Price / affordable</th></tr></thead>
        <tbody>
          {page.items.map((d) => (
            <tr key={d.id}>
              <td className="num">{d.decided_at.slice(0, 16).replace("T", " ")}</td>
              <td>{d.service}</td>
              <td><Link href={`/decisions/${d.id}`}><code>{d.sha.slice(0, 7)}</code></Link></td>
              <td><span className="verdict" data-v={d.verdict}>{d.verdict}</span>{d.override && " (overridden)"}</td>
              <td>{d.reason_code}</td>
              <td className="num">{pct(d.price_pct, 2)} / {pct(d.affordable_pct, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {page.next_cursor && <p><Link href={`/decisions?cursor=${encodeURIComponent(page.next_cursor)}${service ? `&service=${encodeURIComponent(service)}` : ""}`}>Older →</Link></p>}
    </>
  );
}
