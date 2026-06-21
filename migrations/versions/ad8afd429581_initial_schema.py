"""initial schema

Revision ID: ad8afd429581
Revises:
Create Date: 2026-05-01 20:16:01.411726

Wraps the 8 DDL files in db/raw_ddl/ as a single Alembic migration.
DDL content is embedded as Python string literals at generation time;
the migration is self-contained and does not depend on db/raw_ddl/ at
runtime. Production deployments can ship migrations/ without the DDL
source on disk.

The DDL files in db/raw_ddl/ remain the source of truth for schema
GENERATION. Once a DDL change is needed, regenerate the migration (or
write a new ALTER-style migration) rather than editing this file.

Extensions (ltree, pgcrypto) are NOT installed by this migration.
CREATE EXTENSION requires superuser privilege; the application role is
NOSUPERUSER NOBYPASSRLS by design (see CLAUDE.md "Current state").
Extensions are a database-setup precondition, installed once by a
privileged role before migrations run. The upgrade() begins with a
precondition check that surfaces a clear error if either extension is
missing.

Schema name is parameterised via DB_SCHEMA env var (D-15). Tables
resolve to the configured schema via search_path, set by env.py before
this migration runs. The migration itself contains no hardcoded schema
literal.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "ad8afd429581"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ============================================================================
# DDL content
# ============================================================================
#
# Each constant below holds the content of one DDL file from db/raw_ddl/,
# embedded at generation time. CREATE EXTENSION statements have been
# stripped (extensions are a setup precondition; see header docstring).
# Other statements pass through unchanged.

SQL_SHARED_UTILITIES = r"""
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
"""

SQL_LOOKUPS = r"""
-- ============================================================================
-- Ithina: Lookups
-- Postgres SQL DDL
-- Version: v1
--
-- Platform-global table of named lists of options. Serves the frontend
-- as the authoritative source for dropdown values, filter options, and
-- enum-as-display rendering.
--
-- Examples of lists:
--   list_name = 'tier'            -> ENTERPRISE, MID_MARKET, SMB, SINGLE_STORE
--   list_name = 'industry'        -> CONVENIENCE, GROCERY, SPECIALTY, ...
--   list_name = 'tenant_status'   -> ONBOARDING, ACTIVE, SUSPENDED, ...
--   list_name = 'audit_result'    -> SUCCESS, PENDING, DENIED
--
-- Design notes (per CLAUDE.md):
--   - Pattern P1: display-only catalogue. No FK from other tables.
--     Other tables use Postgres enums for their own values; this table
--     exists purely to drive UI dropdowns and filters.
--   - Platform-global: no tenant_id, no RLS. Same lists apply to all
--     tenants in v0. (FN-AB future: tenant-specific overrides if needed.)
--   - Sole writer: admin backend. Population in v0 is via seed migrations
--     so changes go through code review, not manual prod SQL.
--   - No audit-actor columns; catalogue tables managed exclusively via
--     migration (per D-13) do not record per-row actors; the seed
--     migration's git history is the audit trail.
--
-- Migration order:
--   1. shared_utilities
--   2. lookups            (this file)
--   3. platform_users
--   4. tenants
--   5. tenant_users
--   6. org_nodes
--   7. stores
--   8. rbac
--   9. (future) audit_logs
--
-- Dependencies: shared_utilities (set_updated_at_timestamp).
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Table: lookups
-- ----------------------------------------------------------------------------

CREATE TABLE lookups (
    id UUID NOT NULL DEFAULT uuidv7(),

    -- ----- identity --------------------------------------------------------
    list_name           TEXT        NOT NULL,
        -- The named list this row belongs to. snake_case, lowercase.
        -- Examples: 'tier', 'industry', 'tenant_status', 'audit_result'.
    code                TEXT        NOT NULL,
        -- The enum-style code value used on the wire and stored in other
        -- tables' enum columns. UPPER_SNAKE_CASE.
        -- Examples: 'ENTERPRISE', 'MID_MARKET', 'ACTIVE', 'PENDING'.
    display_name        TEXT        NOT NULL,
        -- Human-readable label for UI rendering. Title-Case typically.
        -- Examples: 'Enterprise', 'Mid-Market', 'Active', 'Pending Review'.
    description         TEXT        NULL,
        -- Optional longer explanation. Used for hover tooltips, help text.

    -- ----- presentation ----------------------------------------------------
    display_order       INTEGER     NOT NULL DEFAULT 0,
        -- Frontend ordering hint. Lower numbers shown first.
        -- Within a list, multiple rows can share an order; ties broken by
        -- display_name.
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
        -- Soft-deactivation. Inactive rows hidden from default API responses
        -- but preserved for historical reference (a tenant currently on a
        -- deactivated tier should still render correctly).

    -- ----- audit ---------------------------------------------------------
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ----- constraints -----------------------------------------------------
    CONSTRAINT pk_lookups
        PRIMARY KEY (id),

    CONSTRAINT uq_lookups_list_name_code
        UNIQUE (list_name, code),
        -- Natural key. (list_name, code) uniquely identifies a row.

    CONSTRAINT ck_lookups_list_name_format
        CHECK (list_name ~ '^[a-z][a-z0-9_]*$'),
        -- snake_case, must start with a letter. Prevents typos like
        -- 'Tier' vs 'tier' creating duplicate logical lists.

    CONSTRAINT ck_lookups_code_format
        CHECK (code ~ '^[A-Z][A-Z0-9_]*$'),
        -- UPPER_SNAKE_CASE. Wire-stable enum codes.

    CONSTRAINT ck_lookups_display_name_not_empty
        CHECK (length(btrim(display_name)) > 0),

    CONSTRAINT ck_lookups_display_order_non_negative
        CHECK (display_order >= 0)
);


-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------

-- Primary read pattern from API: "give me all active options in list X,
-- ordered for display."
CREATE INDEX ix_lookups_list_name_active_order
    ON lookups (list_name, is_active, display_order, display_name)
    WHERE is_active = TRUE;

-- Backstop index for staff queries that include inactive rows.
CREATE INDEX ix_lookups_list_name_all
    ON lookups (list_name, code);

CREATE INDEX ix_lookups_updated_at
    ON lookups (updated_at DESC);


-- ----------------------------------------------------------------------------
-- Trigger: refresh updated_at on every UPDATE
-- ----------------------------------------------------------------------------

CREATE TRIGGER trg_lookups_set_updated_at
    BEFORE UPDATE ON lookups
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- Application-layer invariants (enforced in service code, not SQL)
-- ----------------------------------------------------------------------------

-- AI-LK-01: Population is via seed migrations. Manual INSERT into prod is
-- disallowed. Adding a list_name or code requires a new Alembic migration.
--
-- AI-LK-02: Existing codes must not be renamed. Renaming a code breaks
-- every other table that stores that string in an enum column. Deprecate
-- by setting is_active = FALSE, then add a new code if the meaning has
-- changed.
--
-- AI-LK-03: list_name values are reserved and must be coordinated with the
-- frontend. Adding a new list_name requires frontend awareness.

-- End of file.
"""

SQL_PLATFORM_USERS = r"""
-- ============================================================================
-- Ithina: platform_users
-- Postgres SQL DDL
-- Version: v1
--
-- Ithina staff users. Internal employees who manage the platform across
-- all tenants (Super Admin, Platform Admin, Module Admin, Support Admin).
-- Physically separated from tenant_users to make staff/tenant data
-- segregation structural rather than policy-only (Pattern 2).
--
-- Key properties:
--   * No tenant_id column. Staff are platform-global.
--   * No RLS. Access controlled by DB role: only the staff DB role
--     (with BYPASSRLS for tenant-scoped tables) connects here.
--   * Auth0 is the credential authority. auth0_sub maps Auth0 identity
--     to local row. NULL during INVITED state, populated on first login.
--   * Audit columns self-reference platform_users (only staff create
--     other staff).
--
-- Lifecycle:
--   * INVITED  -- row created by an existing staff user; invite email
--                 sent; Auth0 signup not yet completed; auth0_sub NULL.
--   * ACTIVE   -- invite accepted; auth0_sub populated; can log in.
--   * SUSPENDED-- temporarily disabled; auth0_sub retained; cannot log in.
--   No INACTIVE / no soft-delete in v1. Hard delete blocked by FK
--   RESTRICT on referencing tables (audit history must remain resolvable).
--
-- Dependencies (must exist before this file runs):
--   * Shared utilities migration providing:
--       - function set_updated_at_timestamp()
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enum types used by platform_users
-- ----------------------------------------------------------------------------

CREATE TYPE platform_user_status_enum AS ENUM (
    'INVITED',
    'ACTIVE',
    'SUSPENDED'
);


-- ----------------------------------------------------------------------------
-- platform_users
-- ----------------------------------------------------------------------------

CREATE TABLE platform_users (

    -- ---------- Surrogate primary key ----------
    id                          UUID                            NOT NULL DEFAULT uuidv7(),

    -- ---------- External identity ----------
    auth0_sub                   TEXT                            NULL,
        -- Auth0 'sub' claim. Populated when the user completes Auth0
        -- signup. NULL only while status = INVITED. Unique platform-wide
        -- when set.

    -- ---------- Identity ----------
    email                       TEXT                            NOT NULL,
        -- Stored lowercased; CHECK enforces no uppercase characters.
        -- Globally unique across platform_users.
    full_name                   TEXT                            NOT NULL,

    -- ---------- Lifecycle ----------
    status                      platform_user_status_enum       NOT NULL DEFAULT 'INVITED',

    -- ---------- Invitation ----------
    invited_at                  TIMESTAMPTZ                     NULL,
        -- Set when the invite email is dispatched. Typically NOT NULL on
        -- the first save; nullable to accommodate the rare seed/system
        -- user that bypasses invite flow.
    invitation_accepted_at      TIMESTAMPTZ                     NULL,
        -- Set when status transitions INVITED -> ACTIVE.

    -- ---------- Suspension ----------
    suspended_at                TIMESTAMPTZ                     NULL,
        -- Set when status transitions to SUSPENDED. Cleared (NULL) when
        -- status returns to ACTIVE.
    suspended_by_user_id        UUID                            NULL,
        -- platform_users.id of the staff user who performed the suspension.

    -- ---------- Audit ----------
    created_at                  TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    created_by_user_id          UUID                            NULL,
        -- platform_users.id of the staff user who created this row.
        -- NULL only for the first seeded user (chicken-and-egg) or
        -- system-created rows.
    updated_at                  TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    updated_by_user_id          UUID                            NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_platform_users
        PRIMARY KEY (id),

    -- Self-referencing FKs for audit columns. RESTRICT prevents deletion
    -- of a staff user while their UUID is referenced by other rows.
    CONSTRAINT fk_platform_users_created_by
        FOREIGN KEY (created_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_platform_users_updated_by
        FOREIGN KEY (updated_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_platform_users_suspended_by
        FOREIGN KEY (suspended_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT ck_platform_users_email_format
        CHECK (
            email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'
        ),

    CONSTRAINT ck_platform_users_email_lowercase
        CHECK (email = LOWER(email)),

    CONSTRAINT ck_platform_users_full_name_length
        CHECK (LENGTH(full_name) BETWEEN 1 AND 200),

    -- auth0_sub may be NULL only while status = INVITED.
    CONSTRAINT ck_platform_users_auth0_sub_consistency
        CHECK (
            (status = 'INVITED' AND auth0_sub IS NULL)
            OR
            (status IN ('ACTIVE', 'SUSPENDED') AND auth0_sub IS NOT NULL)
        ),

    -- invitation_accepted_at must be set once status leaves INVITED.
    CONSTRAINT ck_platform_users_invitation_accepted_consistency
        CHECK (
            (status = 'INVITED'
                AND invitation_accepted_at IS NULL)
            OR
            (status IN ('ACTIVE', 'SUSPENDED')
                AND invitation_accepted_at IS NOT NULL)
        ),

    CONSTRAINT ck_platform_users_suspended_consistency
        CHECK (
            (status = 'SUSPENDED'
                AND suspended_at IS NOT NULL
                AND suspended_by_user_id IS NOT NULL)
            OR
            (status IN ('INVITED', 'ACTIVE')
                AND suspended_at IS NULL
                AND suspended_by_user_id IS NULL)
        )
);


-- ----------------------------------------------------------------------------
-- Indexes beyond PK (which auto-creates an index)
-- ----------------------------------------------------------------------------

-- Globally unique email (case-insensitive uniqueness already enforced by
-- the email_lowercase CHECK above; plain UNIQUE is sufficient).
CREATE UNIQUE INDEX uq_platform_users_email
    ON platform_users (email);

-- Globally unique auth0_sub when set; multiple NULLs allowed (multiple
-- INVITED users coexist before any has signed up).
CREATE UNIQUE INDEX uq_platform_users_auth0_sub
    ON platform_users (auth0_sub)
    WHERE auth0_sub IS NOT NULL;

-- Status filtering ("all active staff", "all invited not yet accepted").
CREATE INDEX ix_platform_users_status
    ON platform_users (status);


-- ----------------------------------------------------------------------------
-- Trigger: keep updated_at fresh on every UPDATE
-- ----------------------------------------------------------------------------

CREATE TRIGGER tg_platform_users_set_updated_at
    BEFORE UPDATE ON platform_users
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- No Row-Level Security
--
-- platform_users is platform-global. There is no tenant boundary. Access
-- is controlled at the DB role level: only the staff DB role connects
-- to this table. Tenant DB roles must not be granted SELECT, INSERT,
-- UPDATE, or DELETE on platform_users.
--
-- This is a deliberate departure from the RLS pattern used on all
-- tenant-owned tables. The structural separation between
-- platform_users and tenant_users (Pattern 2) is the security boundary.
-- ----------------------------------------------------------------------------


-- ============================================================================
-- Application-layer invariants
--
-- Captured here for reference. Not enforced by DDL in v1; document in
-- CLAUDE.md and enforce in service code.
--
-- AI-PU-01: Status transition INVITED -> SUSPENDED is not allowed.
--           Cancel the invite instead (see AI-PU-02). The DB CHECK
--           constraint ck_platform_users_auth0_sub_consistency rejects
--           SUSPENDED with auth0_sub IS NULL, so this transition fails
--           at the DB layer for an unaccepted invite. App layer should
--           reject it explicitly with a clear error rather than letting
--           the CHECK fail.
--
-- AI-PU-02: Hard-delete of a platform_user is permitted ONLY when
--           status = INVITED and the user has never been ACTIVE
--           (invitation_accepted_at IS NULL). Once a user has been
--           ACTIVE, they cannot be deleted; use SUSPENDED instead.
--           This is the documented exception to the project-wide
--           "no hard delete of users" rule, scoped to cancelled or
--           never-accepted invites only.
--
-- AI-PU-03: Self-suspension (suspended_by_user_id = id) is structurally
--           allowed by FK but should be rejected at the app layer.
--
-- AI-PU-04: created_by_user_id and updated_by_user_id must be populated
--           on every write except the seeded first user (chicken-and-egg).
--           App layer is authoritative; trigger does not enforce this.
--
-- AI-PU-05: Email values must be lowercased before insert. The
--           ck_platform_users_email_lowercase CHECK rejects mixed-case
--           values; app layer should lowercase upstream to avoid
--           surfacing CHECK violations to users.
-- ============================================================================
"""

SQL_TENANTS = r"""
-- ============================================================================
-- Ithina: tenants
-- Postgres SQL DDL
-- Version: v3
--
-- This file defines tenants and the enum types it depends on.
-- tenants is the root of the multi-tenancy isolation hierarchy. Every other
-- tenant-owned entity in the schema references tenants.id via tenant_id
-- and enforces row-level isolation against this boundary.
--
-- Dependencies (must exist before this file runs):
--   * platform_users (id) -- referenced by audit columns
--   * Shared utilities migration providing:
--       - function set_updated_at_timestamp()
--
-- Changes from v2:
--   * Audit columns now reference platform_users only. Tenants are
--     created, updated, suspended, and terminated exclusively by Ithina
--     staff (FN-AB-03). Single FK to platform_users.id; no actor type
--     enum needed.
--   * Renamed audit columns for naming consistency with platform_users /
--     tenant_users / RBAC:
--       - created_by      -> created_by_user_id
--       - updated_by      -> updated_by_user_id
--       - terminated_by   -> terminated_by_user_id
--   * Added suspended_by_user_id column to mirror suspended_at and
--     match the audit pattern used elsewhere. Suspension consistency
--     CHECK updated to enforce the actor.
--   * Added FK constraints to platform_users on all four actor columns.
--
-- Changes from v1 (retained in v3):
--   * BEFORE UPDATE trigger to refresh updated_at automatically.
--   * display_code: case-insensitive UNIQUE via LOWER() expression index;
--     format CHECK enforces URL-friendly slug shape.
--   * country: TEXT, accepts full names or abbreviations.
--   * contact_email: stored lowercased via CHECK.
--   * suspended_at + ck_tenants_suspended_consistency.
--   * region column (US / EU).
--   * as_of_date columns for self-reported commercial figures.
--   * RLS enabled with FORCE; default-deny via NULL session var.
--   * Removed indexes on tier and industry (small table; no benefit).
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enum types used by tenants
-- ----------------------------------------------------------------------------

CREATE TYPE tenant_status_enum AS ENUM (
    'ONBOARDING',
    'TRIAL',
    'ACTIVE',
    'SUSPENDED',
    'TERMINATED'
);

CREATE TYPE tenant_tier_enum AS ENUM (
    'ENTERPRISE',
    'MID_MARKET',
    'SMB',
    'SINGLE_STORE'
);

CREATE TYPE tenant_industry_enum AS ENUM (
    'CONVENIENCE_FUEL',
    'CONVENIENCE',
    'GROCERY',
    'HYPERMART',
    'SPECIALITY_GROCERY',
    'ORGANIC_GROCERY'
);

CREATE TYPE tenant_region_enum AS ENUM (
    'US',
    'EU'
);


-- ----------------------------------------------------------------------------
-- Helper function: refresh updated_at on UPDATE
--
-- Generic trigger function used by any table that has an updated_at column.
-- Defined here for tenants; safe to redefine identically when other tables
-- import it. If a shared utilities file is introduced later, move this there.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- tenants
--
-- A customer organisation on the Ithina platform. The top-level isolation
-- boundary: every tenant-owned entity carries tenant_id and enforces
-- row-level isolation against this row.
-- ----------------------------------------------------------------------------

CREATE TABLE tenants (

    -- ---------- Surrogate primary key ----------
    id                          UUID                    NOT NULL DEFAULT uuidv7(),

    -- ---------- Identity ----------
    name                        TEXT                    NOT NULL,
    display_code                TEXT                    NULL,
        -- URL-friendly short identifier for the tenant (e.g. 'acme-retail').
        -- Used by admin URLs, log filtering, support tooling.
        -- Optional at creation; expected to be set during onboarding.
        -- Stored as lowercase by app convention; uniqueness is enforced
        -- case-insensitively via expression index below.

    country                     TEXT                    NULL,
        -- Country of the tenant's home operation. Accepts a full country
        -- name (e.g. 'Poland', 'India') or an abbreviation (e.g. 'USA',
        -- 'UK'). No fixed format enforced at the DB layer beyond
        -- whitespace/digit-only rejection; canonicalisation and lookup
        -- is the application's responsibility.

    region                      tenant_region_enum      NOT NULL,
        -- Deployment region for the tenant. Pinned at tenant creation;
        -- determines which regional Postgres instance the tenant lives
        -- in. Tenants do not move regions.

    -- ---------- Classification ----------
    tier                        tenant_tier_enum        NULL,
        -- Commercial segmentation tier. Set during onboarding once the
        -- tenant's scale and contract shape are known.

    industry                    tenant_industry_enum    NULL,
        -- Retail vertical. Drives default category mappings, regulatory
        -- defaults, and benchmarking peer groups.

    -- ---------- Commercial profile ----------
    monthly_revenue_usd         NUMERIC(15, 2)          NULL,
        -- Tenant's monthly revenue in USD. Self-reported at onboarding
        -- (or at the as-of date below). Used for tier validation and
        -- commercial reporting. Not maintained continuously; treat as
        -- point-in-time self-report.
    monthly_revenue_as_of_date  DATE                    NULL,
        -- The date the monthly_revenue_usd figure was reported as of.
        -- NULL when monthly_revenue_usd is NULL.

    number_of_stores            INTEGER                 NULL,
        -- Total store count across the tenant. Self-reported at onboarding
        -- (or at the as-of date below). Authoritative store count is the
        -- count of rows in the stores table scoped to this tenant_id;
        -- this column captures the self-reported figure.
    number_of_stores_as_of_date DATE                    NULL,
        -- The date the number_of_stores figure was reported as of.
        -- NULL when number_of_stores is NULL.

    -- ---------- Primary contact ----------
    primary_contact_name        TEXT                    NULL,
    contact_email               TEXT                    NULL,
        -- Stored lowercased; CHECK enforces no uppercase characters.

    -- ---------- Lifecycle ----------
    status                      tenant_status_enum      NOT NULL DEFAULT 'ONBOARDING',

    -- ---------- Audit ----------
    created_at                  TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    created_by_user_id          UUID                    NULL,
        -- platform_users.id of the staff user who created this tenant.
        -- NULL only for the seeded first tenant or system-created rows.
    updated_at                  TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    updated_by_user_id          UUID                    NULL,
        -- platform_users.id of the staff user who last updated this row.
        -- NULL on creation; set on every subsequent UPDATE by the app layer.
    suspended_at                TIMESTAMPTZ             NULL,
        -- Set when status transitions to SUSPENDED. Cleared (NULL) when
        -- status transitions back to ACTIVE.
    suspended_by_user_id        UUID                    NULL,
        -- platform_users.id of the staff user who suspended this tenant.
        -- NULL when suspended_at is NULL.
    terminated_at               TIMESTAMPTZ             NULL,
        -- Set when status transitions to TERMINATED. NULL otherwise.
    terminated_by_user_id       UUID                    NULL,
        -- platform_users.id of the staff user who terminated this tenant.
        -- NULL when terminated_at is NULL.

    -- ---------- Constraints ----------

    CONSTRAINT pk_tenants
        PRIMARY KEY (id),

    CONSTRAINT ck_tenants_name_length
        CHECK (LENGTH(name) BETWEEN 1 AND 200),

    CONSTRAINT ck_tenants_display_code_format
        CHECK (
            display_code IS NULL
            OR display_code ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'
        ),

    CONSTRAINT ck_tenants_country_format
        CHECK (
            country IS NULL
            OR (
                LENGTH(country) BETWEEN 2 AND 100
                AND country ~ '[A-Za-z]'
                AND country !~ '^[[:space:]]*$'
            )
        ),

    CONSTRAINT ck_tenants_primary_contact_name_length
        CHECK (
            primary_contact_name IS NULL
            OR LENGTH(primary_contact_name) BETWEEN 1 AND 200
        ),

    CONSTRAINT ck_tenants_contact_email_format
        CHECK (
            contact_email IS NULL
            OR contact_email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'
        ),

    CONSTRAINT ck_tenants_contact_email_lowercase
        CHECK (
            contact_email IS NULL
            OR contact_email = LOWER(contact_email)
        ),

    CONSTRAINT ck_tenants_monthly_revenue_nonnegative
        CHECK (monthly_revenue_usd IS NULL OR monthly_revenue_usd >= 0),

    CONSTRAINT ck_tenants_monthly_revenue_as_of_consistency
        CHECK (
            (monthly_revenue_usd IS NULL AND monthly_revenue_as_of_date IS NULL)
            OR
            (monthly_revenue_usd IS NOT NULL AND monthly_revenue_as_of_date IS NOT NULL)
        ),

    CONSTRAINT ck_tenants_number_of_stores_nonnegative
        CHECK (number_of_stores IS NULL OR number_of_stores >= 0),

    CONSTRAINT ck_tenants_number_of_stores_as_of_consistency
        CHECK (
            (number_of_stores IS NULL AND number_of_stores_as_of_date IS NULL)
            OR
            (number_of_stores IS NOT NULL AND number_of_stores_as_of_date IS NOT NULL)
        ),

    CONSTRAINT ck_tenants_suspended_consistency
        CHECK (
            (status = 'SUSPENDED'
                AND suspended_at IS NOT NULL
                AND suspended_by_user_id IS NOT NULL)
            OR
            (status != 'SUSPENDED'
                AND suspended_at IS NULL
                AND suspended_by_user_id IS NULL)
        ),

    CONSTRAINT ck_tenants_terminated_consistency
        CHECK (
            (status = 'TERMINATED'
                AND terminated_at IS NOT NULL
                AND terminated_by_user_id IS NOT NULL)
            OR
            (status != 'TERMINATED'
                AND terminated_at IS NULL
                AND terminated_by_user_id IS NULL)
        ),

    -- ---------- Audit FKs to platform_users ----------
    -- All tenant lifecycle actions are taken by Ithina staff (FN-AB-03).
    -- Single FK to platform_users; no actor type discrimination needed.

    CONSTRAINT fk_tenants_created_by_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_tenants_updated_by_user
        FOREIGN KEY (updated_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_tenants_suspended_by_user
        FOREIGN KEY (suspended_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_tenants_terminated_by_user
        FOREIGN KEY (terminated_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT
);


-- ----------------------------------------------------------------------------
-- Indexes beyond PK (which auto-creates an index)
-- ----------------------------------------------------------------------------

-- Case-insensitive UNIQUE on display_code. Replaces a plain UNIQUE
-- constraint so 'Acme-Retail' and 'acme-retail' cannot both be inserted.
CREATE UNIQUE INDEX uq_tenants_display_code_lower
    ON tenants (LOWER(display_code))
    WHERE display_code IS NOT NULL;

-- Operational scans by lifecycle state (e.g. "all active tenants").
-- Tenants table is small; this is primarily for admin and reporting tooling.
CREATE INDEX ix_tenants_status
    ON tenants (status);

-- Region filtering for cross-region staff aggregation queries that may run
-- against a regional instance and join with metadata. Small table; index is
-- cheap and matches a known access pattern.
CREATE INDEX ix_tenants_region
    ON tenants (region);


-- ----------------------------------------------------------------------------
-- Trigger: keep updated_at fresh on every UPDATE
-- ----------------------------------------------------------------------------

CREATE TRIGGER tg_tenants_set_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- Row-Level Security
--
-- tenants is the boundary itself, not a tenant-owned entity. RLS still
-- applies because tenant users must only see their own tenant row, while
-- Ithina staff need to see all rows. The policy compares tenants.id to the
-- session-scoped app.tenant_id. Staff connections use a role with
-- BYPASSRLS, which skips the policy entirely. FORCE RLS prevents the table
-- owner from also bypassing.
-- ----------------------------------------------------------------------------

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;

CREATE POLICY tenants_self_access
    ON tenants
    FOR ALL
    USING (id = current_setting('app.tenant_id', TRUE)::uuid)
    WITH CHECK (id = current_setting('app.tenant_id', TRUE)::uuid);

-- Note: current_setting('app.tenant_id', TRUE) returns NULL when the
-- session variable is unset. NULL = NULL evaluates to UNKNOWN, so the
-- policy filters out all rows by default. A handler that forgets to call
-- SET LOCAL gets zero rows, not all rows. Default-deny by construction.
"""

SQL_TENANT_USERS = r"""
-- ============================================================================
-- Ithina: tenant_users
-- Postgres SQL DDL
-- Version: v1
--
-- Customer-side users. Employees of a tenant organisation (Owner,
-- Pricing Manager, Store Manager, Associate, etc.) who use Ithina
-- modules within their tenant's scope.
--
-- Physically separated from platform_users (Pattern 2): different table,
-- different DB role, RLS enforced. Cross-tenant data leak is structurally
-- prevented because tenant_users has no rows for staff and platform_users
-- has no tenant_id.
--
-- Key properties:
--   * tenant_id NOT NULL. Every row belongs to one tenant.
--   * RLS enabled with FORCE; tenant-scoped policy on app.tenant_id.
--   * Auth0 is the credential authority. auth0_sub maps Auth0 identity
--     to local row. NULL during INVITED state, populated on first login.
--   * Email unique per tenant: UNIQUE(tenant_id, email). Same human
--     across tenants (e.g., Maria at Buc-ee's AND at Zabka) has two
--     separate rows. Future evolution to one-row-per-human noted in
--     forward-notes (FN-AB-XX).
--   * Audit columns use pattern (b): *_user_id UUID + *_user_type enum,
--     no FK. created_by/updated_by can be a platform_user (Phase 1:
--     Ithina staff invites tenant users) or eventually a tenant_user
--     (Phase 2: tenant admins invite their own users). The schema
--     supports both from day one; app layer controls which is permitted
--     in which phase.
--
-- Lifecycle:
--   * INVITED  -- row created by an authorised actor; invite email sent;
--                 Auth0 signup not yet completed; auth0_sub NULL.
--   * ACTIVE   -- invite accepted; auth0_sub populated; can log in.
--   * SUSPENDED-- temporarily disabled; auth0_sub retained; cannot log in.
--   No INACTIVE / no soft-delete in v1. Hard delete blocked by FK
--   RESTRICT on referencing tables (audit history must remain resolvable).
--
-- Dependencies (must exist before this file runs):
--   * tenants (id)
--   * Shared utilities migration providing:
--       - function set_updated_at_timestamp()
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enum types used by tenant_users
-- ----------------------------------------------------------------------------

CREATE TYPE tenant_user_status_enum AS ENUM (
    'INVITED',
    'ACTIVE',
    'SUSPENDED'
);


-- ----------------------------------------------------------------------------
-- tenant_users
-- ----------------------------------------------------------------------------

CREATE TABLE tenant_users (

    -- ---------- Surrogate primary key ----------
    id                          UUID                            NOT NULL DEFAULT uuidv7(),

    -- ---------- Ownership ----------
    tenant_id                   UUID                            NOT NULL,

    -- ---------- External identity ----------
    auth0_sub                   TEXT                            NULL,
        -- Auth0 'sub' claim. Populated when the user completes Auth0
        -- signup. NULL only while status = INVITED. Unique per tenant
        -- when set; a single human (same Auth0 sub) may appear in
        -- multiple tenants as separate rows.

    -- ---------- Identity ----------
    email                       TEXT                            NOT NULL,
        -- Stored lowercased; CHECK enforces no uppercase characters.
        -- Unique per tenant.
    full_name                   TEXT                            NOT NULL,

    -- ---------- Lifecycle ----------
    status                      tenant_user_status_enum         NOT NULL DEFAULT 'INVITED',

    -- ---------- Invitation ----------
    invited_at                  TIMESTAMPTZ                     NULL,
    invitation_accepted_at      TIMESTAMPTZ                     NULL,

    -- ---------- Suspension (pattern b) ----------
    suspended_at                TIMESTAMPTZ                     NULL,
    suspended_by_user_id        UUID                            NULL,
    suspended_by_user_type      actor_user_type_enum            NULL,

    -- ---------- Audit (pattern b) ----------
    created_at                  TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    created_by_user_id          UUID                            NULL,
    created_by_user_type        actor_user_type_enum            NULL,
    updated_at                  TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    updated_by_user_id          UUID                            NULL,
    updated_by_user_type        actor_user_type_enum            NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_tenant_users
        PRIMARY KEY (id),

    CONSTRAINT fk_tenant_users_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES tenants (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT ck_tenant_users_email_format
        CHECK (
            email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'
        ),

    CONSTRAINT ck_tenant_users_email_lowercase
        CHECK (email = LOWER(email)),

    CONSTRAINT ck_tenant_users_full_name_length
        CHECK (LENGTH(full_name) BETWEEN 1 AND 200),

    -- auth0_sub may be NULL only while status = INVITED.
    CONSTRAINT ck_tenant_users_auth0_sub_consistency
        CHECK (
            (status = 'INVITED' AND auth0_sub IS NULL)
            OR
            (status IN ('ACTIVE', 'SUSPENDED') AND auth0_sub IS NOT NULL)
        ),

    -- invitation_accepted_at must be set once status leaves INVITED.
    CONSTRAINT ck_tenant_users_invitation_accepted_consistency
        CHECK (
            (status = 'INVITED'
                AND invitation_accepted_at IS NULL)
            OR
            (status IN ('ACTIVE', 'SUSPENDED')
                AND invitation_accepted_at IS NOT NULL)
        ),

    -- Pattern (b) requires id and type to be set together (or both NULL).
    CONSTRAINT ck_tenant_users_suspended_actor_pair
        CHECK (
            (suspended_by_user_id IS NULL AND suspended_by_user_type IS NULL)
            OR
            (suspended_by_user_id IS NOT NULL AND suspended_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_tenant_users_created_by_actor_pair
        CHECK (
            (created_by_user_id IS NULL AND created_by_user_type IS NULL)
            OR
            (created_by_user_id IS NOT NULL AND created_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_tenant_users_updated_by_actor_pair
        CHECK (
            (updated_by_user_id IS NULL AND updated_by_user_type IS NULL)
            OR
            (updated_by_user_id IS NOT NULL AND updated_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_tenant_users_suspended_consistency
        CHECK (
            (status = 'SUSPENDED'
                AND suspended_at IS NOT NULL
                AND suspended_by_user_id IS NOT NULL
                AND suspended_by_user_type IS NOT NULL)
            OR
            (status IN ('INVITED', 'ACTIVE')
                AND suspended_at IS NULL
                AND suspended_by_user_id IS NULL
                AND suspended_by_user_type IS NULL)
        )
);


-- ----------------------------------------------------------------------------
-- Indexes beyond PK (which auto-creates an index)
-- ----------------------------------------------------------------------------

-- Email unique per tenant.
CREATE UNIQUE INDEX uq_tenant_users_tenant_email
    ON tenant_users (tenant_id, email);

-- auth0_sub unique per tenant when set; multiple NULLs allowed (multiple
-- INVITED users coexist before any has signed up).
CREATE UNIQUE INDEX uq_tenant_users_tenant_auth0_sub
    ON tenant_users (tenant_id, auth0_sub)
    WHERE auth0_sub IS NOT NULL;

-- Tenant-scoped queries (RLS path).
CREATE INDEX ix_tenant_users_tenant
    ON tenant_users (tenant_id);

-- Tenant + status scans ("all active users for this tenant").
CREATE INDEX ix_tenant_users_tenant_status
    ON tenant_users (tenant_id, status);


-- ----------------------------------------------------------------------------
-- Trigger: keep updated_at fresh on every UPDATE
-- ----------------------------------------------------------------------------

CREATE TRIGGER tg_tenant_users_set_updated_at
    BEFORE UPDATE ON tenant_users
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- Row-Level Security
--
-- tenant_users is tenant-owned. The policy filters rows where tenant_id
-- matches the session-scoped app.tenant_id. Staff connections with
-- BYPASSRLS skip the policy entirely. FORCE RLS prevents the table
-- owner from also bypassing.
-- ----------------------------------------------------------------------------

ALTER TABLE tenant_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_users FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_users_tenant_isolation
    ON tenant_users
    FOR ALL
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- Note: current_setting('app.tenant_id', TRUE) returns NULL when the
-- session variable is unset. tenant_id = NULL evaluates to UNKNOWN, so
-- the policy filters out all rows by default.


-- ============================================================================
-- Application-layer invariants
--
-- Captured here for reference. Not enforced by DDL in v1; document in
-- CLAUDE.md and enforce in service code.
--
-- AI-TU-01: Status transition INVITED -> SUSPENDED is not allowed.
--           Cancel the invite instead (see AI-TU-02). The DB CHECK
--           constraint ck_tenant_users_auth0_sub_consistency rejects
--           SUSPENDED with auth0_sub IS NULL, so this transition fails
--           at the DB layer for an unaccepted invite. App layer should
--           reject it explicitly with a clear error.
--
-- AI-TU-02: Hard-delete of a tenant_user is permitted ONLY when
--           status = INVITED and the user has never been ACTIVE
--           (invitation_accepted_at IS NULL). Once a user has been
--           ACTIVE, they cannot be deleted; use SUSPENDED instead.
--           Documented exception to the project-wide "no hard delete
--           of users" rule, scoped to cancelled or never-accepted
--           invites only.
--
-- AI-TU-03: Pattern (b) actor columns require app-layer validation:
--           when inserting/updating any *_by_user_id + *_by_user_type
--           pair, app must verify the UUID exists in the table indicated
--           by user_type (platform_users for PLATFORM, tenant_users for
--           TENANT). The DB does not enforce this referential link.
--           See FN-AB-09 for migration path to pattern (a) if drift
--           becomes an issue.
--
-- AI-TU-04: Phase 1: only platform_users may be the actor in
--           created_by_user_id, updated_by_user_id, and
--           suspended_by_user_id (i.e., user_type must be 'PLATFORM').
--           Phase 2: tenant admins gain user-management permission;
--           user_type may then be 'TENANT'. The schema supports both
--           from day one; the constraint is enforced in the service
--           layer based on the active phase.
--
-- AI-TU-05: Self-suspension (suspended_by_user_id = id when
--           suspended_by_user_type = 'TENANT' and id matches the row's
--           own id) is structurally allowed but should be rejected at
--           the app layer.
--
-- AI-TU-06: Email values must be lowercased before insert. The
--           ck_tenant_users_email_lowercase CHECK rejects mixed-case
--           values.
--
-- AI-TU-07: Future evolution -- one-row-per-human across tenants
--           (currently one row per tenant per human). Migration path
--           involves making auth0_sub globally unique and decoupling
--           tenant_id to a separate membership table. Parked.
--
-- AI-TU-08: App must set updated_by_user_id and updated_by_user_type
--           on every UPDATE. The trigger refreshes updated_at
--           automatically; the actor pair is app's responsibility.
--           Without this, the actor pair retains its previous value
--           and audit fidelity is lost for that update.
-- ============================================================================
"""

SQL_ORG_NODES = r"""
-- ============================================================================
-- Ithina: org_nodes
-- Postgres SQL DDL
-- Version: v2
--
-- This file defines org_nodes, the tree-position table for the
-- organisation hierarchy. Every entity that participates in the tenant's
-- org tree (tenants, stores, and intermediate groupings like BU / HQ /
-- Region / Department) has a corresponding row in org_nodes.
--
-- Changes from v1:
--   * Audit columns adopt pattern (b): each actor slot is a UUID +
--     actor_user_type_enum pair, no FK. created_by, updated_by, and
--     archived_by columns renamed and split:
--       - created_by  -> created_by_user_id  + created_by_user_type
--       - updated_by  -> updated_by_user_id  + updated_by_user_type
--       - archived_by -> archived_by_user_id + archived_by_user_type
--     The actor may be a platform_user (Phase 1) or a tenant_user
--     (post-launch tree edits).
--   * ck_org_nodes_archived_consistency updated to enforce both columns
--     of the archived_by pair are set together with archived_at.
--   * New CHECKs ck_org_nodes_*_actor_pair enforce actor pair shape.
--   * No FK on actor columns. App layer validates UUID exists in the
--     table indicated by user_type (FN-AB-09 tech debt).
--
-- Purpose (retained from v1):
--   * Drive the org-tree UI.
--   * Anchor permissions: a permission attached to a node cascades to
--     descendants via the tree.
--   * Uniform model for varied tenant hierarchies (tenants may skip
--     levels: SmartStore Demo has Tenant > HQ > Store, no Region).
--
-- ltree extension (retained):
--   * Materialised path column for descendant / ancestor queries via
--     <@ and @> operators. Cloud SQL allows ltree.
--   * Path maintained by app layer on insert and on tree mutations.
--
-- Dependencies (must exist before this file runs):
--   * tenants (id)
--   * Extension ltree.
--   * Shared utilities migration providing:
--       - function set_updated_at_timestamp()
--       - enum actor_user_type_enum (defined in tenant_users v1)
--
-- Sync invariants enforced outside this DDL (app layer for v1):
--   * Every tenants row has exactly one org_nodes row with node_type=TENANT
--     and parent_id=NULL. Created together at tenant onboarding.
--   * Every stores row has exactly one org_nodes row with node_type=STORE.
--     Linked via stores.org_node_id (added in stores v4).
--   * The parent_id of any node references a node within the same
--     tenant_id (enforced by composite FK).
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Required extension
-- ----------------------------------------------------------------------------



-- ----------------------------------------------------------------------------
-- Enum types used by org_nodes
-- ----------------------------------------------------------------------------

CREATE TYPE org_node_type_enum AS ENUM (
    'TENANT',
    'BUSINESS_UNIT',
    'HQ',
    'COUNTRY',
    'REGION',
    'STORE',
    'DEPARTMENT'
        -- DEPARTMENT is in-scope for v1 as a tree node. Linkage to
        -- product_catalogues (department-to-category) is parked; revisit
        -- when SKUs or sales need department-level grouping.
);

CREATE TYPE org_node_status_enum AS ENUM (
    'ACTIVE',
    'INACTIVE',
    'ARCHIVED'
);


-- ----------------------------------------------------------------------------
-- org_nodes
--
-- A tree-position record. Holds where a node sits in the tenant's
-- organisation hierarchy and its display metadata. Business attributes
-- (currency, tax_treatment, address, contact_email, etc.) live on the
-- entity tables (tenants, stores), not here.
-- ----------------------------------------------------------------------------

CREATE TABLE org_nodes (

    -- ---------- Surrogate primary key ----------
    id                  UUID                    NOT NULL DEFAULT uuidv7(),

    -- ---------- Ownership ----------
    tenant_id           UUID                    NOT NULL,
        -- Every node belongs to exactly one tenant. For TENANT-type nodes
        -- this column matches the entity itself (tenant_id = tenants.id
        -- of the corresponding tenant row).

    -- ---------- Tree position ----------
    parent_id           UUID                    NULL,
        -- NULL only for TENANT-type nodes (tree root for the tenant).
        -- All other nodes must reference a parent within the same tenant.
        -- v1 does not enforce parent-child type pairs; app layer applies
        -- shape rules.

    path                LTREE                   NOT NULL,
        -- Materialised path from the tenant root to this node, using
        -- node code labels separated by dots, lowercased.
        -- Example: 'buc.hq.tx.s101.deli'.
        -- Maintained by app layer on insert and on tree mutations.
        -- Used for descendant / ancestor queries via ltree operators.

    -- ---------- Identity ----------
    node_type           org_node_type_enum      NOT NULL,
    name                TEXT                    NOT NULL,
        -- Display name (e.g., 'Texas Region', 'Buc-ee's #101 New Braunfels').
    code                TEXT                    NOT NULL,
        -- Short code used in UI badges and as the path label component
        -- (e.g., 'TX', 'TX-101', 'BU-HQ'). Unique within tenant.
        -- Stored case-insensitively; uniqueness enforced via expression
        -- index below.

    -- ---------- Lifecycle ----------
    status              org_node_status_enum    NOT NULL DEFAULT 'ACTIVE',
        -- ACTIVE: node is live in the tree.
        -- INACTIVE: hidden from default UI but retained.
        -- ARCHIVED: soft-deleted, kept for audit and historical queries.

    -- ---------- Audit (pattern b: id + type, no FK) ----------
    created_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    created_by_user_id      UUID                    NULL,
    created_by_user_type    actor_user_type_enum    NULL,
        -- platform_users.id (PLATFORM) or tenant_users.id (TENANT) of
        -- the actor who created this node. App layer validates UUID
        -- exists in the table indicated by user_type. NULL only when
        -- created by a system process (e.g., auto-creation on tenant
        -- insert).
    updated_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    updated_by_user_id      UUID                    NULL,
    updated_by_user_type    actor_user_type_enum    NULL,
    archived_at             TIMESTAMPTZ             NULL,
        -- Set when status transitions to ARCHIVED.
    archived_by_user_id     UUID                    NULL,
    archived_by_user_type   actor_user_type_enum    NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_org_nodes
        PRIMARY KEY (id),

    -- Composite FK: parent must be in the same tenant. Requires the
    -- (tenant_id, id) compound to be referencable, hence the unique
    -- constraint below.
    CONSTRAINT fk_org_nodes_parent_same_tenant
        FOREIGN KEY (tenant_id, parent_id)
        REFERENCES org_nodes (tenant_id, id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_org_nodes_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES tenants (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- Required by the composite FK above.
    CONSTRAINT uq_org_nodes_tenant_id
        UNIQUE (tenant_id, id),

    CONSTRAINT ck_org_nodes_name_length
        CHECK (LENGTH(name) BETWEEN 1 AND 200),

    CONSTRAINT ck_org_nodes_code_format
        CHECK (
            code ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,62}[A-Za-z0-9]$'
            OR LENGTH(code) = 1
        ),

    -- TENANT nodes have no parent; all other nodes must have one.
    -- Free-form means we do not constrain WHICH parent type, only that
    -- non-tenant nodes have one.
    CONSTRAINT ck_org_nodes_root_parent_consistency
        CHECK (
            (node_type = 'TENANT' AND parent_id IS NULL)
            OR
            (node_type != 'TENANT' AND parent_id IS NOT NULL)
        ),

    CONSTRAINT ck_org_nodes_archived_consistency
        CHECK (
            (status = 'ARCHIVED'
                AND archived_at IS NOT NULL
                AND archived_by_user_id IS NOT NULL
                AND archived_by_user_type IS NOT NULL)
            OR
            (status != 'ARCHIVED'
                AND archived_at IS NULL
                AND archived_by_user_id IS NULL
                AND archived_by_user_type IS NULL)
        ),

    -- Pattern (b): each actor pair must be both NULL or both NOT NULL.
    CONSTRAINT ck_org_nodes_created_by_actor_pair
        CHECK (
            (created_by_user_id IS NULL AND created_by_user_type IS NULL)
            OR
            (created_by_user_id IS NOT NULL AND created_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_org_nodes_updated_by_actor_pair
        CHECK (
            (updated_by_user_id IS NULL AND updated_by_user_type IS NULL)
            OR
            (updated_by_user_id IS NOT NULL AND updated_by_user_type IS NOT NULL)
        )
);


-- ----------------------------------------------------------------------------
-- Indexes beyond PK and the unique constraints (which auto-create indexes)
-- ----------------------------------------------------------------------------

-- Case-insensitive UNIQUE on code within a tenant. 'TX-101' and 'tx-101'
-- cannot both exist in the same tenant.
CREATE UNIQUE INDEX uq_org_nodes_tenant_code_lower
    ON org_nodes (tenant_id, LOWER(code));

-- Tenant-scoped queries (every multi-tenant access path filters by tenant_id).
CREATE INDEX ix_org_nodes_tenant
    ON org_nodes (tenant_id);

-- Direct-children lookup ("show me the kids of node X").
CREATE INDEX ix_org_nodes_parent
    ON org_nodes (parent_id);

-- Filter by node type within a tenant ("all stores in this tenant tree",
-- "all regions"). Useful for the tree-rendering query.
CREATE INDEX ix_org_nodes_tenant_type
    ON org_nodes (tenant_id, node_type);

-- ltree GIST index for descendant / ancestor queries (path <@ ?, ? @> path).
-- This is the load-bearing index for tree walks.
CREATE INDEX ix_org_nodes_path_gist
    ON org_nodes USING GIST (path);


-- ----------------------------------------------------------------------------
-- Trigger: keep updated_at fresh on every UPDATE
--
-- Uses the shared set_updated_at_timestamp() function defined in the
-- shared-utilities migration.
-- ----------------------------------------------------------------------------

CREATE TRIGGER tg_org_nodes_set_updated_at
    BEFORE UPDATE ON org_nodes
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- Row-Level Security
--
-- org_nodes is tenant-owned. The policy filters rows where tenant_id
-- matches the session-scoped app.tenant_id. Staff connections with
-- BYPASSRLS skip the policy entirely. FORCE RLS prevents the table owner
-- from also bypassing.
-- ----------------------------------------------------------------------------

ALTER TABLE org_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_nodes FORCE ROW LEVEL SECURITY;

CREATE POLICY org_nodes_tenant_isolation
    ON org_nodes
    FOR ALL
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- Note: current_setting('app.tenant_id', TRUE) returns NULL when the
-- session variable is unset. Default-deny by construction.
"""

SQL_STORES = r"""
-- ============================================================================
-- Ithina: stores
-- Postgres SQL DDL
-- Version: v5
--
-- This file defines stores. Enum types and the shared updated_at trigger
-- function are defined in a separate shared-utilities migration that runs
-- before this file (see notes below).
--
-- A store is a physical retail location belonging to a tenant. Stores hold
-- the operational and contextual attributes that scope SKU activity at that
-- location: country, timezone, currency, and tax treatment.
--
-- Dependencies (must exist before this file runs):
--   * tenants (id)
--   * org_nodes (id, tenant_id)
--   * Shared utilities migration providing:
--       - function set_updated_at_timestamp()
--       - enum tax_treatment_enum
--       - enum actor_user_type_enum (defined in tenant_users v1)
--
-- Changes from v4:
--   * Audit columns adopt pattern (b): each actor slot is a UUID +
--     actor_user_type_enum pair, no FK. created_by, updated_by, and
--     closed_by columns renamed and split:
--       - created_by  -> created_by_user_id  + created_by_user_type
--       - updated_by  -> updated_by_user_id  + updated_by_user_type
--       - closed_by   -> closed_by_user_id   + closed_by_user_type
--     The actor may be a platform_user (Phase 1: staff onboards stores)
--     or a tenant_user (post-launch edits by tenant admin/manager).
--   * ck_stores_closed_consistency updated to enforce both columns of
--     the closed_by pair are set together with closed_at.
--   * New CHECKs ck_stores_*_actor_pair enforce that *_user_id and
--     *_user_type are both NULL or both non-NULL.
--   * No FK on actor columns. App layer validates UUID exists in the
--     table indicated by user_type (FN-AB-09 tech debt).
--
-- Changes from v3 (retained):
--   * org_node_id column linking to org_nodes(id) via composite FK.
--   * 1:1 link enforced by uq_stores_org_node_id partial UNIQUE.
--
-- Changes from v2 (retained):
--   * status default 'ACTIVE'.
--
-- Changes from v1 (retained):
--   * store_code uniqueness is case-insensitive within a tenant.
--
-- Changes from v0 (retained):
--   * country: TEXT.
--   * BEFORE UPDATE trigger.
--   * RLS enabled with FORCE.
--   * FK to tenants RESTRICT.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enum types used by stores
--
-- store_status_enum is local to stores. tax_treatment_enum is shared and
-- defined in the shared-utilities migration; not redefined here.
-- ----------------------------------------------------------------------------

CREATE TYPE store_status_enum AS ENUM (
    'OPENING',
    'ACTIVE',
    'INACTIVE',
    'CLOSED'
);


-- ----------------------------------------------------------------------------
-- stores
--
-- A physical retail location belonging to a tenant. Stocks SKUs, hosts sale
-- and inventory events, and is the unit at which operational decisions are
-- scoped. Carries the country, timezone, currency, and tax treatment that
-- scope every SKU instance at the store.
-- ----------------------------------------------------------------------------

CREATE TABLE stores (

    -- ---------- Surrogate primary key ----------
    id                  UUID                NOT NULL DEFAULT uuidv7(),

    -- ---------- Ownership ----------
    tenant_id           UUID                NOT NULL,

    -- ---------- Tree position ----------
    org_node_id         UUID                NULL,
        -- FK to org_nodes(id). Links this store to its position in the
        -- tenant's organisation tree. The matching org_nodes row has
        -- node_type=STORE. Nullable in v4 to support store creation
        -- before tree placement; promote to NOT NULL once onboarding
        -- flow is stable. 1:1 with org_nodes (enforced by UNIQUE below).

    -- ---------- Identity ----------
    name                TEXT                NOT NULL,
    store_code          TEXT                NULL,
        -- Tenant-scoped short identifier for the store (e.g. 'MUM-AND-001').
        -- Optional at creation; expected to be set during onboarding.
        -- Unique within a tenant, not globally.

    -- ---------- Geography ----------
    country             TEXT                NOT NULL,
        -- Country of the store. Accepts a full country name (e.g. 'Poland',
        -- 'India') or an abbreviation (e.g. 'USA', 'UK'). No fixed format
        -- enforced at the DB layer beyond whitespace/digit-only rejection;
        -- canonicalisation and lookup is the application's responsibility.
        -- Aligned with tenants.country format (v2).
    timezone            TEXT                NOT NULL,
        -- IANA timezone identifier (e.g. 'Asia/Kolkata', 'America/New_York').
        -- Validation expected at the application layer.
    address             TEXT                NULL,
        -- Free-form single-field address for v1. May be normalised into
        -- structured fields (street, city, postal_code, region) later if
        -- geographic queries demand it.
    latitude            NUMERIC(9, 6)       NULL,
    longitude           NUMERIC(9, 6)       NULL,

    -- ---------- Operational context ----------
    currency            CHAR(3)             NOT NULL,
        -- ISO 4217 currency code (e.g. 'USD', 'GBP', 'INR').
        -- All SKU prices at this store are denominated in this currency.
    tax_treatment       tax_treatment_enum  NOT NULL,
        -- EXCLUSIVE: prices shown without tax (US convention).
        -- INCLUSIVE: prices shown with tax (EU, UK, IN convention).

    -- ---------- Lifecycle ----------
    status              store_status_enum   NOT NULL DEFAULT 'ACTIVE',

    -- ---------- Audit (pattern b: id + type, no FK) ----------
    created_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    created_by_user_id      UUID                    NULL,
    created_by_user_type    actor_user_type_enum    NULL,
        -- platform_users.id (PLATFORM) or tenant_users.id (TENANT) of
        -- the actor who created this store. App layer validates UUID
        -- exists in the table indicated by user_type. NULL only when
        -- the row was created by a system process.
    updated_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    updated_by_user_id      UUID                    NULL,
    updated_by_user_type    actor_user_type_enum    NULL,
    closed_at               TIMESTAMPTZ             NULL,
        -- Set when status transitions to CLOSED. NULL otherwise.
    closed_by_user_id       UUID                    NULL,
    closed_by_user_type     actor_user_type_enum    NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_stores
        PRIMARY KEY (id),

    CONSTRAINT fk_stores_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES tenants (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_stores_org_node_same_tenant
        FOREIGN KEY (tenant_id, org_node_id)
        REFERENCES org_nodes (tenant_id, id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,
        -- Composite FK ensures the linked org_node belongs to the same
        -- tenant as the store. Relies on org_nodes' UNIQUE(tenant_id, id).

    CONSTRAINT ck_stores_name_length
        CHECK (LENGTH(name) BETWEEN 1 AND 200),

    CONSTRAINT ck_stores_country_format
        CHECK (
            LENGTH(country) BETWEEN 2 AND 100
            AND country ~ '[A-Za-z]'
            AND country !~ '^[[:space:]]*$'
        ),

    CONSTRAINT ck_stores_currency_format
        CHECK (currency ~ '^[A-Z]{3}$'),

    CONSTRAINT ck_stores_latitude_range
        CHECK (latitude IS NULL OR (latitude >= -90 AND latitude <= 90)),

    CONSTRAINT ck_stores_longitude_range
        CHECK (longitude IS NULL OR (longitude >= -180 AND longitude <= 180)),

    CONSTRAINT ck_stores_closed_consistency
        CHECK (
            (status = 'CLOSED'
                AND closed_at IS NOT NULL
                AND closed_by_user_id IS NOT NULL
                AND closed_by_user_type IS NOT NULL)
            OR
            (status != 'CLOSED'
                AND closed_at IS NULL
                AND closed_by_user_id IS NULL
                AND closed_by_user_type IS NULL)
        ),

    -- Pattern (b): each actor pair must be both NULL or both NOT NULL.
    CONSTRAINT ck_stores_created_by_actor_pair
        CHECK (
            (created_by_user_id IS NULL AND created_by_user_type IS NULL)
            OR
            (created_by_user_id IS NOT NULL AND created_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_stores_updated_by_actor_pair
        CHECK (
            (updated_by_user_id IS NULL AND updated_by_user_type IS NULL)
            OR
            (updated_by_user_id IS NOT NULL AND updated_by_user_type IS NOT NULL)
        )
);


-- ----------------------------------------------------------------------------
-- Indexes beyond PK (which auto-creates an index)
-- ----------------------------------------------------------------------------

-- 1:1 link between stores and org_nodes. A store has at most one tree
-- position; an org_node row of type STORE corresponds to at most one store.
-- Partial index allows multiple NULLs (stores not yet placed in the tree).
CREATE UNIQUE INDEX uq_stores_org_node_id
    ON stores (org_node_id)
    WHERE org_node_id IS NOT NULL;

-- Case-insensitive UNIQUE on store_code within a tenant. Partial index
-- excludes NULL store_codes so multiple stores can coexist in the
-- "code not yet assigned" onboarding state. Replaces the case-sensitive
-- UNIQUE(tenant_id, store_code) constraint from v1.
CREATE UNIQUE INDEX uq_stores_tenant_store_code_lower
    ON stores (tenant_id, LOWER(store_code))
    WHERE store_code IS NOT NULL;

-- Tenant-scoped queries (every multi-tenant access path filters by tenant_id).
CREATE INDEX ix_stores_tenant
    ON stores (tenant_id);

-- Tenant + status scans ("list all active stores for this tenant").
CREATE INDEX ix_stores_tenant_status
    ON stores (tenant_id, status);


-- ----------------------------------------------------------------------------
-- Trigger: keep updated_at fresh on every UPDATE
--
-- Uses the shared set_updated_at_timestamp() function defined in the
-- shared-utilities migration.
-- ----------------------------------------------------------------------------

CREATE TRIGGER tg_stores_set_updated_at
    BEFORE UPDATE ON stores
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- Row-Level Security
--
-- stores is a tenant-owned entity. The policy filters rows where
-- tenant_id matches the session-scoped app.tenant_id. Staff connections
-- with BYPASSRLS skip the policy entirely. FORCE RLS prevents the table
-- owner from also bypassing.
-- ----------------------------------------------------------------------------

ALTER TABLE stores ENABLE ROW LEVEL SECURITY;
ALTER TABLE stores FORCE ROW LEVEL SECURITY;

CREATE POLICY stores_tenant_isolation
    ON stores
    FOR ALL
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- Note: current_setting('app.tenant_id', TRUE) returns NULL when the
-- session variable is unset. tenant_id = NULL evaluates to UNKNOWN, so
-- the policy filters out all rows by default. A handler that forgets to
-- call SET LOCAL gets zero rows, not all rows. Default-deny by
-- construction.
"""

SQL_RBAC = r"""
-- ============================================================================
-- Ithina: RBAC (roles, permissions, role_permissions, user_role_assignments)
-- Postgres SQL DDL
-- Version: v2
--
-- This file defines the RBAC entities for the Ithina platform.
--
-- Model summary:
--   * Permission shape: Module + Resource + Action + Scope. Stored as a
--     composite row in the `permissions` table (the canonical catalogue
--     of valid permission tuples).
--   * Role: a named bundle of permissions. Two role universes:
--       - PLATFORM roles: Ithina staff. Always Global-scope-eligible.
--       - TENANT roles: customer-side. Cap is Tenant-scope.
--     All roles are platform-defined in v1; tenant users cannot create
--     roles.
--   * role_permissions: many-to-many junction.
--   * user_role_assignments: many-to-many between users and roles, with
--     an org_node anchor (for TENANT-audience). A user can hold multiple
--     roles, each at its own org_node. The org_node is the cascade root
--     for that assignment's permissions; downward cascade via ltree path.
--
-- Changes from v1:
--   * Pattern 2 user split: physical separation of platform_users and
--     tenant_users tables. user_role_assignments must reference exactly
--     one of the two:
--       - PLATFORM-audience: platform_user_id NOT NULL, tenant_user_id NULL,
--         tenant_id NULL, org_node_id NULL.
--       - TENANT-audience:   tenant_user_id NOT NULL, platform_user_id NULL,
--         tenant_id NOT NULL, org_node_id NOT NULL.
--     Replaces the single user_id FK that v1 declared.
--   * Two FK constraints (one to each user table), each fires only when
--     its column is non-NULL.
--   * XOR CHECK enforces exactly one user reference per row.
--   * granted_by, revoked_by adopt pattern (b): UUID + actor_user_type
--     enum, no FK.
--
-- Key model decisions (retained):
--   * Assignment-vs-permission scope rule (AI-RBAC-03): app layer
--     validates assignment org_node level >= widest permission scope
--     in role.
--   * tenant_id denormalised on user_role_assignments for RLS efficiency.
--   * Roles, permissions, role_permissions: platform-global, no RLS,
--     API-layer authorisation.
--   * user_role_assignments: RLS on tenant_id; PLATFORM rows have
--     tenant_id=NULL and visible only to staff queries (BYPASSRLS).
--
-- Out of scope for v1 (retained):
--   * Tenant-custom roles (FN-AB-06).
--   * Approval workflow tables.
--   * Manager-approval requirements per permission.
--   * Time-bound assignments.
--   * Role hierarchies.
--   * Per-user permission overrides.
--
-- Dependencies (must exist before this file runs):
--   * tenants (id)
--   * org_nodes (id, tenant_id)
--   * platform_users (id)
--   * tenant_users (id)
--   * Shared utilities migration providing:
--       - function set_updated_at_timestamp()
--       - enum actor_user_type_enum
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enum types used by RBAC
-- ----------------------------------------------------------------------------

CREATE TYPE role_audience_enum AS ENUM (
    'PLATFORM',
        -- Ithina staff role. Eligible for GLOBAL-scope permissions.
    'TENANT'
        -- Customer-side role. Cap is TENANT-scope.
);

CREATE TYPE module_enum AS ENUM (
    'ROOS',
    'GOAL_CONSOLE',
    'PRICING_OS',
    'PERISHABLES_ASSISTANT',
    'PROMOTIONS_ASSISTANT',
    'ADMIN'
);

CREATE TYPE resource_enum AS ENUM (
    -- Pricing OS
    'PRICING_RULES',
    'MARKDOWNS',
    -- Perishables Assistant
    'EXPIRING_ITEMS',
    'WASTE_LOG',
    'DONATION_ROUTING',
    -- Promotions Assistant
    'CAMPAIGNS',
    -- Admin
    'USERS',
    'ROLES',
    'AUDIT_LOG',
    'TENANTS',
    'STORES',
    'ORG_NODES'
        -- Extend by ALTER TYPE as new resources surface.
);

CREATE TYPE action_enum AS ENUM (
    'VIEW',
    'CONFIGURE',
    'EXECUTE',
    'APPROVE',
    'OVERRIDE',
    'AUDIT'
);

CREATE TYPE permission_scope_enum AS ENUM (
    'GLOBAL',
        -- Platform-wide. Only assignable via PLATFORM-audience roles.
    'TENANT',
    'REGION',
    'STORE'
);

CREATE TYPE role_status_enum AS ENUM (
    'ACTIVE',
    'INACTIVE',
    'ARCHIVED'
);

CREATE TYPE user_role_assignment_status_enum AS ENUM (
    'ACTIVE',
    'INACTIVE'
);


-- ----------------------------------------------------------------------------
-- permissions
--
-- Canonical catalogue of every valid (module, resource, action, scope)
-- tuple. New permissions are added by Ithina platform admins via
-- migration or admin API. UI matrix renders directly from this table.
--
-- Platform-global table. No tenant_id. No RLS. Access controlled by
-- API-layer authorisation: only roles with ADMIN.PERMISSIONS.VIEW can
-- read the catalogue (in practice all logged-in users for UI rendering),
-- and only ROLES.CONFIGURE can mutate.
-- ----------------------------------------------------------------------------

CREATE TABLE permissions (

    -- ---------- Surrogate primary key ----------
    id              UUID                    NOT NULL DEFAULT uuidv7(),

    -- ---------- Composite identity ----------
    module          module_enum             NOT NULL,
    resource        resource_enum           NOT NULL,
    action          action_enum             NOT NULL,
    scope           permission_scope_enum   NOT NULL,

    -- ---------- Display ----------
    code            TEXT                    NOT NULL,
        -- Derived stable string for logs and audit references, e.g.
        -- 'PRICING_OS.MARKDOWNS.APPROVE.REGION'. App layer or trigger
        -- maintains; in v1 set on insert by app layer.
    description     TEXT                    NULL,

    -- ---------- Audit ----------
    created_at      TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ             NOT NULL DEFAULT NOW(),

    -- ---------- Constraints ----------

    CONSTRAINT pk_permissions
        PRIMARY KEY (id),

    CONSTRAINT uq_permissions_tuple
        UNIQUE (module, resource, action, scope),

    CONSTRAINT uq_permissions_code
        UNIQUE (code),

    CONSTRAINT ck_permissions_code_format
        CHECK (code ~ '^[A-Z_]+\.[A-Z_]+\.[A-Z_]+\.[A-Z_]+$')
);

CREATE TRIGGER tg_permissions_set_updated_at
    BEFORE UPDATE ON permissions
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- roles
--
-- Named bundle of permissions. Platform-defined and platform-global.
-- Same role catalogue applies to every tenant. Audience flag controls
-- whether the role is for Ithina staff (PLATFORM) or customer users
-- (TENANT).
--
-- Platform-global table. No tenant_id. No RLS. Access controlled by
-- API-layer authorisation: all logged-in users can read; only
-- ROLES.CONFIGURE can mutate.
-- ----------------------------------------------------------------------------

CREATE TABLE roles (

    -- ---------- Surrogate primary key ----------
    id              UUID                    NOT NULL DEFAULT uuidv7(),

    -- ---------- Identity ----------
    name            TEXT                    NOT NULL,
        -- Display name (e.g., 'Pricing Manager', 'Super Admin').
    code            TEXT                    NOT NULL,
        -- Stable wire-code (e.g., 'PRICING_MANAGER', 'SUPER_ADMIN').
        -- Used in API and config files; not localised.
    description     TEXT                    NULL,

    -- ---------- Classification ----------
    audience        role_audience_enum      NOT NULL,
        -- PLATFORM: Ithina staff. May hold GLOBAL-scope permissions.
        -- TENANT:   customer staff. Cap is TENANT-scope.

    -- ---------- Lifecycle ----------
    status          role_status_enum        NOT NULL DEFAULT 'ACTIVE',
    is_system       BOOLEAN                 NOT NULL DEFAULT FALSE,
        -- TRUE for roles shipped by Ithina that should not be deleted
        -- (e.g., SUPER_ADMIN). FALSE for roles created post-launch.
        -- Mutating system roles requires elevated privilege.

    -- ---------- Audit (pattern b) ----------
    created_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    created_by_user_id      UUID                    NULL,
    created_by_user_type    actor_user_type_enum    NULL,
    updated_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    updated_by_user_id      UUID                    NULL,
    updated_by_user_type    actor_user_type_enum    NULL,
    archived_at             TIMESTAMPTZ             NULL,
    archived_by_user_id     UUID                    NULL,
    archived_by_user_type   actor_user_type_enum    NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_roles
        PRIMARY KEY (id),

    CONSTRAINT uq_roles_code
        UNIQUE (code),

    CONSTRAINT ck_roles_name_length
        CHECK (LENGTH(name) BETWEEN 1 AND 100),

    CONSTRAINT ck_roles_code_format
        CHECK (code ~ '^[A-Z][A-Z0-9_]{1,49}$'),

    CONSTRAINT ck_roles_archived_consistency
        CHECK (
            (status = 'ARCHIVED'
                AND archived_at IS NOT NULL
                AND archived_by_user_id IS NOT NULL
                AND archived_by_user_type IS NOT NULL)
            OR
            (status != 'ARCHIVED'
                AND archived_at IS NULL
                AND archived_by_user_id IS NULL
                AND archived_by_user_type IS NULL)
        ),

    CONSTRAINT ck_roles_created_by_actor_pair
        CHECK (
            (created_by_user_id IS NULL AND created_by_user_type IS NULL)
            OR
            (created_by_user_id IS NOT NULL AND created_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_roles_updated_by_actor_pair
        CHECK (
            (updated_by_user_id IS NULL AND updated_by_user_type IS NULL)
            OR
            (updated_by_user_id IS NOT NULL AND updated_by_user_type IS NOT NULL)
        )
);

CREATE INDEX ix_roles_audience
    ON roles (audience);

CREATE INDEX ix_roles_status
    ON roles (status);

CREATE TRIGGER tg_roles_set_updated_at
    BEFORE UPDATE ON roles
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- role_permissions
--
-- Many-to-many junction between roles and permissions. Defines which
-- permissions a role grants. Updated only by Ithina platform admins.
--
-- Platform-global table. No tenant_id. No RLS.
--
-- Audience-scope invariant (enforced at app layer in v1):
--   * If role.audience = TENANT, no linked permission may have
--     scope = GLOBAL. GLOBAL is reserved for platform roles.
--   * If role.audience = PLATFORM, any scope is permitted.
-- ----------------------------------------------------------------------------

CREATE TABLE role_permissions (

    role_id                 UUID                    NOT NULL,
    permission_id           UUID                    NOT NULL,

    -- ---------- Audit (pattern b) ----------
    created_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    created_by_user_id      UUID                    NULL,
    created_by_user_type    actor_user_type_enum    NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_role_permissions
        PRIMARY KEY (role_id, permission_id),

    CONSTRAINT fk_role_permissions_role
        FOREIGN KEY (role_id)
        REFERENCES roles (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_role_permissions_permission
        FOREIGN KEY (permission_id)
        REFERENCES permissions (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT ck_role_permissions_created_by_actor_pair
        CHECK (
            (created_by_user_id IS NULL AND created_by_user_type IS NULL)
            OR
            (created_by_user_id IS NOT NULL AND created_by_user_type IS NOT NULL)
        )
);

CREATE INDEX ix_role_permissions_permission
    ON role_permissions (permission_id);


-- ----------------------------------------------------------------------------
-- user_role_assignments
--
-- Assigns a role to a user at a specific org_node anchor. Permissions
-- granted by the role cascade DOWNWARD from the org_node anchor through
-- the org tree (ltree path comparison).
--
-- A user may hold multiple roles. A user's effective permission set is
-- the UNION of permissions across all active assignments.
--
-- Shape rules (some enforced at DB level, some at app layer):
--
--   PLATFORM audience (Ithina staff):
--     - tenant_id      MUST be NULL
--     - org_node_id    MUST be NULL
--     - The staff user is global-scope; no anchor needed.
--
--   TENANT audience (customer staff):
--     - tenant_id      MUST be NOT NULL
--     - org_node_id    MUST be NOT NULL
--     - org_node_id must belong to the same tenant_id (enforced by
--       composite FK to org_nodes (tenant_id, id)).
--     - org_node level must be at or above the widest permission scope
--       in the role. Enforced at app layer in v1.
--
-- tenant_id is denormalised onto this row (rather than joined through
-- org_nodes) so RLS can filter directly.
-- ----------------------------------------------------------------------------

CREATE TABLE user_role_assignments (

    -- ---------- Surrogate primary key ----------
    id                      UUID                            NOT NULL DEFAULT uuidv7(),

    -- ---------- Subject (Pattern 2: exactly one of these is non-NULL) ----------
    platform_user_id        UUID                            NULL,
        -- platform_users.id when role.audience = PLATFORM. NULL when
        -- role.audience = TENANT.
    tenant_user_id          UUID                            NULL,
        -- tenant_users.id when role.audience = TENANT. NULL when
        -- role.audience = PLATFORM.

    -- ---------- Granted role ----------
    role_id                 UUID                            NOT NULL,

    -- ---------- Anchor ----------
    tenant_id               UUID                            NULL,
        -- NOT NULL for TENANT-audience roles, NULL for PLATFORM-audience.
    org_node_id             UUID                            NULL,
        -- NOT NULL for TENANT-audience roles, NULL for PLATFORM-audience.
        -- Permissions cascade DOWNWARD from this node via ltree path.

    -- ---------- Lifecycle ----------
    status                  user_role_assignment_status_enum NOT NULL DEFAULT 'ACTIVE',

    -- ---------- Audit (pattern b: id + type, no FK) ----------
    granted_at              TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    granted_by_user_id      UUID                            NULL,
    granted_by_user_type    actor_user_type_enum            NULL,
    revoked_at              TIMESTAMPTZ                     NULL,
    revoked_by_user_id      UUID                            NULL,
    revoked_by_user_type    actor_user_type_enum            NULL,
    updated_at              TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),

    -- ---------- Constraints ----------

    CONSTRAINT pk_user_role_assignments
        PRIMARY KEY (id),

    CONSTRAINT fk_user_role_assignments_role
        FOREIGN KEY (role_id)
        REFERENCES roles (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_user_role_assignments_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES tenants (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- Composite FK ensures the org_node belongs to the same tenant.
    -- Relies on org_nodes UNIQUE(tenant_id, id).
    CONSTRAINT fk_user_role_assignments_org_node_same_tenant
        FOREIGN KEY (tenant_id, org_node_id)
        REFERENCES org_nodes (tenant_id, id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- FK to platform_users (fires only when platform_user_id is non-NULL).
    CONSTRAINT fk_user_role_assignments_platform_user
        FOREIGN KEY (platform_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- FK to tenant_users (fires only when tenant_user_id is non-NULL).
    CONSTRAINT fk_user_role_assignments_tenant_user
        FOREIGN KEY (tenant_user_id)
        REFERENCES tenant_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- XOR: exactly one of platform_user_id / tenant_user_id must be set.
    CONSTRAINT ck_user_role_assignments_user_xor
        CHECK (
            (platform_user_id IS NOT NULL AND tenant_user_id IS NULL)
            OR
            (platform_user_id IS NULL AND tenant_user_id IS NOT NULL)
        ),

    -- Anchor shape: tenant_id and org_node_id must both be NULL
    -- (PLATFORM-audience) or both NOT NULL (TENANT-audience).
    CONSTRAINT ck_user_role_assignments_anchor_shape
        CHECK (
            (tenant_id IS NULL AND org_node_id IS NULL)
            OR
            (tenant_id IS NOT NULL AND org_node_id IS NOT NULL)
        ),

    -- Combined: PLATFORM user has no anchor; TENANT user must have anchor.
    -- Cross-check with the user XOR + anchor shape constraints above.
    CONSTRAINT ck_user_role_assignments_user_anchor_consistency
        CHECK (
            (platform_user_id IS NOT NULL AND tenant_id IS NULL AND org_node_id IS NULL)
            OR
            (tenant_user_id   IS NOT NULL AND tenant_id IS NOT NULL AND org_node_id IS NOT NULL)
        ),

    -- Pattern (b) actor pair shape.
    CONSTRAINT ck_user_role_assignments_granted_by_actor_pair
        CHECK (
            (granted_by_user_id IS NULL AND granted_by_user_type IS NULL)
            OR
            (granted_by_user_id IS NOT NULL AND granted_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_user_role_assignments_revoked_by_actor_pair
        CHECK (
            (revoked_by_user_id IS NULL AND revoked_by_user_type IS NULL)
            OR
            (revoked_by_user_id IS NOT NULL AND revoked_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_user_role_assignments_revoked_consistency
        CHECK (
            (status = 'INACTIVE'
                AND revoked_at IS NOT NULL
                AND revoked_by_user_id IS NOT NULL
                AND revoked_by_user_type IS NOT NULL)
            OR
            (status = 'ACTIVE'
                AND revoked_at IS NULL
                AND revoked_by_user_id IS NULL
                AND revoked_by_user_type IS NULL)
        )
);

-- ---------- Indexes ----------

-- Partial UNIQUE: a platform user cannot hold the same role twice
-- (active). PLATFORM assignments have no org_node anchor, so the
-- uniqueness is just (platform_user_id, role_id).
CREATE UNIQUE INDEX uq_user_role_assignments_platform_active_unique
    ON user_role_assignments (platform_user_id, role_id)
    WHERE status = 'ACTIVE' AND platform_user_id IS NOT NULL;

-- Partial UNIQUE: a tenant user cannot hold the same role at the same
-- org_node twice (active).
CREATE UNIQUE INDEX uq_user_role_assignments_tenant_active_unique
    ON user_role_assignments (tenant_user_id, role_id, org_node_id)
    WHERE status = 'ACTIVE' AND tenant_user_id IS NOT NULL;

-- Tenant-scoped queries (RLS path).
CREATE INDEX ix_user_role_assignments_tenant
    ON user_role_assignments (tenant_id);

-- "What roles does this platform user have?"
CREATE INDEX ix_user_role_assignments_platform_user
    ON user_role_assignments (platform_user_id)
    WHERE platform_user_id IS NOT NULL;

-- "What roles does this tenant user have?"
CREATE INDEX ix_user_role_assignments_tenant_user
    ON user_role_assignments (tenant_user_id)
    WHERE tenant_user_id IS NOT NULL;

-- "Who has this role assigned at this org_node?"
CREATE INDEX ix_user_role_assignments_role_org_node
    ON user_role_assignments (role_id, org_node_id);

-- Active platform assignments for permission resolution at login.
CREATE INDEX ix_user_role_assignments_platform_user_active
    ON user_role_assignments (platform_user_id)
    WHERE status = 'ACTIVE' AND platform_user_id IS NOT NULL;

-- Active tenant assignments for permission resolution at login.
CREATE INDEX ix_user_role_assignments_tenant_user_active
    ON user_role_assignments (tenant_user_id)
    WHERE status = 'ACTIVE' AND tenant_user_id IS NOT NULL;

CREATE TRIGGER tg_user_role_assignments_set_updated_at
    BEFORE UPDATE ON user_role_assignments
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();

-- ---------- Row-Level Security ----------
--
-- TENANT-audience assignment rows have tenant_id set; tenant users see
-- only their own tenant's rows. PLATFORM-audience rows have
-- tenant_id=NULL and are visible only to staff connections (which use
-- BYPASSRLS or a staff-role view that exposes them).
--
-- The policy expression filters by tenant_id match. NULL tenant_id rows
-- are filtered out for tenant sessions (NULL = NULL is UNKNOWN in SQL).
-- Staff sessions skip RLS via BYPASSRLS.

ALTER TABLE user_role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_role_assignments FORCE ROW LEVEL SECURITY;

CREATE POLICY user_role_assignments_tenant_isolation
    ON user_role_assignments
    FOR ALL
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);


-- ============================================================================
-- Application-layer invariants captured here for reference. These are
-- not enforced by DDL in v1; document them in CLAUDE.md and enforce in
-- service code.
--
-- AI-RBAC-01: When inserting role_permissions, if role.audience='TENANT'
--             reject any permission with scope='GLOBAL'.
--
-- AI-RBAC-02: When inserting user_role_assignments:
--             - role.audience='PLATFORM' requires tenant_id IS NULL and
--               org_node_id IS NULL, AND the user must be Ithina staff.
--             - role.audience='TENANT' requires tenant_id IS NOT NULL
--               and org_node_id IS NOT NULL, AND the user must belong to
--               the same tenant.
--
-- AI-RBAC-03: Assignment org_node level must be at or above the widest
--             permission scope in the role. (Pricing Manager with
--             Region-scope permissions: assignable at Region or Tenant,
--             not at Store.)
--
-- AI-RBAC-04: Permission resolution at request time must filter by:
--             - assignment.status = ACTIVE
--             - role.status = ACTIVE
--             - user.is_active = TRUE
--             - org_node.status = ACTIVE
--             AuthContext caches the resolved set; invalidate on changes
--             to any of the above.
--
-- AI-RBAC-05: Permission cascade for TENANT-audience: a permission
--             granted at assignment org_node X applies to X and all
--             descendants of X in the org tree (ltree path comparison).
--             For PLATFORM-audience: permission applies globally.
--
-- AI-RBAC-06: Cross-tenant injection prevention. When inserting a
--             user_role_assignments row with tenant_user_id NOT NULL,
--             app layer must verify that
--                 tenant_users.tenant_id WHERE id = tenant_user_id
--             equals the assignment's tenant_id column. Without this
--             check, a row could pair a tenant_user from tenant A with
--             tenant_id = tenant B; RLS would hide it from tenant A's
--             session, making it invisible to that tenant's auditors.
--             Future enforcement: DB trigger that performs this lookup
--             on insert/update. Tracked as part of FN-AB-09 tech debt.
-- ============================================================================
"""


# Order matters: dependencies before dependents.
DDL_IN_ORDER = [
    SQL_SHARED_UTILITIES,
    SQL_LOOKUPS,
    SQL_PLATFORM_USERS,
    SQL_TENANTS,
    SQL_TENANT_USERS,
    SQL_ORG_NODES,
    SQL_STORES,
    SQL_RBAC,
]


# ============================================================================
# Tables to drop on downgrade (reverse dependency order).
# ============================================================================

TABLES_REVERSE_ORDER = [
    "user_role_assignments",
    "role_permissions",
    "roles",
    "permissions",
    "stores",
    "org_nodes",
    "tenant_users",
    "tenants",
    "platform_users",
    "lookups",
]


# ============================================================================
# Enums to drop on downgrade. Enumerated from DDL CREATE TYPE statements
# at generation time; see Step 1.6 prompt section 2a for the grep.
# ============================================================================

ENUMS_TO_DROP = [
    "action_enum",
    "actor_user_type_enum",
    "module_enum",
    "org_node_status_enum",
    "org_node_type_enum",
    "permission_scope_enum",
    "platform_user_status_enum",
    "resource_enum",
    "role_audience_enum",
    "role_status_enum",
    "store_status_enum",
    "tax_treatment_enum",
    "tenant_industry_enum",
    "tenant_region_enum",
    "tenant_status_enum",
    "tenant_tier_enum",
    "tenant_user_status_enum",
    "user_role_assignment_status_enum",
]


# ============================================================================
# Functions to drop on downgrade. Enumerated from DDL CREATE FUNCTION
# statements at generation time; case-insensitive grep catches both the
# uppercase project style and the lowercase kjmph-vendored style.
# ============================================================================

FUNCTIONS_TO_DROP = [
    "set_updated_at_timestamp",
    "uuidv7",
]


# ============================================================================
# Precondition check: required extensions must be present
# ============================================================================
#
# CREATE EXTENSION requires superuser privilege; the application role
# (per Step 1.5) is NOSUPERUSER NOBYPASSRLS. Extensions must be
# installed during database setup by a privileged role, not here.
#
# This check runs first in upgrade() and aborts the migration with a
# clear error if either expected extension is missing. Failing this
# means the database wasn't set up correctly before migrations were
# applied; the fix is in the setup procedure, not in the migration.

PRECONDITION_CHECK_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'ltree') THEN
        RAISE EXCEPTION 'ltree extension is required but not installed. '
            'Install via: CREATE EXTENSION ltree; (requires superuser)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
        RAISE EXCEPTION 'pgcrypto extension is required but not installed. '
            'Install via: CREATE EXTENSION pgcrypto; (requires superuser)';
    END IF;
END
$$;
"""


def upgrade() -> None:
    """Apply the initial schema.

    Runs the extension-precondition check first; if it raises, the
    migration aborts before any DDL is applied. Then applies each DDL
    in dependency order. search_path is set by env.py before this
    function runs, so unqualified table names resolve to the configured
    schema (DB_SCHEMA env var).
    """
    op.execute(PRECONDITION_CHECK_SQL)
    for sql in DDL_IN_ORDER:
        op.execute(sql)


def downgrade() -> None:
    """Reverse the upgrade.

    Drops tables (reverse dependency order), then enums, then functions.
    CASCADE handles any residual FK or trigger dependencies; IF EXISTS
    keeps the downgrade idempotent. Extensions are NOT dropped: they may
    be shared with other schemas in the same database, and dropping
    them is the database administrator's call, not this migration's.
    """
    for table in TABLES_REVERSE_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")

    for enum in ENUMS_TO_DROP:
        op.execute(f"DROP TYPE IF EXISTS {enum} CASCADE;")

    for func in FUNCTIONS_TO_DROP:
        op.execute(f"DROP FUNCTION IF EXISTS {func}() CASCADE;")
