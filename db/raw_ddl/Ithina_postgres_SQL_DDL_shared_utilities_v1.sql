-- ============================================================================
-- Ithina: Shared utilities
-- Postgres SQL DDL
-- Version: v1
--
-- This is the FIRST migration to run. All other DDL files depend on
-- the functions, enums, and extensions defined here.
--
-- Contents:
--   1. Required Postgres extensions (ltree).
--   2. Shared trigger functions (set_updated_at_timestamp).
--   3. Shared enum types used across multiple tables:
--      - tax_treatment_enum  (stores, store_current_positions, ...)
--      - actor_user_type_enum (audit columns on tenants/stores/org_nodes,
--                              user_role_assignments, audit_logs, ...)
--
-- Migration order:
--   1. shared_utilities  (this file)
--   2. platform_users
--   3. tenants
--   4. tenant_users
--   5. org_nodes
--   6. stores
--   7. rbac
--   8. (future) audit_logs
--
-- Dependencies: none. Postgres 13+.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Required extensions
-- ----------------------------------------------------------------------------

-- ltree: hierarchical path data type. Used by org_nodes for materialised
-- path-based descendant/ancestor queries. Cloud SQL supports ltree
-- (verified: in the cloudsqladmin extension allowlist).
CREATE EXTENSION IF NOT EXISTS ltree;

-- gen_random_uuid() is built into Postgres 13+ (no extension needed).
-- For Postgres < 13, you'd need:
--   CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- Not required at our target version.


-- ----------------------------------------------------------------------------
-- Shared trigger function: refresh updated_at on every UPDATE
--
-- Used by all tables that have an updated_at column. Each table attaches
-- a BEFORE UPDATE trigger that calls this function.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- Shared function: uuidv7()
--
-- UUIDv7 generator (RFC 9562). Vendored from kjmph's PL/pgSQL
-- reference implementation; renamed from uuid_generate_v7() to
-- uuidv7() so that table DEFAULTs do not need to change when Cloud
-- SQL Postgres 18 lands with a native uuidv7() (see FN-AB-13).
--
-- Source:    https://gist.github.com/kjmph/5bd772b2c2df145aa645b837da7eca74
-- Vendored:  db/raw_ddl/_vendored/uuid7-kjmph.sql
-- Author:    Kyle Hubert (kjmph), 2023
-- Licence:   MIT (full text in db/raw_ddl/_vendored/license.md)
-- Reference: RFC 9562 (UUID v7)
-- Used by:   every metadata-table PK DEFAULT (per D-21 in CLAUDE.md);
--            canonical-layer tables inherit the convention.
--
-- The function body below is byte-for-byte identical to
-- uuid_generate_v7() in the vendored source; only the function name
-- has been changed. Do not reformat or "improve" the body; if the
-- vendored source updates, replace this block, do not edit in place.
-- ----------------------------------------------------------------------------

create or replace function uuidv7()
returns uuid
as $$
begin
  -- use random v4 uuid as starting point (which has the same variant we need)
  -- then overlay timestamp
  -- then set version 7 by flipping the 2 and 1 bit in the version 4 string
  return encode(
    set_bit(
      set_bit(
        overlay(uuid_send(gen_random_uuid())
                placing substring(int8send(floor(extract(epoch from clock_timestamp()) * 1000)::bigint) from 3)
                from 1 for 6
        ),
        52, 1
      ),
      53, 1
    ),
    'hex')::uuid;
end
$$
language plpgsql
volatile;

COMMENT ON FUNCTION uuidv7() IS 'UUIDv7 generator (RFC 9562). Vendored from kjmph PL/pgSQL reference; see Ithina_postgres_SQL_DDL_shared_utilities_v1.sql header for provenance. Used as DEFAULT for every metadata-table PK per D-21.';


-- ----------------------------------------------------------------------------
-- Shared enum: tax_treatment_enum
--
-- Used by stores and downstream canonical tables that carry per-store
-- tax treatment (store_current_positions, sale history, etc.). Defined
-- once here to avoid duplicate-definition conflicts.
-- ----------------------------------------------------------------------------

CREATE TYPE tax_treatment_enum AS ENUM (
    'EXCLUSIVE',
        -- Prices shown without tax (US convention).
    'INCLUSIVE'
        -- Prices shown with tax (EU, UK, IN convention).
);


-- ----------------------------------------------------------------------------
-- Shared enum: actor_user_type_enum
--
-- Used by every audit column pair (created_by_user_id +
-- created_by_user_type, etc.) and by user_role_assignments to
-- discriminate which user table a UUID actor refers to. Defined here
-- once because it is referenced before tenant_users (where it was
-- inline-defined in v1 of that file).
-- ----------------------------------------------------------------------------

CREATE TYPE actor_user_type_enum AS ENUM (
    'PLATFORM',
        -- Actor is a row in platform_users.
    'TENANT'
        -- Actor is a row in tenant_users.
);
