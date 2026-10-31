# Runbook: chaos drill of BudgetGuard itself (W8.6)

This drill breaks one BudgetGuard dependency at a time while a deploy waits on the gate. It checks
two things: the gate stays correct, and every request still ends in exactly one decision row.

```bash
bash deploy/compose/chaos-drill.sh     # inside WSL (Ubuntu-D), compose stack up, ~6 minutes
```

The drill sends signed `deployment_protection_rule` webhooks shaped the way GitHub sends them.
Without a GitHub App, the callback to GitHub fails after the decision is recorded. That is the
order the gate guarantees, so every row shows `callback=failed`.

## Rows, expected vs measured (compose, 31 Oct 2026)

| Kill | Expected | Measured |
| --- | --- | --- |
| `platform-api` during a deploy | GitHub keeps the job waiting. GitHub does not redeliver by itself; the redelivery reconciler does (`security-and-api.md` T-1). The delivery guid and the deployment id dedupe | Delivery while down: connection refused. Redelivery, a second redelivery and a new guid for the same deployment: all 200. **One decision row** |
| `gate-worker` mid-evaluation (job read by a consumer that never acks) | `XAUTOCLAIM` adopts it after 60 s; one verdict | Adopted after **70 s** (60 s idle plus the loop interval). One row, nothing left pending |
| TimescaleDB | The record is buffered in `bg:records-pending` before the callback. Replayed after recovery with a matching inputs hash | Buffered (record plus callback outcome), replayed once the database was back, **replay matches**, stream empty. The verdict was stricter (`OVER_BUDGET`): see note 1 |
| Valkey | No queue, cache or single-flight: the API evaluates inline. Verdict unchanged | Same verdict (`admit WITHIN_BUDGET`). Webhook answered in **0.13 s** inline vs **0.015 s** queued |
| Prometheus | Integrity STALE: `fail_open` admits as `admit_unverified` and pages | `admit_unverified SLI_UNKNOWN`, replay matches |
| Snapshotter leader | The standby takes the advisory lock; the 6-hour backfill fills any gap | Standby took the lock after **1 s**. 12 of 12 buckets per SLO in the last closed hour |

## Notes

1. **A DB outage makes the gate more conservative.** Without buckets, the remaining budget comes
   from the `increase30d` recording rule, which counts all errors. Self-only attribution (D-3)
   needs `self_bad` from the buckets. So orders, whose spent budget was mostly auth's fault,
   priced against its all-errors budget and was blocked. This is deliberate: losing attribution
   should never loosen the gate.
2. **Fixed by this drill:** a buffered decision's callback outcome used to be lost. The replayed
   row said `callback_state = pending` forever. The worker now queues the outcome behind the
   record in `bg:records-pending`, and the replay applies both in order (`gate_worker.py`
   `_mark_callback`, `replay_pending`). The first run showed `pending`; the second showed
   `failed`, the real outcome.
3. **Not reproducible locally:** GitHub-side redelivery. Row 1 re-sends the same signed delivery,
   which is what a redelivery is. The reconciler's GitHub calls are unit-tested
   (`tests/test_reconcilers.py`). Run row 1 on the VM with the real App before the demo.
