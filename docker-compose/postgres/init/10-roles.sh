#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# Runs once, when the Postgres volume is first created. Makes three logins:
#
#   atomic          owns the atomic database; the runtime's own login
#   atomic_reader   read-only on what atomic creates, for Grafana
#   atomic_monitor  pg_monitor only, for the collector's Postgres metrics
#
# None of them is a superuser. atomic creates its schemas and runs its
# migrations itself, as the owner of its database. Passwords are read from
# the environment inside psql (\getenv), so they never appear on a command
# line. Changing them in .env later does not change them here; use
# ALTER ROLE ... PASSWORD, or recreate the volume.
set -eu

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname postgres <<'SQL'
\getenv atomic_pw ATOMIC_DB_PASSWORD
\getenv reader_pw ATOMIC_READER_PASSWORD
\getenv monitor_pw ATOMIC_MONITOR_PASSWORD

CREATE ROLE atomic LOGIN PASSWORD :'atomic_pw';
CREATE ROLE atomic_reader LOGIN PASSWORD :'reader_pw';
CREATE ROLE atomic_monitor LOGIN PASSWORD :'monitor_pw';

CREATE DATABASE atomic OWNER atomic;

-- Only the logins that need a database may connect to it.
REVOKE ALL ON DATABASE atomic FROM PUBLIC;
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE atomic TO atomic;
GRANT CONNECT ON DATABASE atomic TO atomic_reader, atomic_monitor;
-- The collector's Postgres receiver runs its cluster-wide queries here.
GRANT CONNECT ON DATABASE postgres TO atomic_monitor;

GRANT pg_monitor TO atomic_monitor;

-- Read-only. These are defaults for the session, which the login could
-- change; what stops it writing is that it holds no privilege to write.
ALTER ROLE atomic_reader SET default_transaction_read_only = on;
ALTER ROLE atomic_reader SET statement_timeout = '15s';

\connect atomic

-- The reader sees the schemas and tables atomic creates from now on,
-- durare's included, and nothing else: not other databases, not the
-- system catalogs' password hashes. It can still read every column of
-- those tables, workflow inputs and outputs included.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO atomic;
GRANT USAGE ON SCHEMA public TO atomic_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE atomic GRANT USAGE ON SCHEMAS TO atomic_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE atomic GRANT SELECT ON TABLES TO atomic_reader;
SQL
