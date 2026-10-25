import { notFound } from "next/navigation";
import { Suspense } from "react";
import { ApiError, api } from "@/lib/api";
import { pct } from "@/lib/format";
import { OverrideForm } from "./OverrideForm";
import { ReplayButton } from "./ReplayButton";

export default function DecisionPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <main>
      <Suspense fallback={<p className="muted">Loading decision…</p>}>
        <DecisionView params={params} />
      </Suspense>
    </main>
  );
}

async function DecisionView({ params }: { params: Promise<{ id: string }> }) {
  const id = Number((await params).id);
  if (!Number.isInteger(id) || id <= 0) notFound();
  const d = await api.decision(id).catch((e) => {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  });
  const canOverride = d.overridable;              // the API decides; this only hides the form
  return (
    <>
      <h1>{d.service} · <code>{d.sha.slice(0, 7)}</code></h1>
      <p className="verdict" data-v={d.verdict}><strong>{d.verdict}</strong> — {d.reason_code}</p>
      <table className="num">
        <tbody>
          <tr><th>Price</th><td>{pct(d.price_pct, 2)}</td></tr>
          <tr><th>Affordable</th><td>{pct(d.affordable_pct, 2)}</td></tr>
          <tr><th>Canary</th><td>{d.canary_profile.length ? d.canary_profile.join(" → ") + "%" : "—"}</td></tr>
          <tr><th>Policy</th><td><code>{d.policy_version.slice(0, 7)}</code></td></tr>
          <tr><th>Inputs hash</th><td><code>{d.inputs_hash.slice(0, 16)}…</code></td></tr>
          <tr><th>Decided</th><td>{d.decided_at}</td></tr>
        </tbody>
      </table>
      {d.brief && (
        <section aria-labelledby="brief">
          <h2 id="brief">Freeze Brief <span className="muted">({d.brief.source}{d.brief.fallback_reason ? `: ${d.brief.fallback_reason}` : ""})</span></h2>
          <p style={{ whiteSpace: "pre-line" }}>{d.brief.text}</p>
        </section>
      )}
      {d.override && <p><strong>Overridden</strong> by {d.override.approver}: “{d.override.reason}”</p>}
      <ReplayButton decisionId={d.id} recordedHash={d.inputs_hash} />
      {canOverride && <OverrideForm decisionId={d.id} />}
      <details><summary>Stored inputs</summary><pre>{JSON.stringify(d.inputs, null, 2)}</pre></details>
    </>
  );
}
