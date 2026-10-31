-- 008: freeze impact per service and day (plan W6.6; Rev 1 idea §3.2; feeds D-6's false-freeze
-- minutes). A block's freeze lasts until the service's next decision; consecutive blocks therefore
-- partition the time instead of overlapping, and the last block runs to now (at most 7 days).
-- Admin-bypass records are not gate decisions and are left out.
CREATE VIEW freeze_impact AS
WITH ordered AS (
    SELECT d.service_id, d.decided_at, d.verdict,
           lead(d.decided_at) OVER (PARTITION BY d.service_id ORDER BY d.decided_at, d.id) AS next_at
    FROM decisions d
    WHERE d.reason_code <> 'ADMIN_BYPASS'
)
SELECT s.name AS service,
       time_bucket(INTERVAL '1 day', o.decided_at) AS day,
       count(*) FILTER (WHERE o.verdict = 'block') AS blocked_deploys,
       count(*) FILTER (WHERE o.verdict <> 'block') AS admitted_deploys,
       round((sum(EXTRACT(epoch FROM COALESCE(o.next_at, LEAST(now(), o.decided_at + INTERVAL '7 days'))
                                   - o.decided_at)) FILTER (WHERE o.verdict = 'block') / 60)::numeric, 1)
           AS freeze_minutes
FROM ordered o JOIN services s ON s.id = o.service_id
GROUP BY s.name, time_bucket(INTERVAL '1 day', o.decided_at);

GRANT SELECT ON freeze_impact TO bg_grafana;
GRANT SELECT ON freeze_impact TO bg_app;
