"use client";
import { useSyncExternalStore } from "react";
import { untilText } from "@/lib/format";

// One shared 30 s clock. The server snapshot is null, so the server HTML and the hydrating
// client render agree; React then switches to the client snapshot and adds the relative part.
let now = Date.now();
function subscribe(onChange: () => void) {
  now = Date.now();                       // React re-reads the snapshot right after subscribing
  const id = setInterval(() => { now = Date.now(); onChange(); }, 30_000);
  return () => clearInterval(id);
}

/** Absolute UTC time is rendered on the server; the relative part only on the client, so the
 *  server and client HTML never disagree (no hydration mismatch). */
export function RelativeTime({ iso }: { iso: string }) {
  const clock = useSyncExternalStore(subscribe, () => now, () => null);
  return (
    <time dateTime={iso} title={iso}>
      {iso.slice(0, 16).replace("T", " ")} UTC{clock !== null ? ` (${untilText(iso, new Date(clock))})` : ""}
    </time>
  );
}
