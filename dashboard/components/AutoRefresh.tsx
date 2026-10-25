"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-renders server components every `seconds` while the tab is visible. No websockets needed
 *  for numbers that change on a 60-second snapshot cadence. */
export function AutoRefresh({ seconds = 15 }: { seconds?: number }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, seconds * 1000);
    return () => clearInterval(id);
  }, [router, seconds]);
  return null;
}
