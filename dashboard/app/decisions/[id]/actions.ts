"use server";
import { headers } from "next/headers";
import { refresh } from "next/cache";
import { ApiError, api } from "@/lib/api";
import { auth } from "@/lib/auth";
import { OverrideInput, type Replay } from "@/lib/schemas";

export type ReplayState = { result?: Replay; error?: string };
export type OverrideState = { ok?: boolean; error?: string };

/** D-8 on demand: the API recomputes the verdict from the stored inputs and compares hashes. */
export async function replayAction(_: ReplayState, form: FormData): Promise<ReplayState> {
  const id = Number(form.get("decisionId"));
  if (!Number.isInteger(id) || id <= 0) return { error: "Bad decision id" };
  try {
    return { result: await api.replay(id) };
  } catch {
    return { error: "Replay failed; the decision is unchanged" };
  }
}

/** Overrides: the dashboard proves who the human is; the API decides whether they may. */
export async function overrideAction(_: OverrideState, form: FormData): Promise<OverrideState> {
  const parsed = OverrideInput.safeParse({ decisionId: form.get("decisionId"), reason: form.get("reason") });
  if (!parsed.success) return { error: parsed.error.issues[0]?.message ?? "Invalid input" };

  const h = await headers();
  const session = await auth.api.getSession({ headers: h });
  if (!session) return { error: "Sign in with GitHub first" };

  // better-auth 1.7 selects by the local account row id, not by provider name.
  const accounts = await auth.api.listUserAccounts({ headers: h });
  const github = accounts.find((a) => a.providerId === "github");
  if (!github) return { error: "No GitHub account linked to this session" };
  const { accessToken } = await auth.api.getAccessToken({ body: { accountId: github.id }, headers: h });
  if (!accessToken) return { error: "GitHub session expired; sign in again" };
  try {
    await api.override(parsed.data.decisionId, parsed.data.reason, accessToken);
  } catch (e) {
    if (e instanceof ApiError && e.status === 403) return { error: "Your GitHub login is not on the approvers list" };
    if (e instanceof ApiError && e.status === 409) return { error: "Nothing to override: not blocked, already overridden, or too old to re-run" };
    if (e instanceof ApiError && e.status === 401) return { error: "GitHub rejected your session token; sign in again" };
    if (e instanceof ApiError && e.status === 503) return { error: "GitHub would not re-run the job, so the override was withdrawn; try again" };
    return { error: "Override failed; nothing was approved" };
  }
  refresh();                       // Next 16: re-read uncached data on this page
  return { ok: true };
}
