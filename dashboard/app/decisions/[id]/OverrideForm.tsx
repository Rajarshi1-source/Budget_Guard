"use client";
import { useActionState } from "react";
import { overrideAction, type OverrideState } from "./actions";

export function OverrideForm({ decisionId }: { decisionId: number }) {
  const [state, action, pending] = useActionState<OverrideState, FormData>(overrideAction, {});
  if (state.ok) return <p role="status">Override recorded. The deploy job is being re-run and will be admitted on the override lane with a 5% canary.</p>;
  return (
    <form action={action}>
      <h2>Override this block</h2>
      <p className="muted">Recorded with your GitHub login, valid once for 30 minutes, and posted to Slack. The gate re-runs the job; rollbacks and proven fixes never need this.</p>
      <input type="hidden" name="decisionId" value={decisionId} />
      <label htmlFor="reason">Reason (at least 20 characters)</label>
      <textarea id="reason" name="reason" required minLength={20} maxLength={500} rows={3} aria-describedby="override-error" />
      <button type="submit" disabled={pending}>{pending ? "Recording…" : "Override and re-run"}</button>
      {state.error && <p id="override-error" role="alert">{state.error}</p>}
    </form>
  );
}
