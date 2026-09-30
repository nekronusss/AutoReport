CREATE SCHEMA meta;
CREATE SCHEMA raw;
CREATE SCHEMA mart;
CREATE TABLE meta.runs (
 run_id uuid PRIMARY KEY, logical_key text UNIQUE NOT NULL, dataset text NOT NULL,
 period date NOT NULL, snapshot_id text NOT NULL, source text NOT NULL,
 as_of timestamptz NOT NULL, manifest jsonb NOT NULL, code_version text NOT NULL,
 config_hash text NOT NULL, state text NOT NULL, error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), heartbeat timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz, duration_ms bigint, quality_ms bigint, row_count bigint DEFAULT 0
);
CREATE INDEX ON meta.runs(dataset,period,created_at);
CREATE TABLE meta.attempts (
 attempt_id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES meta.runs,
 state text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE INDEX ON meta.attempts(run_id);
CREATE TABLE meta.source_batches (
 run_id uuid REFERENCES meta.runs, table_name text NOT NULL, path text NOT NULL,
 sha256 text NOT NULL, row_count bigint NOT NULL, PRIMARY KEY(run_id, table_name)
);
CREATE TABLE raw.records (
 run_id uuid REFERENCES meta.runs, table_name text NOT NULL, ordinal bigint NOT NULL,
 payload jsonb NOT NULL, sha256 text NOT NULL, PRIMARY KEY(run_id, table_name, ordinal)
);
CREATE TABLE meta.check_results (
 run_id uuid REFERENCES meta.runs, rule_id text NOT NULL, version text NOT NULL,
 description text NOT NULL, severity text NOT NULL, status text NOT NULL,
 violations bigint NOT NULL, duration_ms bigint NOT NULL, examples jsonb NOT NULL,
 threshold text NOT NULL DEFAULT '0', PRIMARY KEY(run_id,rule_id)
);
CREATE TABLE meta.rejected_records (
 run_id uuid REFERENCES meta.runs, table_name text NOT NULL, ordinal bigint NOT NULL,
 reason text NOT NULL, technical_id text, PRIMARY KEY(run_id,table_name,ordinal)
);
CREATE TABLE meta.publications (
 dataset text NOT NULL, period date NOT NULL, run_id uuid NOT NULL REFERENCES meta.runs,
 published_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(dataset,period)
);
CREATE TABLE meta.report_artifacts (
 run_id uuid REFERENCES meta.runs, name text NOT NULL, path text NOT NULL,
 sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(run_id,name)
);
CREATE TABLE meta.outbox (
 event_id uuid PRIMARY KEY, run_id uuid REFERENCES meta.runs, kind text NOT NULL,
 status text NOT NULL DEFAULT 'PENDING', attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(),
 delivered_at timestamptz, error_code text, UNIQUE(run_id,kind)
);
CREATE INDEX ON meta.outbox(status,available_at);
CREATE TABLE meta.audit_events (
 event_id bigserial PRIMARY KEY, run_id uuid REFERENCES meta.runs,
 action text NOT NULL, reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON meta.audit_events(run_id);
CREATE TABLE meta.service_health (name text PRIMARY KEY, last_seen timestamptz NOT NULL, details jsonb);
CREATE TABLE mart.fact_orders_versions (
 run_id uuid NOT NULL REFERENCES meta.runs, order_id text NOT NULL, customer_id text NOT NULL,
 business_date date NOT NULL, status text NOT NULL, currency text NOT NULL,
 total_amount numeric(18,2) NOT NULL, item_amount numeric(18,2) NOT NULL,
 captured_amount numeric(18,2) NOT NULL, refunded_amount numeric(18,2) NOT NULL,
 net_collected_amount numeric(18,2) NOT NULL, PRIMARY KEY(run_id,order_id)
);
CREATE TABLE mart.daily_summary_versions (
 run_id uuid NOT NULL REFERENCES meta.runs, business_date date NOT NULL, currency text NOT NULL,
 orders_count bigint NOT NULL, customers_count bigint NOT NULL, ordered_amount numeric(18,2) NOT NULL,
 captured_amount numeric(18,2) NOT NULL, refunded_amount numeric(18,2) NOT NULL,
 net_collected_amount numeric(18,2) NOT NULL, PRIMARY KEY(run_id,business_date,currency)
);
CREATE VIEW mart.current_orders AS SELECT f.* FROM mart.fact_orders_versions f
 JOIN meta.publications p USING(run_id);
CREATE VIEW mart.current_daily_summary AS SELECT f.* FROM mart.daily_summary_versions f
 JOIN meta.publications p USING(run_id);
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA meta,raw TO dqflow_runner;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA meta,raw TO dqflow_runner;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA meta TO dqflow_runner,dqflow_publisher;
GRANT USAGE ON SCHEMA meta,mart TO dqflow_publisher,dqflow_reader,dqflow_runner;
GRANT SELECT ON ALL TABLES IN SCHEMA mart TO dqflow_reader,dqflow_runner;
GRANT SELECT ON ALL TABLES IN SCHEMA meta TO dqflow_publisher;
GRANT SELECT ON meta.runs,meta.publications,meta.check_results,meta.report_artifacts,meta.outbox,meta.service_health TO dqflow_reader;
GRANT INSERT,UPDATE ON meta.runs,meta.publications,meta.outbox,meta.audit_events TO dqflow_publisher;
GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA mart TO dqflow_publisher;

