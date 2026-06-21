"""Per-sheet column mappings (source of truth for Excel <-> DB).

Each Excel column has one of three roles:

  - DB_COLUMN: maps to a real column in the DDL; passed verbatim to
    INSERT (after 'NULL'-string translation, which excel_reader.py
    already does).
  - FK_REF: an FK; substituted via UUIDMapper.lookup() before INSERT.
    The fk_target names the sheet whose mapper to consult.
  - HELPER: not in the DDL; skipped entirely. Includes _-prefixed
    natural-key columns (_key, _tenant_key, _parent_key, etc.) and
    yellow FYI columns (_legal_name_FYI).

If a per-sheet loader encounters an Excel column NOT in the mapping,
``validate_columns()`` raises ``UnknownColumnError`` with the column
name, sheet name, and a hint to either add the column to the mapping
or remove it from the Excel. Drift detection.

Adding a new sheet or new column to an existing sheet: edit this file
first, then update the loader. Order matters — get the mapping right,
then the loader does the right thing automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class ColumnRole(Enum):
    DB_COLUMN = "db_column"   # Real DB column; INSERT verbatim.
    FK_REF = "fk_ref"         # FK; resolve via UUIDMapper before INSERT.
    HELPER = "helper"         # Skip; not a DB column.


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    role: ColumnRole
    fk_target: str | None = None  # FK_REF only; names the source sheet.

    def __post_init__(self) -> None:
        if self.role is ColumnRole.FK_REF and self.fk_target is None:
            raise ValueError(
                f"FK_REF column '{self.name}' must have fk_target"
            )
        if (
            self.role is not ColumnRole.FK_REF
            and self.fk_target is not None
        ):
            raise ValueError(
                f"Non-FK_REF column '{self.name}' must not have "
                f"fk_target"
            )


SheetMapping = list[ColumnSpec]


def db(name: str) -> ColumnSpec:
    return ColumnSpec(name=name, role=ColumnRole.DB_COLUMN)


def fk(name: str, target: str) -> ColumnSpec:
    return ColumnSpec(
        name=name, role=ColumnRole.FK_REF, fk_target=target
    )


def helper(name: str) -> ColumnSpec:
    return ColumnSpec(name=name, role=ColumnRole.HELPER)


# ---- Per-sheet mappings ----

# platform_users: self-referential audit. created_by/updated_by/
# suspended_by all FK_REF to platform_users itself. Two-phase loader
# bypasses _base.insert_and_register.
PLATFORM_USERS: Final[SheetMapping] = [
    db("id"),
    db("auth0_sub"),
    db("email"),
    db("full_name"),
    db("status"),
    db("invited_at"),
    db("invitation_accepted_at"),
    db("suspended_at"),
    fk("suspended_by_user_id", "platform_users"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    helper("_role_label"),
]

# tenants: Pattern (a) audit-actors — typed FK direct to platform_users,
# no *_by_user_type columns.
TENANTS: Final[SheetMapping] = [
    db("id"),
    db("name"),
    db("display_code"),
    db("country"),
    db("region"),
    db("tier"),
    db("industry"),
    db("monthly_revenue_usd"),
    db("monthly_revenue_as_of_date"),
    db("number_of_stores"),
    db("number_of_stores_as_of_date"),
    db("primary_contact_name"),
    db("contact_email"),
    db("status"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    db("suspended_at"),
    fk("suspended_by_user_id", "platform_users"),
    db("terminated_at"),
    fk("terminated_by_user_id", "platform_users"),
    helper("_legal_name_FYI"),
]

# org_nodes: Pattern (b) audit-actors. The DDL allows *_by_user_id to
# point to either platform_users or tenant_users depending on
# *_by_user_type. The seed has all PLATFORM audit-actors (Anjali); the
# loader looks up only in platform_users. If a future seed row uses
# user_type='TENANT', the load FAILS with UnresolvedFKError — intentional,
# surfaces data drift loudly.
ORG_NODES: Final[SheetMapping] = [
    helper("_key"),
    helper("_tenant_key"),
    helper("_parent_key"),
    db("id"),
    fk("tenant_id", "tenants"),
    fk("parent_id", "org_nodes"),  # self-reference; resolves within sheet
    db("path"),                    # ltree pre-computed; insert verbatim
    db("node_type"),
    db("name"),
    db("code"),
    db("status"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("created_by_user_type"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    db("updated_by_user_type"),
    db("archived_at"),
    fk("archived_by_user_id", "platform_users"),
    db("archived_by_user_type"),
]

# stores: Pattern (b) audit-actors. tenant_id and org_node_id are FK refs.
STORES: Final[SheetMapping] = [
    helper("_org_node_key"),
    helper("_tenant_key"),
    db("id"),
    fk("tenant_id", "tenants"),
    fk("org_node_id", "org_nodes"),
    db("name"),
    db("store_code"),
    db("country"),
    db("timezone"),
    db("address"),
    db("latitude"),
    db("longitude"),
    db("currency"),
    db("tax_treatment"),
    db("status"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("created_by_user_type"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    db("updated_by_user_type"),
]

# tenant_users: Pattern (b) audit-actors. Seed audit-actors are all
# PLATFORM (Anjali); loader resolves via platform_users.
TENANT_USERS: Final[SheetMapping] = [
    helper("_key"),
    helper("_tenant_key"),
    db("id"),
    fk("tenant_id", "tenants"),
    db("auth0_sub"),
    db("email"),
    db("full_name"),
    db("status"),
    db("invited_at"),
    db("invitation_accepted_at"),
    db("suspended_at"),
    fk("suspended_by_user_id", "platform_users"),
    db("suspended_by_user_type"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("created_by_user_type"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    db("updated_by_user_type"),
]

# roles: Pattern (b). is_system is bool.
ROLES: Final[SheetMapping] = [
    helper("_key"),
    db("id"),
    db("name"),
    db("code"),
    db("description"),
    db("audience"),
    db("status"),
    db("is_system"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("created_by_user_type"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    db("updated_by_user_type"),
    db("archived_at"),
    fk("archived_by_user_id", "platform_users"),
    db("archived_by_user_type"),
]

# permissions: minimal audit. permissions are seed-level reference data
# with no per-row attribution.
PERMISSIONS: Final[SheetMapping] = [
    helper("_key"),
    db("id"),
    db("module"),
    db("resource"),
    db("action"),
    db("scope"),
    db("code"),
    db("description"),
    db("created_at"),
    db("updated_at"),
]

# role_permissions: pure mapping table. _permission_code is helper for
# human reading; FK substitution uses _role_key and _permission_key.
ROLE_PERMISSIONS: Final[SheetMapping] = [
    helper("_role_key"),
    helper("_permission_key"),
    helper("_permission_code"),
    fk("role_id", "roles"),
    fk("permission_id", "permissions"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("created_by_user_type"),
]

# user_role_assignments: post Step 6.8.1 split. Excel still has BOTH
# platform_user_id and tenant_user_id columns (one populated per row,
# the other NULL — same shape as before). The loader at
# loaders/user_role_assignments.py inspects which is set and routes
# to either platform_user_role_assignments (no RLS, no tenant_id /
# org_node_id columns) or tenant_user_role_assignments (RLS+FORCE,
# composite FKs). The DB-layer XOR CHECK is gone (the table is gone);
# physical-table separation enforces audience exclusivity, with
# audience-check triggers as the role-side guarantee.
USER_ROLE_ASSIGNMENTS: Final[SheetMapping] = [
    helper("_role_key"),
    helper("_org_node_key"),
    db("id"),
    fk("platform_user_id", "platform_users"),
    fk("tenant_user_id", "tenant_users"),
    fk("role_id", "roles"),
    fk("tenant_id", "tenants"),
    fk("org_node_id", "org_nodes"),
    db("status"),
    db("granted_at"),
    fk("granted_by_user_id", "platform_users"),
    db("granted_by_user_type"),
    db("revoked_at"),
    fk("revoked_by_user_id", "platform_users"),
    db("revoked_by_user_type"),
    db("updated_at"),
]

# tenant_module_access: Pattern (a) audit-actors (PLATFORM-only managed).
TENANT_MODULE_ACCESS: Final[SheetMapping] = [
    helper("_tenant_key"),
    helper("_tenant_name"),
    db("id"),
    fk("tenant_id", "tenants"),
    db("module"),
    db("status"),
    db("enabled_at"),
]


# Master mapping. All loadable sheets present; audit_logs is excluded
# (no DDL — Step 6.2 territory).
SHEET_MAPPINGS: Final[dict[str, SheetMapping]] = {
    "platform_users": PLATFORM_USERS,
    "tenants": TENANTS,
    "org_nodes": ORG_NODES,
    "stores": STORES,
    "tenant_users": TENANT_USERS,
    "roles": ROLES,
    "permissions": PERMISSIONS,
    "role_permissions": ROLE_PERMISSIONS,
    "user_role_assignments": USER_ROLE_ASSIGNMENTS,
    "tenant_module_access": TENANT_MODULE_ACCESS,
}


class UnknownColumnError(Exception):
    """Raised on column drift between Excel and SHEET_MAPPINGS."""


def validate_columns(
    sheet_name: str, excel_headers: list[str]
) -> None:
    """Verify every Excel column is in the mapping. Raise on drift.

    Called by per-sheet loaders before INSERT. Catches: typo'd
    columns, new helper columns missing the `_` prefix, schema
    additions not reflected in the mapping yet.
    """
    if sheet_name not in SHEET_MAPPINGS:
        raise UnknownColumnError(
            f"sheet '{sheet_name}' has no mapping in SHEET_MAPPINGS"
        )
    known = {spec.name for spec in SHEET_MAPPINGS[sheet_name]}
    unknown = [h for h in excel_headers if h is not None and h not in known]
    if unknown:
        raise UnknownColumnError(
            f"unknown columns on sheet '{sheet_name}': {unknown}. "
            f"Either add them to column_mappings.py or remove them "
            f"from the Excel."
        )


def excel_columns_for_db_insert(
    sheet_name: str,
) -> list[ColumnSpec]:
    """Return only the DB_COLUMN and FK_REF specs for a sheet, in order.

    Loader iterates this list to build the row dict for INSERT.
    HELPER columns are filtered out.
    """
    return [
        spec
        for spec in SHEET_MAPPINGS[sheet_name]
        if spec.role is not ColumnRole.HELPER
    ]
