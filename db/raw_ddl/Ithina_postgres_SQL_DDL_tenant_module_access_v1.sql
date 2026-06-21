-- ============================================================================
-- Ithina platform master DB — tenant_module_access
-- Postgres SQL DDL
-- Version: v1
--
-- Tracks which modules each tenant is entitled to use, with full lifecycle
-- audit (enabled_by, disabled_by, dates) for billing and compliance.
--
-- Modules are platform-fixed: Ithina engineering adds new modules via DDL
-- migration. Tenants do NOT extend the module set. The module_code_enum
-- enumerates the set; the lookups table provides display names for UI.
--
-- Pattern (a) audit-actors per D-13: typed FKs direct to platform_users,
-- no *_by_user_type discriminator (modules are managed by Ithina staff
-- only; no TENANT user_type ever appears in audit-actor columns).
--
-- RLS follows D-29: tenant-id-equality plus PLATFORM OR-branch. The
-- unconditional form (no IS-NULL gate) since tenant_id is NOT NULL on
-- this table.
--
-- Dependencies (must exist before this file runs):
--   * shared_utilities (uuidv7(), set_updated_at_timestamp())
--   * tenants (FK target)
--   * platform_users (FK target for audit-actor columns)
--   * lookups (population coordinated via seed in the same migration)
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enum types
-- ----------------------------------------------------------------------------

CREATE TYPE module_code_enum AS ENUM (
    'ROOS',
    'PRICING_OS',
    'PERISHABLES_ASSISTANT',
    'PROMOTIONS_ASSISTANT',
    'GOAL_CONSOLE',
    'ADMIN'
);

CREATE TYPE module_access_status_enum AS ENUM (
    'ENABLED',
    'DISABLED'
);


-- ----------------------------------------------------------------------------
-- Table: tenant_module_access
-- ----------------------------------------------------------------------------

CREATE TABLE tenant_module_access (

    -- ---------- Surrogate primary key ----------
    id                          UUID                            NOT NULL DEFAULT uuidv7(),

    -- ---------- Identity ----------
    tenant_id                   UUID                            NOT NULL,
    module                      module_code_enum                NOT NULL,
    status                      module_access_status_enum       NOT NULL,

    -- ---------- Lifecycle ----------
    enabled_at                  TIMESTAMPTZ                     NOT NULL,
        -- Required: billing reads this to compute prorated charges.
    enabled_by_user_id          UUID                            NOT NULL,
    disabled_at                 TIMESTAMPTZ                     NULL,
    disabled_by_user_id         UUID                            NULL,

    -- ---------- Audit (Pattern (a) per D-13) ----------
    created_at                  TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    created_by_user_id          UUID                            NOT NULL,
    updated_at                  TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    updated_by_user_id          UUID                            NOT NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_tenant_module_access
        PRIMARY KEY (id),

    CONSTRAINT uq_tenant_module_access_tenant_module
        UNIQUE (tenant_id, module),
        -- One row per tenant per module. Re-enabling after disable
        -- updates the existing row rather than inserting a new one.

    CONSTRAINT fk_tenant_module_access_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES tenants (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_tenant_module_access_enabled_by
        FOREIGN KEY (enabled_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_tenant_module_access_disabled_by
        FOREIGN KEY (disabled_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_tenant_module_access_created_by
        FOREIGN KEY (created_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_tenant_module_access_updated_by
        FOREIGN KEY (updated_by_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT ck_tenant_module_access_disabled_pair
        CHECK (
            (disabled_at IS NULL AND disabled_by_user_id IS NULL)
            OR
            (disabled_at IS NOT NULL AND disabled_by_user_id IS NOT NULL)
        ),
        -- XOR pairing: both disabled_* NULL (currently enabled) or
        -- both NOT NULL (disabled).

    CONSTRAINT ck_tenant_module_access_status_consistency
        CHECK (
            (status = 'ENABLED' AND disabled_at IS NULL)
            OR
            (status = 'DISABLED' AND disabled_at IS NOT NULL)
        )
        -- ENABLED requires no disabled_at; DISABLED requires it set.
);


-- ----------------------------------------------------------------------------
-- Indexes beyond PK and UNIQUE (which auto-create indexes)
-- ----------------------------------------------------------------------------

-- Primary read pattern: the API joins tenants -> tenant_module_access on
-- tenant_id to populate the modules array for each tenant card.
CREATE INDEX ix_tenant_module_access_tenant_id
    ON tenant_module_access (tenant_id);


-- ----------------------------------------------------------------------------
-- Trigger: keep updated_at fresh on every UPDATE
-- ----------------------------------------------------------------------------

CREATE TRIGGER tg_tenant_module_access_set_updated_at
    BEFORE UPDATE ON tenant_module_access
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- Row-Level Security
--
-- Standard pattern per D-03 + D-27 + D-29: tenant-id-equality plus
-- unconditional PLATFORM OR-branch (tenant_id is NOT NULL here, so the
-- IS-NULL-gated FN-AB-14 form would never fire).
-- ----------------------------------------------------------------------------

ALTER TABLE tenant_module_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_module_access FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_module_access_tenant_isolation
    ON tenant_module_access
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
        OR current_setting('app.user_type', TRUE) = 'PLATFORM'
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
        OR current_setting('app.user_type', TRUE) = 'PLATFORM'
    );
