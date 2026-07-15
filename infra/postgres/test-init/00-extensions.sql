-- Extensions required by application migrations and integration tests.
-- This runs only in the isolated postgres-test cluster and deliberately does
-- not create Temporal or Keycloak service databases.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
