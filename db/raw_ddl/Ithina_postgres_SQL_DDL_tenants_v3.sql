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
