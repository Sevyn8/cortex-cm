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
