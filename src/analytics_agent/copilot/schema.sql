-- Warehouse schema for the 311 copilot (Postgres 16). Loaded by scripts/v2/pg_load.py.
-- Grain of wh.service_request: one row per service request (current state in the source build).
DROP SCHEMA IF EXISTS wh CASCADE;
DROP SCHEMA IF EXISTS acl CASCADE;
CREATE SCHEMA wh;
CREATE SCHEMA acl;

CREATE TABLE wh.department (
    dept_code   text PRIMARY KEY,
    department  text NOT NULL UNIQUE
);

CREATE TABLE wh.community_area (
    community_area      integer PRIMARY KEY,
    community_area_name text NOT NULL,
    population_acs      integer
);

-- The City's published median days to close per request type (dataset u6fz-87ei); a benchmark,
-- not an official SLA (the City publishes no per-type targets).
CREATE TABLE wh.request_benchmark (
    sr_type         text PRIMARY KEY,
    benchmark_days  double precision NOT NULL
);

CREATE TABLE wh.service_request (
    sr_number           text PRIMARY KEY,
    sr_type             text NOT NULL,
    sr_short_code       text,
    dept_code           text NOT NULL REFERENCES wh.department (dept_code),
    created_department  text,
    origin              text,
    status              text NOT NULL,
    is_open             boolean NOT NULL,
    is_canceled         boolean NOT NULL,
    is_duplicate        boolean NOT NULL,
    parent_sr_number    text,
    created_at          timestamp NOT NULL,
    closed_at           timestamp,
    last_modified_at    timestamp,
    community_area      integer,
    ward                integer
);
