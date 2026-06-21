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
