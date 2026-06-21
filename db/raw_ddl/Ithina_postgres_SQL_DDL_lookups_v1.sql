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
