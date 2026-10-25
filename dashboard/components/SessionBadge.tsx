import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { SignInButton } from "./SignInButton";

export async function SessionBadge() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session) return <SignInButton />;
  return <span className="muted">Signed in as {session.user.name}</span>;
}
