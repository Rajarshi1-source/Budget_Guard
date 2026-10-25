"use client";
import { useActionState } from "react";
import { replayAction, type ReplayState } from "./actions";

export function ReplayButton({ decisionId, recordedHash }: { decisionId: number; recordedHash: string }) {
  const [state, action, pending] = useActionState<ReplayState, FormData>(replayAction, {});
  return (
    <form action={action}>
      <input type="hidden" name="decisionId" value={decisionId} />
      <button type="submit" disabled={pending}>{pending ? "Recomputing…" : "Recompute from stored inputs"}</button>
      {state.result && (
        <p role="status">
          {state.result.matches && state.result.inputs_hash === recordedHash
            ? `Same verdict (${state.result.verdict}, ${state.result.reason_code}) from the same inputs.`
            : `MISMATCH: recomputed ${state.result.verdict}. The gate is not deterministic for this record — investigate.`}
        </p>
      )}
      {state.error && <p role="alert">{state.error}</p>}
    </form>
  );
}
