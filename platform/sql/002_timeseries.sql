ALTER TABLE sli_buckets SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'slo_id',
    timescaledb.compress_orderby   = 'bucket_start DESC'
);
SELECT add_compression_policy('sli_buckets', INTERVAL '7 days');
SELECT add_retention_policy('sli_buckets', INTERVAL '400 days');   -- 13 months

CREATE MATERIALIZED VIEW sli_hourly WITH (timescaledb.continuous) AS
SELECT slo_id,
       time_bucket(INTERVAL '1 hour', bucket_start) AS hour,
       sum(total) AS total, sum(bad) AS bad, sum(self_bad) AS self_bad,
       count(*) FILTER (WHERE integrity <> 'ok') AS degraded_buckets
FROM sli_buckets
GROUP BY slo_id, hour
WITH NO DATA;
SELECT add_continuous_aggregate_policy('sli_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '5 minutes');

-- [added] hierarchical daily rollup for reports and the backtester
CREATE MATERIALIZED VIEW sli_daily WITH (timescaledb.continuous) AS
SELECT slo_id,
       time_bucket(INTERVAL '1 day', hour) AS day,
       sum(total) AS total, sum(bad) AS bad, sum(self_bad) AS self_bad,
       sum(degraded_buckets) AS degraded_buckets
FROM sli_hourly
GROUP BY slo_id, day
WITH NO DATA;
SELECT add_continuous_aggregate_policy('sli_daily',
    start_offset => INTERVAL '10 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');
