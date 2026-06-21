-- ============================================================
-- core schema dump
-- ============================================================
-- Generated: 2026-05-23T14:36:25Z
--
-- pg_dump version:   pg_dump (PostgreSQL) 15.18 (Ubuntu 15.18-1.pgdg22.04+1)
-- Postgres version:  PostgreSQL 15.17 (Debian 15.17-1.pgdg13+1) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit
-- DATABASE_URL host: localhost (Docker container ithina-postgres)
--
-- Alembic head:      7a3c8e9d2f5b
-- Alembic current:   7a3c8e9d2f5b
-- (head == current verified at Step 6.16.7 pre-flight)
--
-- This file represents the LOCAL Postgres schema at alembic head.
-- Cloud SQL is verified separately via operator deployment workflow.
--
-- Regenerated on each run of prompts/refresh-schema-docs-prompt.md;
-- git diff between runs shows schema deltas.
-- ============================================================

--
-- PostgreSQL database dump
--

\restrict Ed2GbTUvH9lX5fRFeINgivVKtalJ4zbhpkHjMkOEB00QQyFPqhzHapxhXawsrOu

-- Dumped from database version 15.17 (Debian 15.17-1.pgdg13+1)
-- Dumped by pg_dump version 15.18 (Ubuntu 15.18-1.pgdg22.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: core; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA core;


--
-- Name: action_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.action_enum AS ENUM (
    'VIEW',
    'CONFIGURE',
    'EXECUTE',
    'APPROVE',
    'OVERRIDE',
    'AUDIT'
);


--
-- Name: actor_user_type_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.actor_user_type_enum AS ENUM (
    'PLATFORM',
    'TENANT'
);


--
-- Name: audit_result_type_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.audit_result_type_enum AS ENUM (
    'SUCCESS',
    'PERMISSION_DENIED',
    'VALIDATION_FAILED',
    'CONFLICT',
    'INTEGRITY_VIOLATION',
    'INTERNAL_ERROR'
);


--
-- Name: module_access_status_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.module_access_status_enum AS ENUM (
    'ENABLED',
    'DISABLED'
);


--
-- Name: module_code_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.module_code_enum AS ENUM (
    'ROOS',
    'PRICING_OS',
    'PERISHABLES_ASSISTANT',
    'PROMOTIONS_ASSISTANT',
    'GOAL_CONSOLE',
    'ADMIN'
);


--
-- Name: org_node_status_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.org_node_status_enum AS ENUM (
    'ACTIVE',
    'INACTIVE',
    'ARCHIVED'
);


--
-- Name: org_node_type_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.org_node_type_enum AS ENUM (
    'TENANT',
    'BUSINESS_UNIT',
    'HQ',
    'COUNTRY',
    'REGION',
    'STORE',
    'DEPARTMENT'
);


--
-- Name: permission_scope_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.permission_scope_enum AS ENUM (
    'GLOBAL',
    'TENANT',
    'STORE'
);


--
-- Name: platform_user_status_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.platform_user_status_enum AS ENUM (
    'INVITED',
    'ACTIVE',
    'SUSPENDED'
);


--
-- Name: resource_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.resource_enum AS ENUM (
    'PRICING_RULES',
    'MARKDOWNS',
    'EXPIRING_ITEMS',
    'WASTE_LOG',
    'DONATION_ROUTING',
    'CAMPAIGNS',
    'USERS',
    'ROLES',
    'AUDIT_LOG',
    'TENANTS',
    'STORES',
    'ORG_NODES'
);


--
-- Name: role_audience_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.role_audience_enum AS ENUM (
    'PLATFORM',
    'TENANT'
);


--
-- Name: role_status_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.role_status_enum AS ENUM (
    'ACTIVE',
    'INACTIVE',
    'ARCHIVED'
);


--
-- Name: store_status_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.store_status_enum AS ENUM (
    'OPENING',
    'ACTIVE',
    'INACTIVE',
    'CLOSED'
);


--
-- Name: tax_treatment_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.tax_treatment_enum AS ENUM (
    'EXCLUSIVE',
    'INCLUSIVE'
);


--
-- Name: tenant_industry_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.tenant_industry_enum AS ENUM (
    'CONVENIENCE_FUEL',
    'CONVENIENCE',
    'GROCERY',
    'HYPERMART',
    'SPECIALITY_GROCERY',
    'ORGANIC_GROCERY'
);


--
-- Name: tenant_region_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.tenant_region_enum AS ENUM (
    'US',
    'EU'
);


--
-- Name: tenant_status_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.tenant_status_enum AS ENUM (
    'ONBOARDING',
    'TRIAL',
    'ACTIVE',
    'SUSPENDED',
    'TERMINATED'
);


--
-- Name: tenant_tier_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.tenant_tier_enum AS ENUM (
    'ENTERPRISE',
    'MID_MARKET',
    'SMB',
    'SINGLE_STORE'
);


--
-- Name: tenant_user_status_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.tenant_user_status_enum AS ENUM (
    'INVITED',
    'ACTIVE',
    'SUSPENDED'
);


--
-- Name: user_role_assignment_status_enum; Type: TYPE; Schema: core; Owner: -
--

CREATE TYPE core.user_role_assignment_status_enum AS ENUM (
    'ACTIVE',
    'INACTIVE'
);


--
-- Name: enforce_platform_role_audience(); Type: FUNCTION; Schema: core; Owner: -
--

CREATE FUNCTION core.enforce_platform_role_audience() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            v_audience core.role_audience_enum;
        BEGIN
            SELECT audience INTO v_audience
            FROM core.roles
            WHERE id = NEW.role_id;
            IF v_audience IS DISTINCT FROM 'PLATFORM' THEN
                RAISE EXCEPTION
                    'audience-check: platform_user_role_assignments requires PLATFORM-audience role; role % has audience %',
                    NEW.role_id, v_audience;
            END IF;
            RETURN NEW;
        END;
        $$;


--
-- Name: enforce_role_audience_scope_coherence(); Type: FUNCTION; Schema: core; Owner: -
--

CREATE FUNCTION core.enforce_role_audience_scope_coherence() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            v_role_audience core.role_audience_enum;
            v_perm_scope core.permission_scope_enum;
        BEGIN
            SELECT audience INTO v_role_audience
            FROM core.roles
            WHERE id = NEW.role_id;
            SELECT scope INTO v_perm_scope
            FROM core.permissions
            WHERE id = NEW.permission_id;
            IF v_role_audience = 'TENANT' AND v_perm_scope = 'GLOBAL' THEN
                RAISE EXCEPTION
                    'audience-scope-check: TENANT-audience role cannot hold GLOBAL-scope permission (role_id=%, permission_id=%)',
                    NEW.role_id, NEW.permission_id;
            END IF;
            RETURN NEW;
        END;
        $$;


--
-- Name: enforce_tenant_role_audience(); Type: FUNCTION; Schema: core; Owner: -
--

CREATE FUNCTION core.enforce_tenant_role_audience() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            v_audience core.role_audience_enum;
        BEGIN
            SELECT audience INTO v_audience
            FROM core.roles
            WHERE id = NEW.role_id;
            IF v_audience IS DISTINCT FROM 'TENANT' THEN
                RAISE EXCEPTION
                    'audience-check: tenant_user_role_assignments requires TENANT-audience role; role % has audience %',
                    NEW.role_id, v_audience;
            END IF;
            RETURN NEW;
        END;
        $$;


--
-- Name: protect_super_admin_override_global_grant(); Type: FUNCTION; Schema: core; Owner: -
--

CREATE FUNCTION core.protect_super_admin_override_global_grant() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            v_super_admin_id UUID;
            v_override_global_id UUID;
        BEGIN
            SELECT id INTO v_super_admin_id
            FROM core.roles
            WHERE code = 'SUPER_ADMIN';
            SELECT id INTO v_override_global_id
            FROM core.permissions
            WHERE code = 'ADMIN.ROLES.OVERRIDE.GLOBAL';
            IF OLD.role_id = v_super_admin_id AND OLD.permission_id = v_override_global_id THEN
                RAISE EXCEPTION
                    'bootstrap-protection: cannot delete SUPER_ADMIN x ADMIN.ROLES.OVERRIDE.GLOBAL grant';
            END IF;
            RETURN OLD;
        END;
        $$;


--
-- Name: protect_super_admin_role(); Type: FUNCTION; Schema: core; Owner: -
--

CREATE FUNCTION core.protect_super_admin_role() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.code = 'SUPER_ADMIN' THEN
                    RAISE EXCEPTION
                        'bootstrap-protection: SUPER_ADMIN role cannot be deleted';
                END IF;
                RETURN OLD;
            ELSIF TG_OP = 'UPDATE' THEN
                IF OLD.code = 'SUPER_ADMIN' AND (
                    NEW.code IS DISTINCT FROM OLD.code OR
                    NEW.status IS DISTINCT FROM OLD.status OR
                    NEW.audience IS DISTINCT FROM OLD.audience
                ) THEN
                    RAISE EXCEPTION
                        'bootstrap-protection: SUPER_ADMIN role status, code, and audience are immutable';
                END IF;
                RETURN NEW;
            END IF;
            RETURN NULL;
        END;
        $$;


--
-- Name: set_updated_at_timestamp(); Type: FUNCTION; Schema: core; Owner: -
--

CREATE FUNCTION core.set_updated_at_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: uuidv7(); Type: FUNCTION; Schema: core; Owner: -
--

CREATE FUNCTION core.uuidv7() RETURNS uuid
    LANGUAGE plpgsql
    AS $$
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
$$;


--
-- Name: FUNCTION uuidv7(); Type: COMMENT; Schema: core; Owner: -
--

COMMENT ON FUNCTION core.uuidv7() IS 'UUIDv7 generator (RFC 9562). Vendored from kjmph PL/pgSQL reference; see Ithina_postgres_SQL_DDL_shared_utilities_v1.sql header for provenance. Used as DEFAULT for every metadata-table PK per D-21.';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: lookups; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.lookups (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    list_name text NOT NULL,
    code text NOT NULL,
    display_name text NOT NULL,
    description text,
    display_order integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_lookups_code_format CHECK ((code ~ '^[A-Z][A-Z0-9_]*$'::text)),
    CONSTRAINT ck_lookups_display_name_not_empty CHECK ((length(btrim(display_name)) > 0)),
    CONSTRAINT ck_lookups_display_order_non_negative CHECK ((display_order >= 0)),
    CONSTRAINT ck_lookups_list_name_format CHECK ((list_name ~ '^[a-z][a-z0-9_]*$'::text))
);


--
-- Name: org_nodes; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.org_nodes (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    tenant_id uuid NOT NULL,
    parent_id uuid,
    path public.ltree NOT NULL,
    node_type core.org_node_type_enum NOT NULL,
    name text NOT NULL,
    code text NOT NULL,
    status core.org_node_status_enum DEFAULT 'ACTIVE'::core.org_node_status_enum NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_user_id uuid,
    created_by_user_type core.actor_user_type_enum,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id uuid,
    updated_by_user_type core.actor_user_type_enum,
    archived_at timestamp with time zone,
    archived_by_user_id uuid,
    archived_by_user_type core.actor_user_type_enum,
    CONSTRAINT ck_org_nodes_archived_consistency CHECK ((((status = 'ARCHIVED'::core.org_node_status_enum) AND (archived_at IS NOT NULL) AND (archived_by_user_id IS NOT NULL) AND (archived_by_user_type IS NOT NULL)) OR ((status <> 'ARCHIVED'::core.org_node_status_enum) AND (archived_at IS NULL) AND (archived_by_user_id IS NULL) AND (archived_by_user_type IS NULL)))),
    CONSTRAINT ck_org_nodes_code_format CHECK (((code ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,62}[A-Za-z0-9]$'::text) OR (length(code) = 1))),
    CONSTRAINT ck_org_nodes_created_by_actor_pair CHECK ((((created_by_user_id IS NULL) AND (created_by_user_type IS NULL)) OR ((created_by_user_id IS NOT NULL) AND (created_by_user_type IS NOT NULL)))),
    CONSTRAINT ck_org_nodes_name_length CHECK (((length(name) >= 1) AND (length(name) <= 200))),
    CONSTRAINT ck_org_nodes_root_parent_consistency CHECK ((((node_type = 'TENANT'::core.org_node_type_enum) AND (parent_id IS NULL)) OR ((node_type <> 'TENANT'::core.org_node_type_enum) AND (parent_id IS NOT NULL)))),
    CONSTRAINT ck_org_nodes_updated_by_actor_pair CHECK ((((updated_by_user_id IS NULL) AND (updated_by_user_type IS NULL)) OR ((updated_by_user_id IS NOT NULL) AND (updated_by_user_type IS NOT NULL))))
);

ALTER TABLE ONLY core.org_nodes FORCE ROW LEVEL SECURITY;


--
-- Name: permissions; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.permissions (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    module core.module_code_enum NOT NULL,
    resource core.resource_enum NOT NULL,
    action core.action_enum NOT NULL,
    scope core.permission_scope_enum NOT NULL,
    code text NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_permissions_code_format CHECK ((code ~ '^[A-Z_]+\.[A-Z_]+\.[A-Z_]+\.[A-Z_]+$'::text))
);


--
-- Name: platform_activity_audit_logs; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.platform_activity_audit_logs (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid,
    tenant_name text,
    actor_user_id uuid NOT NULL,
    actor_user_type core.actor_user_type_enum NOT NULL,
    actor_display_name text NOT NULL,
    resource_type text NOT NULL,
    resource_id uuid,
    resource_label text,
    action text NOT NULL,
    action_label text NOT NULL,
    result_type core.audit_result_type_enum NOT NULL,
    result_label text NOT NULL,
    request_id uuid NOT NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    actor_organization_name text NOT NULL,
    actor_roles text NOT NULL,
    resource_subtype text,
    CONSTRAINT ck_platform_activity_audit_logs_resource_pair CHECK ((((resource_id IS NULL) AND (resource_label IS NULL)) OR ((resource_id IS NOT NULL) AND (resource_label IS NOT NULL)))),
    CONSTRAINT ck_platform_activity_audit_logs_tenant_pair CHECK ((((tenant_id IS NULL) AND (tenant_name IS NULL)) OR ((tenant_id IS NOT NULL) AND (tenant_name IS NOT NULL))))
);


--
-- Name: platform_user_role_assignments; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.platform_user_role_assignments (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    platform_user_id uuid NOT NULL,
    role_id uuid NOT NULL,
    status core.user_role_assignment_status_enum NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    granted_by_user_id uuid,
    granted_by_user_type core.actor_user_type_enum,
    revoked_at timestamp with time zone,
    revoked_by_user_id uuid,
    revoked_by_user_type core.actor_user_type_enum,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_platform_user_role_assignments_granted_by_actor_pair CHECK ((((granted_by_user_id IS NULL) AND (granted_by_user_type IS NULL)) OR ((granted_by_user_id IS NOT NULL) AND (granted_by_user_type IS NOT NULL)))),
    CONSTRAINT ck_platform_user_role_assignments_revoked_by_actor_pair CHECK ((((revoked_by_user_id IS NULL) AND (revoked_by_user_type IS NULL)) OR ((revoked_by_user_id IS NOT NULL) AND (revoked_by_user_type IS NOT NULL)))),
    CONSTRAINT ck_platform_user_role_assignments_revoked_consistency CHECK ((((status = 'INACTIVE'::core.user_role_assignment_status_enum) AND (revoked_at IS NOT NULL) AND (revoked_by_user_id IS NOT NULL) AND (revoked_by_user_type IS NOT NULL)) OR ((status = 'ACTIVE'::core.user_role_assignment_status_enum) AND (revoked_at IS NULL) AND (revoked_by_user_id IS NULL) AND (revoked_by_user_type IS NULL))))
);


--
-- Name: platform_users; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.platform_users (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    auth0_sub text,
    email text NOT NULL,
    full_name text NOT NULL,
    status core.platform_user_status_enum DEFAULT 'INVITED'::core.platform_user_status_enum NOT NULL,
    invited_at timestamp with time zone,
    invitation_accepted_at timestamp with time zone,
    suspended_at timestamp with time zone,
    suspended_by_user_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_user_id uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id uuid,
    CONSTRAINT ck_platform_users_auth0_sub_consistency CHECK ((((status = 'INVITED'::core.platform_user_status_enum) AND (auth0_sub IS NULL)) OR ((status = ANY (ARRAY['ACTIVE'::core.platform_user_status_enum, 'SUSPENDED'::core.platform_user_status_enum])) AND (auth0_sub IS NOT NULL)))),
    CONSTRAINT ck_platform_users_email_format CHECK ((email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'::text)),
    CONSTRAINT ck_platform_users_email_lowercase CHECK ((email = lower(email))),
    CONSTRAINT ck_platform_users_full_name_length CHECK (((length(full_name) >= 1) AND (length(full_name) <= 200))),
    CONSTRAINT ck_platform_users_invitation_accepted_consistency CHECK ((((status = 'INVITED'::core.platform_user_status_enum) AND (invitation_accepted_at IS NULL)) OR ((status = ANY (ARRAY['ACTIVE'::core.platform_user_status_enum, 'SUSPENDED'::core.platform_user_status_enum])) AND (invitation_accepted_at IS NOT NULL)))),
    CONSTRAINT ck_platform_users_suspended_consistency CHECK ((((status = 'SUSPENDED'::core.platform_user_status_enum) AND (suspended_at IS NOT NULL) AND (suspended_by_user_id IS NOT NULL)) OR ((status = ANY (ARRAY['INVITED'::core.platform_user_status_enum, 'ACTIVE'::core.platform_user_status_enum])) AND (suspended_at IS NULL) AND (suspended_by_user_id IS NULL))))
);


--
-- Name: role_permissions; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.role_permissions (
    role_id uuid NOT NULL,
    permission_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_user_id uuid,
    created_by_user_type core.actor_user_type_enum,
    CONSTRAINT ck_role_permissions_created_by_actor_pair CHECK ((((created_by_user_id IS NULL) AND (created_by_user_type IS NULL)) OR ((created_by_user_id IS NOT NULL) AND (created_by_user_type IS NOT NULL))))
);


--
-- Name: roles; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.roles (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    name text NOT NULL,
    code text NOT NULL,
    description text,
    audience core.role_audience_enum NOT NULL,
    status core.role_status_enum DEFAULT 'ACTIVE'::core.role_status_enum NOT NULL,
    is_system boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_user_id uuid,
    created_by_user_type core.actor_user_type_enum,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id uuid,
    updated_by_user_type core.actor_user_type_enum,
    archived_at timestamp with time zone,
    archived_by_user_id uuid,
    archived_by_user_type core.actor_user_type_enum,
    CONSTRAINT ck_roles_archived_consistency CHECK ((((status = 'ARCHIVED'::core.role_status_enum) AND (archived_at IS NOT NULL) AND (archived_by_user_id IS NOT NULL) AND (archived_by_user_type IS NOT NULL)) OR ((status <> 'ARCHIVED'::core.role_status_enum) AND (archived_at IS NULL) AND (archived_by_user_id IS NULL) AND (archived_by_user_type IS NULL)))),
    CONSTRAINT ck_roles_code_format CHECK ((code ~ '^[A-Z][A-Z0-9_]{1,49}$'::text)),
    CONSTRAINT ck_roles_created_by_actor_pair CHECK ((((created_by_user_id IS NULL) AND (created_by_user_type IS NULL)) OR ((created_by_user_id IS NOT NULL) AND (created_by_user_type IS NOT NULL)))),
    CONSTRAINT ck_roles_name_length CHECK (((length(name) >= 1) AND (length(name) <= 100))),
    CONSTRAINT ck_roles_updated_by_actor_pair CHECK ((((updated_by_user_id IS NULL) AND (updated_by_user_type IS NULL)) OR ((updated_by_user_id IS NOT NULL) AND (updated_by_user_type IS NOT NULL))))
);


--
-- Name: stores; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.stores (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    tenant_id uuid NOT NULL,
    org_node_id uuid NOT NULL,
    name text NOT NULL,
    store_code text,
    country text NOT NULL,
    timezone text NOT NULL,
    address text,
    latitude numeric(9,6),
    longitude numeric(9,6),
    currency character(3) NOT NULL,
    tax_treatment core.tax_treatment_enum NOT NULL,
    status core.store_status_enum DEFAULT 'ACTIVE'::core.store_status_enum NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_user_id uuid,
    created_by_user_type core.actor_user_type_enum,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id uuid,
    updated_by_user_type core.actor_user_type_enum,
    closed_at timestamp with time zone,
    closed_by_user_id uuid,
    closed_by_user_type core.actor_user_type_enum,
    CONSTRAINT ck_stores_closed_consistency CHECK ((((status = 'CLOSED'::core.store_status_enum) AND (closed_at IS NOT NULL) AND (closed_by_user_id IS NOT NULL) AND (closed_by_user_type IS NOT NULL)) OR ((status <> 'CLOSED'::core.store_status_enum) AND (closed_at IS NULL) AND (closed_by_user_id IS NULL) AND (closed_by_user_type IS NULL)))),
    CONSTRAINT ck_stores_country_format CHECK ((((length(country) >= 2) AND (length(country) <= 100)) AND (country ~ '[A-Za-z]'::text) AND (country !~ '^[[:space:]]*$'::text))),
    CONSTRAINT ck_stores_created_by_actor_pair CHECK ((((created_by_user_id IS NULL) AND (created_by_user_type IS NULL)) OR ((created_by_user_id IS NOT NULL) AND (created_by_user_type IS NOT NULL)))),
    CONSTRAINT ck_stores_currency_format CHECK ((currency ~ '^[A-Z]{3}$'::text)),
    CONSTRAINT ck_stores_latitude_range CHECK (((latitude IS NULL) OR ((latitude >= ('-90'::integer)::numeric) AND (latitude <= (90)::numeric)))),
    CONSTRAINT ck_stores_longitude_range CHECK (((longitude IS NULL) OR ((longitude >= ('-180'::integer)::numeric) AND (longitude <= (180)::numeric)))),
    CONSTRAINT ck_stores_name_length CHECK (((length(name) >= 1) AND (length(name) <= 200))),
    CONSTRAINT ck_stores_updated_by_actor_pair CHECK ((((updated_by_user_id IS NULL) AND (updated_by_user_type IS NULL)) OR ((updated_by_user_id IS NOT NULL) AND (updated_by_user_type IS NOT NULL))))
);

ALTER TABLE ONLY core.stores FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_activity_audit_logs; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.tenant_activity_audit_logs (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid NOT NULL,
    tenant_name text NOT NULL,
    actor_user_id uuid NOT NULL,
    actor_user_type core.actor_user_type_enum NOT NULL,
    actor_display_name text NOT NULL,
    resource_type text NOT NULL,
    resource_id uuid,
    resource_label text,
    action text NOT NULL,
    action_label text NOT NULL,
    result_type core.audit_result_type_enum NOT NULL,
    result_label text NOT NULL,
    request_id uuid NOT NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    actor_organization_name text NOT NULL,
    actor_roles text NOT NULL,
    resource_subtype text,
    CONSTRAINT ck_tenant_activity_audit_logs_resource_pair CHECK ((((resource_id IS NULL) AND (resource_label IS NULL)) OR ((resource_id IS NOT NULL) AND (resource_label IS NOT NULL))))
);

ALTER TABLE ONLY core.tenant_activity_audit_logs FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_module_access; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.tenant_module_access (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    tenant_id uuid NOT NULL,
    module core.module_code_enum NOT NULL,
    status core.module_access_status_enum NOT NULL,
    enabled_at timestamp with time zone NOT NULL,
    enabled_by_user_id uuid NOT NULL,
    disabled_at timestamp with time zone,
    disabled_by_user_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_user_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id uuid NOT NULL,
    CONSTRAINT ck_tenant_module_access_disabled_pair CHECK ((((disabled_at IS NULL) AND (disabled_by_user_id IS NULL)) OR ((disabled_at IS NOT NULL) AND (disabled_by_user_id IS NOT NULL)))),
    CONSTRAINT ck_tenant_module_access_status_consistency CHECK ((((status = 'ENABLED'::core.module_access_status_enum) AND (disabled_at IS NULL)) OR ((status = 'DISABLED'::core.module_access_status_enum) AND (disabled_at IS NOT NULL))))
);

ALTER TABLE ONLY core.tenant_module_access FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_user_role_assignments; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.tenant_user_role_assignments (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    tenant_user_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    org_node_id uuid NOT NULL,
    role_id uuid NOT NULL,
    status core.user_role_assignment_status_enum NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    granted_by_user_id uuid,
    granted_by_user_type core.actor_user_type_enum,
    revoked_at timestamp with time zone,
    revoked_by_user_id uuid,
    revoked_by_user_type core.actor_user_type_enum,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_tenant_user_role_assignments_granted_by_actor_pair CHECK ((((granted_by_user_id IS NULL) AND (granted_by_user_type IS NULL)) OR ((granted_by_user_id IS NOT NULL) AND (granted_by_user_type IS NOT NULL)))),
    CONSTRAINT ck_tenant_user_role_assignments_revoked_by_actor_pair CHECK ((((revoked_by_user_id IS NULL) AND (revoked_by_user_type IS NULL)) OR ((revoked_by_user_id IS NOT NULL) AND (revoked_by_user_type IS NOT NULL)))),
    CONSTRAINT ck_tenant_user_role_assignments_revoked_consistency CHECK ((((status = 'INACTIVE'::core.user_role_assignment_status_enum) AND (revoked_at IS NOT NULL) AND (revoked_by_user_id IS NOT NULL) AND (revoked_by_user_type IS NOT NULL)) OR ((status = 'ACTIVE'::core.user_role_assignment_status_enum) AND (revoked_at IS NULL) AND (revoked_by_user_id IS NULL) AND (revoked_by_user_type IS NULL))))
);

ALTER TABLE ONLY core.tenant_user_role_assignments FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_users; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.tenant_users (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    tenant_id uuid NOT NULL,
    auth0_sub text,
    email text NOT NULL,
    full_name text NOT NULL,
    status core.tenant_user_status_enum DEFAULT 'INVITED'::core.tenant_user_status_enum NOT NULL,
    invited_at timestamp with time zone,
    invitation_accepted_at timestamp with time zone,
    suspended_at timestamp with time zone,
    suspended_by_user_id uuid,
    suspended_by_user_type core.actor_user_type_enum,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_user_id uuid,
    created_by_user_type core.actor_user_type_enum,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id uuid,
    updated_by_user_type core.actor_user_type_enum,
    CONSTRAINT ck_tenant_users_auth0_sub_consistency CHECK ((((status = 'INVITED'::core.tenant_user_status_enum) AND (auth0_sub IS NULL)) OR ((status = ANY (ARRAY['ACTIVE'::core.tenant_user_status_enum, 'SUSPENDED'::core.tenant_user_status_enum])) AND (auth0_sub IS NOT NULL)))),
    CONSTRAINT ck_tenant_users_created_by_actor_pair CHECK ((((created_by_user_id IS NULL) AND (created_by_user_type IS NULL)) OR ((created_by_user_id IS NOT NULL) AND (created_by_user_type IS NOT NULL)))),
    CONSTRAINT ck_tenant_users_email_format CHECK ((email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'::text)),
    CONSTRAINT ck_tenant_users_email_lowercase CHECK ((email = lower(email))),
    CONSTRAINT ck_tenant_users_full_name_length CHECK (((length(full_name) >= 1) AND (length(full_name) <= 200))),
    CONSTRAINT ck_tenant_users_invitation_accepted_consistency CHECK ((((status = 'INVITED'::core.tenant_user_status_enum) AND (invitation_accepted_at IS NULL)) OR ((status = ANY (ARRAY['ACTIVE'::core.tenant_user_status_enum, 'SUSPENDED'::core.tenant_user_status_enum])) AND (invitation_accepted_at IS NOT NULL)))),
    CONSTRAINT ck_tenant_users_suspended_actor_pair CHECK ((((suspended_by_user_id IS NULL) AND (suspended_by_user_type IS NULL)) OR ((suspended_by_user_id IS NOT NULL) AND (suspended_by_user_type IS NOT NULL)))),
    CONSTRAINT ck_tenant_users_suspended_consistency CHECK ((((status = 'SUSPENDED'::core.tenant_user_status_enum) AND (suspended_at IS NOT NULL) AND (suspended_by_user_id IS NOT NULL) AND (suspended_by_user_type IS NOT NULL)) OR ((status = ANY (ARRAY['INVITED'::core.tenant_user_status_enum, 'ACTIVE'::core.tenant_user_status_enum])) AND (suspended_at IS NULL) AND (suspended_by_user_id IS NULL) AND (suspended_by_user_type IS NULL)))),
    CONSTRAINT ck_tenant_users_updated_by_actor_pair CHECK ((((updated_by_user_id IS NULL) AND (updated_by_user_type IS NULL)) OR ((updated_by_user_id IS NOT NULL) AND (updated_by_user_type IS NOT NULL))))
);

ALTER TABLE ONLY core.tenant_users FORCE ROW LEVEL SECURITY;


--
-- Name: tenants; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.tenants (
    id uuid DEFAULT core.uuidv7() NOT NULL,
    name text NOT NULL,
    display_code text,
    country text,
    region core.tenant_region_enum NOT NULL,
    tier core.tenant_tier_enum,
    industry core.tenant_industry_enum,
    monthly_revenue_usd numeric(15,2),
    monthly_revenue_as_of_date date,
    number_of_stores integer,
    number_of_stores_as_of_date date,
    primary_contact_name text,
    contact_email text,
    status core.tenant_status_enum DEFAULT 'ONBOARDING'::core.tenant_status_enum NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_user_id uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id uuid,
    suspended_at timestamp with time zone,
    suspended_by_user_id uuid,
    terminated_at timestamp with time zone,
    terminated_by_user_id uuid,
    CONSTRAINT ck_tenants_contact_email_format CHECK (((contact_email IS NULL) OR (contact_email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'::text))),
    CONSTRAINT ck_tenants_contact_email_lowercase CHECK (((contact_email IS NULL) OR (contact_email = lower(contact_email)))),
    CONSTRAINT ck_tenants_country_format CHECK (((country IS NULL) OR (((length(country) >= 2) AND (length(country) <= 100)) AND (country ~ '[A-Za-z]'::text) AND (country !~ '^[[:space:]]*$'::text)))),
    CONSTRAINT ck_tenants_display_code_format CHECK (((display_code IS NULL) OR (display_code ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'::text))),
    CONSTRAINT ck_tenants_monthly_revenue_as_of_consistency CHECK ((((monthly_revenue_usd IS NULL) AND (monthly_revenue_as_of_date IS NULL)) OR ((monthly_revenue_usd IS NOT NULL) AND (monthly_revenue_as_of_date IS NOT NULL)))),
    CONSTRAINT ck_tenants_monthly_revenue_nonnegative CHECK (((monthly_revenue_usd IS NULL) OR (monthly_revenue_usd >= (0)::numeric))),
    CONSTRAINT ck_tenants_name_length CHECK (((length(name) >= 1) AND (length(name) <= 200))),
    CONSTRAINT ck_tenants_number_of_stores_as_of_consistency CHECK ((((number_of_stores IS NULL) AND (number_of_stores_as_of_date IS NULL)) OR ((number_of_stores IS NOT NULL) AND (number_of_stores_as_of_date IS NOT NULL)))),
    CONSTRAINT ck_tenants_number_of_stores_nonnegative CHECK (((number_of_stores IS NULL) OR (number_of_stores >= 0))),
    CONSTRAINT ck_tenants_primary_contact_name_length CHECK (((primary_contact_name IS NULL) OR ((length(primary_contact_name) >= 1) AND (length(primary_contact_name) <= 200)))),
    CONSTRAINT ck_tenants_suspended_consistency CHECK ((((status = 'SUSPENDED'::core.tenant_status_enum) AND (suspended_at IS NOT NULL) AND (suspended_by_user_id IS NOT NULL)) OR ((status <> 'SUSPENDED'::core.tenant_status_enum) AND (suspended_at IS NULL) AND (suspended_by_user_id IS NULL)))),
    CONSTRAINT ck_tenants_terminated_consistency CHECK ((((status = 'TERMINATED'::core.tenant_status_enum) AND (terminated_at IS NOT NULL) AND (terminated_by_user_id IS NOT NULL)) OR ((status <> 'TERMINATED'::core.tenant_status_enum) AND (terminated_at IS NULL) AND (terminated_by_user_id IS NULL))))
);

ALTER TABLE ONLY core.tenants FORCE ROW LEVEL SECURITY;


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: lookups pk_lookups; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.lookups
    ADD CONSTRAINT pk_lookups PRIMARY KEY (id);


--
-- Name: org_nodes pk_org_nodes; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.org_nodes
    ADD CONSTRAINT pk_org_nodes PRIMARY KEY (id);


--
-- Name: permissions pk_permissions; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.permissions
    ADD CONSTRAINT pk_permissions PRIMARY KEY (id);


--
-- Name: platform_activity_audit_logs pk_platform_activity_audit_logs; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.platform_activity_audit_logs
    ADD CONSTRAINT pk_platform_activity_audit_logs PRIMARY KEY (id);


--
-- Name: platform_user_role_assignments pk_platform_user_role_assignments; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.platform_user_role_assignments
    ADD CONSTRAINT pk_platform_user_role_assignments PRIMARY KEY (id);


--
-- Name: platform_users pk_platform_users; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.platform_users
    ADD CONSTRAINT pk_platform_users PRIMARY KEY (id);


--
-- Name: role_permissions pk_role_permissions; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.role_permissions
    ADD CONSTRAINT pk_role_permissions PRIMARY KEY (role_id, permission_id);


--
-- Name: roles pk_roles; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.roles
    ADD CONSTRAINT pk_roles PRIMARY KEY (id);


--
-- Name: stores pk_stores; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.stores
    ADD CONSTRAINT pk_stores PRIMARY KEY (id);


--
-- Name: tenant_activity_audit_logs pk_tenant_activity_audit_logs; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_activity_audit_logs
    ADD CONSTRAINT pk_tenant_activity_audit_logs PRIMARY KEY (id);


--
-- Name: tenant_module_access pk_tenant_module_access; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_module_access
    ADD CONSTRAINT pk_tenant_module_access PRIMARY KEY (id);


--
-- Name: tenant_user_role_assignments pk_tenant_user_role_assignments; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_user_role_assignments
    ADD CONSTRAINT pk_tenant_user_role_assignments PRIMARY KEY (id);


--
-- Name: tenant_users pk_tenant_users; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_users
    ADD CONSTRAINT pk_tenant_users PRIMARY KEY (id);


--
-- Name: tenants pk_tenants; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenants
    ADD CONSTRAINT pk_tenants PRIMARY KEY (id);


--
-- Name: lookups uq_lookups_list_name_code; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.lookups
    ADD CONSTRAINT uq_lookups_list_name_code UNIQUE (list_name, code);


--
-- Name: org_nodes uq_org_nodes_tenant_id; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.org_nodes
    ADD CONSTRAINT uq_org_nodes_tenant_id UNIQUE (tenant_id, id);


--
-- Name: permissions uq_permissions_code; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.permissions
    ADD CONSTRAINT uq_permissions_code UNIQUE (code);


--
-- Name: permissions uq_permissions_tuple; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.permissions
    ADD CONSTRAINT uq_permissions_tuple UNIQUE (module, resource, action, scope);


--
-- Name: roles uq_roles_code; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.roles
    ADD CONSTRAINT uq_roles_code UNIQUE (code);


--
-- Name: tenant_module_access uq_tenant_module_access_tenant_module; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_module_access
    ADD CONSTRAINT uq_tenant_module_access_tenant_module UNIQUE (tenant_id, module);


--
-- Name: tenant_users uq_tenant_users_tenant_id; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_users
    ADD CONSTRAINT uq_tenant_users_tenant_id UNIQUE (tenant_id, id);


--
-- Name: ix_lookups_list_name_active_order; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_lookups_list_name_active_order ON core.lookups USING btree (list_name, is_active, display_order, display_name) WHERE (is_active = true);


--
-- Name: ix_lookups_list_name_all; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_lookups_list_name_all ON core.lookups USING btree (list_name, code);


--
-- Name: ix_lookups_updated_at; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_lookups_updated_at ON core.lookups USING btree (updated_at DESC);


--
-- Name: ix_org_nodes_parent; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_org_nodes_parent ON core.org_nodes USING btree (parent_id);


--
-- Name: ix_org_nodes_path_gist; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_org_nodes_path_gist ON core.org_nodes USING gist (path);


--
-- Name: ix_org_nodes_tenant; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_org_nodes_tenant ON core.org_nodes USING btree (tenant_id);


--
-- Name: ix_org_nodes_tenant_type; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_org_nodes_tenant_type ON core.org_nodes USING btree (tenant_id, node_type);


--
-- Name: ix_platform_activity_audit_logs_failures; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_platform_activity_audit_logs_failures ON core.platform_activity_audit_logs USING btree (result_type) WHERE (result_type <> 'SUCCESS'::core.audit_result_type_enum);


--
-- Name: ix_platform_activity_audit_logs_timestamp_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_platform_activity_audit_logs_timestamp_id ON core.platform_activity_audit_logs USING btree ("timestamp" DESC, id DESC);


--
-- Name: ix_platform_user_role_assignments_platform_user; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_platform_user_role_assignments_platform_user ON core.platform_user_role_assignments USING btree (platform_user_id);


--
-- Name: ix_platform_user_role_assignments_platform_user_active; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_platform_user_role_assignments_platform_user_active ON core.platform_user_role_assignments USING btree (platform_user_id) WHERE (status = 'ACTIVE'::core.user_role_assignment_status_enum);


--
-- Name: ix_platform_user_role_assignments_role; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_platform_user_role_assignments_role ON core.platform_user_role_assignments USING btree (role_id);


--
-- Name: ix_platform_users_status; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_platform_users_status ON core.platform_users USING btree (status);


--
-- Name: ix_role_permissions_permission; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_role_permissions_permission ON core.role_permissions USING btree (permission_id);


--
-- Name: ix_roles_audience; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_roles_audience ON core.roles USING btree (audience);


--
-- Name: ix_roles_status; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_roles_status ON core.roles USING btree (status);


--
-- Name: ix_stores_tenant; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_stores_tenant ON core.stores USING btree (tenant_id);


--
-- Name: ix_stores_tenant_status; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_stores_tenant_status ON core.stores USING btree (tenant_id, status);


--
-- Name: ix_tenant_activity_audit_logs_failures; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenant_activity_audit_logs_failures ON core.tenant_activity_audit_logs USING btree (result_type) WHERE (result_type <> 'SUCCESS'::core.audit_result_type_enum);


--
-- Name: ix_tenant_activity_audit_logs_tenant_timestamp_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenant_activity_audit_logs_tenant_timestamp_id ON core.tenant_activity_audit_logs USING btree (tenant_id, "timestamp" DESC, id DESC);


--
-- Name: ix_tenant_activity_audit_logs_timestamp_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenant_activity_audit_logs_timestamp_id ON core.tenant_activity_audit_logs USING btree ("timestamp" DESC, id DESC);


--
-- Name: ix_tenant_module_access_tenant_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenant_module_access_tenant_id ON core.tenant_module_access USING btree (tenant_id);


--
-- Name: ix_tenant_user_role_assignments_role_org_node; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenant_user_role_assignments_role_org_node ON core.tenant_user_role_assignments USING btree (role_id, org_node_id);


--
-- Name: ix_tenant_user_role_assignments_tenant; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenant_user_role_assignments_tenant ON core.tenant_user_role_assignments USING btree (tenant_id);


--
-- Name: ix_tenant_user_role_assignments_tenant_user; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenant_user_role_assignments_tenant_user ON core.tenant_user_role_assignments USING btree (tenant_user_id);


--
-- Name: ix_tenant_user_role_assignments_tenant_user_active; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenant_user_role_assignments_tenant_user_active ON core.tenant_user_role_assignments USING btree (tenant_user_id) WHERE (status = 'ACTIVE'::core.user_role_assignment_status_enum);


--
-- Name: ix_tenant_users_tenant; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenant_users_tenant ON core.tenant_users USING btree (tenant_id);


--
-- Name: ix_tenant_users_tenant_status; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenant_users_tenant_status ON core.tenant_users USING btree (tenant_id, status);


--
-- Name: ix_tenants_region; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenants_region ON core.tenants USING btree (region);


--
-- Name: ix_tenants_status; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_tenants_status ON core.tenants USING btree (status);


--
-- Name: uq_org_nodes_tenant_code_lower; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX uq_org_nodes_tenant_code_lower ON core.org_nodes USING btree (tenant_id, lower(code));


--
-- Name: uq_platform_user_role_assignments_active; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX uq_platform_user_role_assignments_active ON core.platform_user_role_assignments USING btree (platform_user_id, role_id) WHERE (status = 'ACTIVE'::core.user_role_assignment_status_enum);


--
-- Name: uq_platform_users_auth0_sub; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX uq_platform_users_auth0_sub ON core.platform_users USING btree (auth0_sub) WHERE (auth0_sub IS NOT NULL);


--
-- Name: uq_platform_users_email; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX uq_platform_users_email ON core.platform_users USING btree (email);


--
-- Name: uq_stores_org_node_id; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX uq_stores_org_node_id ON core.stores USING btree (org_node_id) WHERE (org_node_id IS NOT NULL);


--
-- Name: uq_stores_tenant_store_code_lower; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX uq_stores_tenant_store_code_lower ON core.stores USING btree (tenant_id, lower(store_code)) WHERE (store_code IS NOT NULL);


--
-- Name: uq_tenant_user_role_assignments_active; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX uq_tenant_user_role_assignments_active ON core.tenant_user_role_assignments USING btree (tenant_user_id, role_id, org_node_id) WHERE (status = 'ACTIVE'::core.user_role_assignment_status_enum);


--
-- Name: uq_tenant_users_tenant_auth0_sub; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX uq_tenant_users_tenant_auth0_sub ON core.tenant_users USING btree (tenant_id, auth0_sub) WHERE (auth0_sub IS NOT NULL);


--
-- Name: uq_tenant_users_tenant_email; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX uq_tenant_users_tenant_email ON core.tenant_users USING btree (tenant_id, email);


--
-- Name: uq_tenants_display_code_lower; Type: INDEX; Schema: core; Owner: -
--

CREATE UNIQUE INDEX uq_tenants_display_code_lower ON core.tenants USING btree (lower(display_code)) WHERE (display_code IS NOT NULL);


--
-- Name: org_nodes tg_org_nodes_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_org_nodes_set_updated_at BEFORE UPDATE ON core.org_nodes FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: permissions tg_permissions_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_permissions_set_updated_at BEFORE UPDATE ON core.permissions FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: platform_user_role_assignments tg_platform_user_role_assignments_audience_check; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_platform_user_role_assignments_audience_check BEFORE INSERT OR UPDATE OF role_id ON core.platform_user_role_assignments FOR EACH ROW EXECUTE FUNCTION core.enforce_platform_role_audience();


--
-- Name: platform_user_role_assignments tg_platform_user_role_assignments_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_platform_user_role_assignments_set_updated_at BEFORE UPDATE ON core.platform_user_role_assignments FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: platform_users tg_platform_users_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_platform_users_set_updated_at BEFORE UPDATE ON core.platform_users FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: role_permissions tg_role_permissions_audience_scope_coherence; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_role_permissions_audience_scope_coherence BEFORE INSERT OR UPDATE OF role_id, permission_id ON core.role_permissions FOR EACH ROW EXECUTE FUNCTION core.enforce_role_audience_scope_coherence();


--
-- Name: role_permissions tg_role_permissions_protect_super_admin_override; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_role_permissions_protect_super_admin_override BEFORE DELETE ON core.role_permissions FOR EACH ROW EXECUTE FUNCTION core.protect_super_admin_override_global_grant();


--
-- Name: roles tg_roles_protect_super_admin; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_roles_protect_super_admin BEFORE DELETE OR UPDATE ON core.roles FOR EACH ROW EXECUTE FUNCTION core.protect_super_admin_role();


--
-- Name: roles tg_roles_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_roles_set_updated_at BEFORE UPDATE ON core.roles FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: stores tg_stores_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_stores_set_updated_at BEFORE UPDATE ON core.stores FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: tenant_module_access tg_tenant_module_access_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_tenant_module_access_set_updated_at BEFORE UPDATE ON core.tenant_module_access FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: tenant_user_role_assignments tg_tenant_user_role_assignments_audience_check; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_tenant_user_role_assignments_audience_check BEFORE INSERT OR UPDATE OF role_id ON core.tenant_user_role_assignments FOR EACH ROW EXECUTE FUNCTION core.enforce_tenant_role_audience();


--
-- Name: tenant_user_role_assignments tg_tenant_user_role_assignments_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_tenant_user_role_assignments_set_updated_at BEFORE UPDATE ON core.tenant_user_role_assignments FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: tenant_users tg_tenant_users_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_tenant_users_set_updated_at BEFORE UPDATE ON core.tenant_users FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: tenants tg_tenants_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER tg_tenants_set_updated_at BEFORE UPDATE ON core.tenants FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: lookups trg_lookups_set_updated_at; Type: TRIGGER; Schema: core; Owner: -
--

CREATE TRIGGER trg_lookups_set_updated_at BEFORE UPDATE ON core.lookups FOR EACH ROW EXECUTE FUNCTION core.set_updated_at_timestamp();


--
-- Name: org_nodes fk_org_nodes_parent_same_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.org_nodes
    ADD CONSTRAINT fk_org_nodes_parent_same_tenant FOREIGN KEY (tenant_id, parent_id) REFERENCES core.org_nodes(tenant_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: org_nodes fk_org_nodes_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.org_nodes
    ADD CONSTRAINT fk_org_nodes_tenant FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: platform_activity_audit_logs fk_platform_activity_audit_logs_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.platform_activity_audit_logs
    ADD CONSTRAINT fk_platform_activity_audit_logs_tenant FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: platform_user_role_assignments fk_platform_user_role_assignments_platform_user; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.platform_user_role_assignments
    ADD CONSTRAINT fk_platform_user_role_assignments_platform_user FOREIGN KEY (platform_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: platform_user_role_assignments fk_platform_user_role_assignments_role; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.platform_user_role_assignments
    ADD CONSTRAINT fk_platform_user_role_assignments_role FOREIGN KEY (role_id) REFERENCES core.roles(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: platform_users fk_platform_users_created_by; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.platform_users
    ADD CONSTRAINT fk_platform_users_created_by FOREIGN KEY (created_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: platform_users fk_platform_users_suspended_by; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.platform_users
    ADD CONSTRAINT fk_platform_users_suspended_by FOREIGN KEY (suspended_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: platform_users fk_platform_users_updated_by; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.platform_users
    ADD CONSTRAINT fk_platform_users_updated_by FOREIGN KEY (updated_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: role_permissions fk_role_permissions_permission; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.role_permissions
    ADD CONSTRAINT fk_role_permissions_permission FOREIGN KEY (permission_id) REFERENCES core.permissions(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: role_permissions fk_role_permissions_role; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.role_permissions
    ADD CONSTRAINT fk_role_permissions_role FOREIGN KEY (role_id) REFERENCES core.roles(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: stores fk_stores_org_node_same_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.stores
    ADD CONSTRAINT fk_stores_org_node_same_tenant FOREIGN KEY (tenant_id, org_node_id) REFERENCES core.org_nodes(tenant_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: stores fk_stores_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.stores
    ADD CONSTRAINT fk_stores_tenant FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_activity_audit_logs fk_tenant_activity_audit_logs_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_activity_audit_logs
    ADD CONSTRAINT fk_tenant_activity_audit_logs_tenant FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_module_access fk_tenant_module_access_created_by; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_module_access
    ADD CONSTRAINT fk_tenant_module_access_created_by FOREIGN KEY (created_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_module_access fk_tenant_module_access_disabled_by; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_module_access
    ADD CONSTRAINT fk_tenant_module_access_disabled_by FOREIGN KEY (disabled_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_module_access fk_tenant_module_access_enabled_by; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_module_access
    ADD CONSTRAINT fk_tenant_module_access_enabled_by FOREIGN KEY (enabled_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_module_access fk_tenant_module_access_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_module_access
    ADD CONSTRAINT fk_tenant_module_access_tenant FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_module_access fk_tenant_module_access_updated_by; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_module_access
    ADD CONSTRAINT fk_tenant_module_access_updated_by FOREIGN KEY (updated_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_user_role_assignments fk_tenant_user_role_assignments_org_node_same_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_user_role_assignments
    ADD CONSTRAINT fk_tenant_user_role_assignments_org_node_same_tenant FOREIGN KEY (tenant_id, org_node_id) REFERENCES core.org_nodes(tenant_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_user_role_assignments fk_tenant_user_role_assignments_role; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_user_role_assignments
    ADD CONSTRAINT fk_tenant_user_role_assignments_role FOREIGN KEY (role_id) REFERENCES core.roles(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_user_role_assignments fk_tenant_user_role_assignments_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_user_role_assignments
    ADD CONSTRAINT fk_tenant_user_role_assignments_tenant FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_user_role_assignments fk_tenant_user_role_assignments_tenant_user_same_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_user_role_assignments
    ADD CONSTRAINT fk_tenant_user_role_assignments_tenant_user_same_tenant FOREIGN KEY (tenant_id, tenant_user_id) REFERENCES core.tenant_users(tenant_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenant_users fk_tenant_users_tenant; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenant_users
    ADD CONSTRAINT fk_tenant_users_tenant FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenants fk_tenants_created_by_user; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenants
    ADD CONSTRAINT fk_tenants_created_by_user FOREIGN KEY (created_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenants fk_tenants_suspended_by_user; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenants
    ADD CONSTRAINT fk_tenants_suspended_by_user FOREIGN KEY (suspended_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenants fk_tenants_terminated_by_user; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenants
    ADD CONSTRAINT fk_tenants_terminated_by_user FOREIGN KEY (terminated_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: tenants fk_tenants_updated_by_user; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.tenants
    ADD CONSTRAINT fk_tenants_updated_by_user FOREIGN KEY (updated_by_user_id) REFERENCES core.platform_users(id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: org_nodes; Type: ROW SECURITY; Schema: core; Owner: -
--

ALTER TABLE core.org_nodes ENABLE ROW LEVEL SECURITY;

--
-- Name: org_nodes org_nodes_tenant_isolation; Type: POLICY; Schema: core; Owner: -
--

CREATE POLICY org_nodes_tenant_isolation ON core.org_nodes USING (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text))) WITH CHECK (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text)));


--
-- Name: stores; Type: ROW SECURITY; Schema: core; Owner: -
--

ALTER TABLE core.stores ENABLE ROW LEVEL SECURITY;

--
-- Name: stores stores_tenant_isolation; Type: POLICY; Schema: core; Owner: -
--

CREATE POLICY stores_tenant_isolation ON core.stores USING (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text))) WITH CHECK (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text)));


--
-- Name: tenant_activity_audit_logs; Type: ROW SECURITY; Schema: core; Owner: -
--

ALTER TABLE core.tenant_activity_audit_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_activity_audit_logs tenant_activity_audit_logs_tenant_isolation; Type: POLICY; Schema: core; Owner: -
--

CREATE POLICY tenant_activity_audit_logs_tenant_isolation ON core.tenant_activity_audit_logs USING (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text))) WITH CHECK (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text)));


--
-- Name: tenant_module_access; Type: ROW SECURITY; Schema: core; Owner: -
--

ALTER TABLE core.tenant_module_access ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_module_access tenant_module_access_tenant_isolation; Type: POLICY; Schema: core; Owner: -
--

CREATE POLICY tenant_module_access_tenant_isolation ON core.tenant_module_access USING (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text))) WITH CHECK (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text)));


--
-- Name: tenant_user_role_assignments; Type: ROW SECURITY; Schema: core; Owner: -
--

ALTER TABLE core.tenant_user_role_assignments ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_user_role_assignments tenant_user_role_assignments_tenant_isolation; Type: POLICY; Schema: core; Owner: -
--

CREATE POLICY tenant_user_role_assignments_tenant_isolation ON core.tenant_user_role_assignments USING (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text))) WITH CHECK (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text)));


--
-- Name: tenant_users; Type: ROW SECURITY; Schema: core; Owner: -
--

ALTER TABLE core.tenant_users ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_users tenant_users_tenant_isolation; Type: POLICY; Schema: core; Owner: -
--

CREATE POLICY tenant_users_tenant_isolation ON core.tenant_users USING (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text))) WITH CHECK (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text)));


--
-- Name: tenants; Type: ROW SECURITY; Schema: core; Owner: -
--

ALTER TABLE core.tenants ENABLE ROW LEVEL SECURITY;

--
-- Name: tenants tenants_self_access; Type: POLICY; Schema: core; Owner: -
--

CREATE POLICY tenants_self_access ON core.tenants USING (((id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text))) WITH CHECK (((id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) OR (current_setting('app.user_type'::text, true) = 'PLATFORM'::text)));


--
-- PostgreSQL database dump complete
--

\unrestrict Ed2GbTUvH9lX5fRFeINgivVKtalJ4zbhpkHjMkOEB00QQyFPqhzHapxhXawsrOu

