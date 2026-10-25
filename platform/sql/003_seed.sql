INSERT INTO services (name, owner, failure_mode) VALUES
  ('auth-service','team-identity','fail_open'),
  ('orders-service','team-commerce','fail_open'),
  ('inventory-service','team-commerce','fail_open'),
  ('notifications-service','team-messaging','fail_open')
ON CONFLICT (name) DO NOTHING;

-- slos rows are upserted by `bg sync-slos` from slo/*.yaml (spec_hash detects drift).
-- policy_versions: the platform inserts the running policy version on boot (git log -1 --format=%H -- policy/freeze-policy.yaml, baked into the image as BG_POLICY_VERSION):
--   INSERT INTO policy_versions (version) VALUES (:sha) ON CONFLICT DO NOTHING;
