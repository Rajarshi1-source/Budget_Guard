import Link from "next/link";
import type { ServiceSummary } from "@/lib/schemas";
import { burn, pct, selfShareText, STATE_LABEL, unknownReason } from "@/lib/format";
import { RelativeTime } from "./RelativeTime";
import { Sparkline } from "./Sparkline";

const ICON = { healthy: "●", at_risk: "▲", unknown: "?" } as const;   // shape + text, never colour alone

export function ServiceCard({ s }: { s: ServiceSummary }) {
  const why = unknownReason(s);
  const share = selfShareText(s.remaining_pct, s.remaining_self_pct);
  return (
    <article className="card" aria-labelledby={`svc-${s.service}`}>
      <h2 id={`svc-${s.service}`}>{s.service}</h2>
      <p className="badge" data-state={s.state}><span aria-hidden>{ICON[s.state]}</span>{STATE_LABEL[s.state]}</p>
      {why && <p className="muted">{why}. Deploys follow this service&apos;s declared failure mode.</p>}
      <dl className="num">
        <dt>Budget remaining (30 d, SLO {s.objective_pct}%)</dt><dd>{pct(s.remaining_pct)}</dd>
        <dt>Burn rate (1 h)</dt><dd>{burn(s.burn_rate_1h)}</dd>
      </dl>
      {share && <p className="muted">{share}</p>}
      <Sparkline values={s.daily_remaining} />
      {s.frozen && (
        <p><strong>Frozen.</strong>{" "}
          {s.unfreeze_eta ? <>Unfreezes at <RelativeTime iso={s.unfreeze_eta} /> if traffic holds.</> : "No unfreeze projection."}
        </p>
      )}
      {s.open_incident && <p>Open: {s.open_incident.alertname}</p>}
      {s.last_decision && (
        <p>Last decision: <Link href={`/decisions/${s.last_decision.id}`}>
          <span className="verdict" data-v={s.last_decision.verdict}>{s.last_decision.verdict}</span></Link></p>
      )}
    </article>
  );
}
