# Runbook: rotate a bearer token or the webhook secret with no failed request

Every caller-facing secret has two slots, so a rotation never needs downtime
(`security-and-api.md` §6–§7):

| Secret | Current | Rotation slot | Consumers to switch |
| --- | --- | --- | --- |
| Dashboard → API token | `BG_DASHBOARD_TOKEN` | `BG_DASHBOARD_TOKEN_NEXT` | dashboard (`BUDGETGUARD_DASHBOARD_TOKEN`) |
| Alertmanager → API token | `BG_ALERTMANAGER_TOKEN` | `BG_ALERTMANAGER_TOKEN_NEXT` | Alertmanager `credentials_file` |
| GitHub webhook secret | `BG_GITHUB_WEBHOOK_SECRET` | `BG_GITHUB_WEBHOOK_SECRET_PREVIOUS` | GitHub App settings |

An empty slot means unset. Since W8.5 the platform turns a blank slot into "no secret" and never
lets an empty bearer match. Before that fix, compose's `${X_NEXT:-}` would have produced an empty
secret that `Authorization: Bearer ` matched.

## Steps (bearer tokens)

1. Generate: `openssl rand -hex 32`.
2. Put it in the `_NEXT` slot on the API and restart the API. Both tokens are now accepted.
3. Switch the consumer to the new token. On the API, make the new value current and the old one
   `_NEXT`. Restart both.
4. Remove `_NEXT` from the API. The old token is now rejected.
5. Update the stored copy: `deploy/compose/secrets/local.env`, or the k3s Secret via `bootstrap.sh`.

For the webhook secret the order is reversed. Set the new value as current and the old one as
`_PREVIOUS`. Change the secret in the GitHub App, then remove `_PREVIOUS` once a delivery verifies.

## Drill (W8.5): dashboard token, compose, 31 Oct 2026

`bash deploy/compose/rotation-drill.sh` runs the bearer-token steps against the live stack. It
prints status codes only. `dashboard=cards` means the page streamed real service cards. A 200
alone would not prove that, because a failed API call still returns 200.

| Step | old token | new token | empty bearer | dashboard |
| --- | --- | --- | --- | --- |
| 0. before (current = old) | 200 | 401 | 401 | cards |
| 1. API accepts next | 200 | 200 | 401 | cards |
| 2. dashboard switched (current = new, next = old) | 200 | 200 | 401 | cards |
| 3. old retired | 401 | 200 | 401 | cards |
| 4. persisted in local.env | 401 | 200 | 401 | cards |

Result: no request failed at any step, and the old token is dead after step 3.
