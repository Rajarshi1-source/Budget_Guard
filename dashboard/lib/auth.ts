import "server-only";
import { betterAuth } from "better-auth";
import { nextCookies } from "better-auth/next-js";
import { Pool } from "pg";

// Better Auth (Auth.js is now maintained by the Better Auth team; new projects start here).
// Sessions live in PostgreSQL, schema "auth", separate from BudgetGuard's tables.
export const auth = betterAuth({
  baseURL: process.env.BETTER_AUTH_URL,          // e.g. https://budgetguard.example.dev
  secret: process.env.BETTER_AUTH_SECRET,        // 32+ random bytes; rotate like any signing key
  database: new Pool({ connectionString: process.env.AUTH_DATABASE_URL, options: "-c search_path=auth" }),
  socialProviders: {
    github: {
      clientId: process.env.GITHUB_CLIENT_ID ?? "",
      clientSecret: process.env.GITHUB_CLIENT_SECRET ?? "",
      scope: ["read:user"],          // identity only; authorization is decided by the platform API
    },
  },
  plugins: [nextCookies()],          // lets server actions set cookies
});
