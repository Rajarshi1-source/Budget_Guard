// Print the SQL Better Auth needs in schema "auth", using the installed better-auth version's own
// migration planner (what `npx auth generate` runs). Alembic owns the result (platform/sql/006_auth.sql)
// so one tool owns every schema; re-run after a better-auth upgrade and commit any difference:
//   AUTH_DATABASE_URL=postgresql://bg_auth:...@host:5432/budgetguard node scripts/auth-schema.mjs
// It needs a database because the planner diffs against what exists; point it at an empty one.
import { getMigrations } from "better-auth/db/migration";
import { Pool } from "pg";

const pool = new Pool({ connectionString: process.env.AUTH_DATABASE_URL, options: "-c search_path=auth" });
const { compileMigrations } = await getMigrations({
  database: pool,
  socialProviders: { github: { clientId: "x", clientSecret: "x" } },   // same tables as lib/auth.ts
});
process.stdout.write(await compileMigrations());
await pool.end();
