"""Unit tests for `src/admin_backend/audit/emit.py`.

No database; the AsyncSession is a mock that captures `add()` calls
so the routing decision is observable without running INSERT. The
SQL-side behaviour is exercised end-to-end in
`tests/integration/test_audit_emission_tenants.py` and
`tests/integration/test_audit_emission_failures.py`.

Six tests:

- AE1 : `_build_row` with `route_to_platform=False` and tenant_id
        set builds a `TenantActivityAuditLog` instance.
- AE2 : `_build_row` with `route_to_platform=True` and tenant_id set
        builds a `PlatformActivityAuditLog` instance with tenant_id
        populated (tenant-creation success).
- AE3 : `_build_row` with `route_to_platform=False` and tenant_id
        None builds a `PlatformActivityAuditLog` with tenant_id NULL.
- AE4 : `_actor_type_from_auth` maps PLATFORM / TENANT correctly.
- AE5 : `_label_for_action` returns expected labels for all 4
        action codes.
- AE6 : The details builders produce dicts matching the design doc
        Failure-row payload shapes table (one assertion per builder).

LOAD-BEARING: AE1, AE2, AE3 (routing-decision correctness drives
every downstream emission test; without these, audit emission could
silently route to the wrong table).
"""
from __future__ import annotations

import uuid

import pytest

from admin_backend.audit.emit import (
    _actor_type_from_auth,
    _build_row,
    _label_for_action,
    _label_for_resource_type,
    _qualifier_for_conflict,
    build_conflict_details,
    build_integrity_violation_details,
    build_internal_error_details,
    build_permission_denied_details,
    build_success_details_for_create,
    build_success_details_for_transition,
    build_success_details_for_update,
    build_validation_failed_details,
    compose_conflict_result_label,
)
from admin_backend.auth.context import AuthContext
from admin_backend.models.audit_log import (
    AuditResultType,
    PlatformActivityAuditLog,
    TenantActivityAuditLog,
)
from admin_backend.models.tenant_user import ActorUserType


def _platform_auth() -> AuthContext:
    return AuthContext(  # type: ignore[call-arg]
        sub="auth0|unit-test",
        iss="https://stub-issuer.example.com",
        aud="ithina-admin-backend",
        exp=2_000_000_000,
        email="anjali@ithina.com",
        user_id=uuid.uuid4(),
        tenant_id=None,
        user_type="PLATFORM",
    )


def _tenant_auth(tenant_id: uuid.UUID) -> AuthContext:
    return AuthContext(  # type: ignore[call-arg]
        sub="auth0|unit-test-tenant",
        iss="https://stub-issuer.example.com",
        aud="ithina-admin-backend",
        exp=2_000_000_000,
        email="owner@example.com",
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_type="TENANT",
    )


# ---------------------------------------------------------------------------
# AE1 / AE2 / AE3 : routing decisions (LOAD-BEARING)
# ---------------------------------------------------------------------------


def test_ae1_route_normal_with_tenant_id_builds_tenant_table_row() -> None:
    """LOAD-BEARING: tenant_id set + route_to_platform=False -> tenant table."""
    tenant_id = uuid.uuid4()
    row = _build_row(
        auth=_platform_auth(),
        action="UPDATE",
        action_label=None,
        resource_type="TENANT",
        resource_id=tenant_id,
        resource_label="Buc-ee's",
        resource_subtype=None,
        result_type=AuditResultType.SUCCESS,
        result_label=None,
        details={"before": {"name": "Old"}, "after": {"name": "Buc-ee's"}},
        tenant_id=tenant_id,
        tenant_name="Buc-ee's",
        request_id=uuid.uuid4(),
        route_to_platform=False,
        actor_organization_name="Buc-ee's",
        actor_roles="Owner",
    )
    assert isinstance(row, TenantActivityAuditLog)
    assert row.tenant_id == tenant_id
    assert row.tenant_name == "Buc-ee's"
    assert row.action == "UPDATE"
    # Step 6.16.7 LD8 : "Updated" -> "Edited".
    assert row.action_label == "Edited"
    # Step 6.16.7 LD13 : new columns populated on the ORM row.
    assert row.actor_organization_name == "Buc-ee's"
    assert row.actor_roles == "Owner"
    assert row.resource_subtype is None


def test_ae2_route_to_platform_with_tenant_id_builds_platform_row() -> None:
    """LOAD-BEARING: tenant-creation success exception (route_to_platform=True)."""
    tenant_id = uuid.uuid4()
    row = _build_row(
        auth=_platform_auth(),
        action="CREATE",
        action_label=None,
        resource_type="TENANT",
        resource_id=tenant_id,
        resource_label="New Co",
        resource_subtype=None,
        result_type=AuditResultType.SUCCESS,
        result_label=None,
        details={"snapshot": {"name": "New Co"}},
        tenant_id=tenant_id,
        tenant_name="New Co",
        request_id=uuid.uuid4(),
        route_to_platform=True,
        actor_organization_name="Platform-Ithina",
        actor_roles="Super Admin",
    )
    assert isinstance(row, PlatformActivityAuditLog)
    # Named exception: tenant_id IS populated on the platform table.
    assert row.tenant_id == tenant_id
    assert row.tenant_name == "New Co"
    assert row.actor_organization_name == "Platform-Ithina"


def test_ae3_route_normal_with_no_tenant_id_builds_platform_null_row() -> None:
    """LOAD-BEARING: platform-scope action (tenant_id None) -> platform row."""
    row = _build_row(
        auth=_platform_auth(),
        action="GRANT",
        action_label=None,
        resource_type="ROLE_ASSIGNMENT",
        resource_id=uuid.uuid4(),
        resource_label="ops grant",
        resource_subtype=None,
        result_type=AuditResultType.SUCCESS,
        result_label=None,
        details={},
        tenant_id=None,
        tenant_name=None,
        request_id=uuid.uuid4(),
        route_to_platform=False,
        actor_organization_name="Platform-Ithina",
        actor_roles="-",
    )
    assert isinstance(row, PlatformActivityAuditLog)
    assert row.tenant_id is None
    assert row.tenant_name is None


# ---------------------------------------------------------------------------
# AE4 : actor_type mapping
# ---------------------------------------------------------------------------


def test_ae4_actor_type_from_auth_maps_correctly() -> None:
    assert _actor_type_from_auth("PLATFORM") is ActorUserType.PLATFORM
    assert _actor_type_from_auth("TENANT") is ActorUserType.TENANT
    with pytest.raises(ValueError):
        _actor_type_from_auth("OTHER")


# ---------------------------------------------------------------------------
# AE5 : action -> label resolution
# ---------------------------------------------------------------------------


def test_ae5_label_for_action_covers_v0_vocabulary() -> None:
    assert _label_for_action("CREATE") == "Created"
    # Step 6.16.7 LD8 : "Updated" -> "Edited".
    assert _label_for_action("UPDATE") == "Edited"
    assert _label_for_action("SUSPEND") == "Suspended"
    assert _label_for_action("ACTIVATE") == "Activated"
    # Unknown action falls back to the action code itself (defensive).
    assert _label_for_action("ZAP") == "ZAP"


# ---------------------------------------------------------------------------
# AE6 : details builders produce design-doc-shaped dicts
# ---------------------------------------------------------------------------


def test_ae6_details_builders_match_design_doc_shapes() -> None:
    """One assertion per builder verifying the design-doc payload shape."""
    # SUCCESS - CREATE shape: {"snapshot": {...}}
    create_d = build_success_details_for_create({"name": "Foo"})
    assert create_d == {"snapshot": {"name": "Foo"}}

    # SUCCESS - UPDATE shape: {"before": {...}, "after": {...}}
    update_d = build_success_details_for_update(
        {"name": "Old"}, {"name": "New"}
    )
    assert update_d == {"before": {"name": "Old"}, "after": {"name": "New"}}

    # SUCCESS - TRANSITION shape: status pair
    trans_d = build_success_details_for_transition("ACTIVE", "SUSPENDED")
    assert trans_d == {
        "before": {"status": "ACTIVE"},
        "after": {"status": "SUSPENDED"},
    }

    # PERMISSION_DENIED shape
    pd = build_permission_denied_details(
        required_permission="ADMIN.TENANTS.CONFIGURE.GLOBAL",
        caller_audience="TENANT",
        caller_roles=["OWNER"],
    )
    assert pd == {
        "required_permission": "ADMIN.TENANTS.CONFIGURE.GLOBAL",
        "caller_audience": "TENANT",
        "caller_roles": ["OWNER"],
    }

    # VALIDATION_FAILED shape (no submitted values)
    vf = build_validation_failed_details(
        validation_errors=[
            {"field": "name", "error_message": "required"},
        ]
    )
    assert vf == {
        "validation_errors": [
            {"field": "name", "error_message": "required"}
        ]
    }

    # CONFLICT shape
    cf = build_conflict_details(
        constraint="DUPLICATE_TENANT_NAME",
        field="name",
        value="Buc-ee's",
    )
    assert cf == {
        "constraint": "DUPLICATE_TENANT_NAME",
        "field": "name",
        "value": "Buc-ee's",
    }

    # INTEGRITY_VIOLATION shape
    iv = build_integrity_violation_details(constraint="fk_some_table")
    assert iv == {"constraint": "fk_some_table"}

    # INTERNAL_ERROR shape
    ie = build_internal_error_details(
        error_class="AppRolePrivilegeError",
        sanitised_message="An internal error occurred",
    )
    assert ie == {
        "error_class": "AppRolePrivilegeError",
        "sanitised_message": "An internal error occurred",
    }


# ---------------------------------------------------------------------------
# AE7 : CREATE-shape success builder with frozen-label roles (LOAD-BEARING)
# ---------------------------------------------------------------------------


def test_ae7_create_builder_includes_roles_list_with_frozen_labels() -> None:
    """LOAD-BEARING: tenant-users CREATE payload carries roles[] with the
    4 frozen-label fields per LD9 (role_id, role_name, org_node_id,
    org_node_name). Each item's labels are snapshotted at write time.
    """
    role_id = uuid.uuid4()
    org_node_id = uuid.uuid4()
    user_id = uuid.uuid4()
    snapshot = {
        "id": user_id,
        "email": "ada@example.com",
        "full_name": "Ada Lovelace",
        "status": "INVITED",
    }
    roles = [
        {
            "role_id": str(role_id),
            "role_name": "OWNER",
            "org_node_id": str(org_node_id),
            "org_node_name": "Buc-ee's HQ",
        }
    ]
    payload = build_success_details_for_create(snapshot, roles=roles)

    assert "snapshot" in payload
    assert payload["snapshot"]["email"] == "ada@example.com"
    assert payload["snapshot"]["full_name"] == "Ada Lovelace"
    assert payload["snapshot"]["status"] == "INVITED"
    # Roles list with frozen labels per LD9.
    inner_roles = payload["snapshot"]["roles"]
    assert isinstance(inner_roles, list) and len(inner_roles) == 1
    item = inner_roles[0]
    assert item["role_id"] == str(role_id)
    assert item["role_name"] == "OWNER"
    assert item["org_node_id"] == str(org_node_id)
    assert item["org_node_name"] == "Buc-ee's HQ"

    # Backwards-compatibility: omitting ``roles`` produces the bare
    # snapshot shape (the 6.16.2 callers' contract; AE6 verifies).
    bare = build_success_details_for_create({"name": "Foo"})
    assert "roles" not in bare["snapshot"]


# ---------------------------------------------------------------------------
# AE8 : UPDATE-shape success builder with before/after roles or permissions
# ---------------------------------------------------------------------------


def test_ae8_update_builder_carries_before_after_role_or_permission_lists() -> None:
    """UPDATE payload carries the full before+after role list when role
    diff fired (tenant-users PATCH) OR the full before+after permission
    list when permission diff fired (roles PATCH). Per Phase 1 Q1 both
    halves are full lists, not diffs.
    """
    role_a = uuid.uuid4()
    role_b = uuid.uuid4()
    on_a = uuid.uuid4()
    on_b = uuid.uuid4()

    # Roles diff case (tenant-users PATCH shape).
    before_roles = [
        {
            "role_id": str(role_a),
            "role_name": "OWNER",
            "org_node_id": str(on_a),
            "org_node_name": "HQ",
        }
    ]
    after_roles = [
        {
            "role_id": str(role_b),
            "role_name": "STORE_MANAGER",
            "org_node_id": str(on_b),
            "org_node_name": "Store 1",
        }
    ]
    pu = build_success_details_for_update(
        {"full_name": "Ada"},
        {"full_name": "Ada Lovelace"},
        before_roles=before_roles,
        after_roles=after_roles,
    )
    assert pu["before"]["full_name"] == "Ada"
    assert pu["after"]["full_name"] == "Ada Lovelace"
    assert pu["before"]["roles"] == before_roles
    assert pu["after"]["roles"] == after_roles

    # Permissions diff case (roles PATCH shape).
    perm_x = uuid.uuid4()
    perm_y = uuid.uuid4()
    before_perms = [
        {
            "permission_id": str(perm_x),
            "permission_code": "ADMIN.USERS.VIEW.TENANT",
        }
    ]
    after_perms = [
        {
            "permission_id": str(perm_y),
            "permission_code": "ADMIN.USERS.CONFIGURE.TENANT",
        }
    ]
    pp = build_success_details_for_update(
        {"description": "Old desc"},
        {"description": "New desc"},
        before_permissions=before_perms,
        after_permissions=after_perms,
    )
    assert pp["before"]["description"] == "Old desc"
    assert pp["after"]["description"] == "New desc"
    assert pp["before"]["permissions"] == before_perms
    assert pp["after"]["permissions"] == after_perms

    # Backwards-compatibility: omitting both keeps the bare shape
    # (the 6.16.2 callers' contract).
    bare = build_success_details_for_update(
        {"name": "Old"}, {"name": "New"}
    )
    assert bare == {"before": {"name": "Old"}, "after": {"name": "New"}}


# ---------------------------------------------------------------------------
# AE9 : optional sub-keys (denial_reason + invariant) per LD11 / LD12
# (LOAD-BEARING : the optional-sub-key convention is a public payload
# contract)
# ---------------------------------------------------------------------------


def test_ae9_optional_sub_keys_for_denied_and_invariant() -> None:
    """LOAD-BEARING: PERMISSION_DENIED carries optional ``denial_reason``
    when a handler-side guard produced the 403 (LD11). INTERNAL_ERROR
    carries optional ``invariant`` when a Layer 2 tripwire fired (LD12).
    Standard sub-keys remain present in both cases.
    """
    # PERMISSION_DENIED with denial_reason.
    pd = build_permission_denied_details(
        required_permission="ADMIN.USERS.CONFIGURE.TENANT",
        caller_audience="TENANT",
        caller_roles=["OWNER"],
        denial_reason="SELF_EDIT_FORBIDDEN",
    )
    assert pd == {
        "required_permission": "ADMIN.USERS.CONFIGURE.TENANT",
        "caller_audience": "TENANT",
        "caller_roles": ["OWNER"],
        "denial_reason": "SELF_EDIT_FORBIDDEN",
    }

    # PERMISSION_DENIED WITHOUT denial_reason : the 6.16.2 callers'
    # contract is unchanged (standard sub-keys only).
    pd_std = build_permission_denied_details(
        required_permission="ADMIN.TENANTS.CONFIGURE.GLOBAL",
        caller_audience="TENANT",
        caller_roles=["OWNER"],
    )
    assert "denial_reason" not in pd_std

    # INTERNAL_ERROR with invariant.
    ie = build_internal_error_details(
        error_class="InternalInvariantViolationError",
        sanitised_message="An internal error occurred",
        invariant="OVERRIDE_GLOBAL_HOLDER_PRESERVATION",
    )
    assert ie == {
        "error_class": "InternalInvariantViolationError",
        "sanitised_message": "An internal error occurred",
        "invariant": "OVERRIDE_GLOBAL_HOLDER_PRESERVATION",
    }

    # INTERNAL_ERROR WITHOUT invariant : standard shape preserved.
    ie_std = build_internal_error_details(error_class="AppRolePrivilegeError")
    assert "invariant" not in ie_std


# ---------------------------------------------------------------------------
# AE10 : Step 6.16.5 — CREATE-shape snapshot carries new optional sub-keys
# without explicit builder kwargs (callers compose the dict directly).
# ---------------------------------------------------------------------------


def test_ae10_create_snapshot_includes_atomic_and_parent_name_when_supplied() -> None:
    """LD5 + LD6: org-tree CREATE snapshot carries
    ``parent_org_node_name`` so the auditor sees where the node was
    added; stores CREATE snapshot carries
    ``org_node_created_atomically`` so the auditor sees the paired
    org_node was newly created (always true in v0 per FN-AB-68).

    The builder accepts a free-shape dict; the caller composes the
    snapshot with whichever keys are relevant. This test verifies the
    new keys flow through ``_json_safe`` cleanly without coercion.
    """
    parent_id = uuid.uuid4()
    node_id = uuid.uuid4()
    org_tree_snapshot = {
        "id": node_id,
        "name": "Buc-ee's Region 1",
        "code": "REG1",
        "node_type": "REGION",
        "path": "buc_ees.us.reg1",
        "parent_id": parent_id,
        "parent_org_node_name": "Buc-ee's US",
        "status": "ACTIVE",
    }
    payload = build_success_details_for_create(org_tree_snapshot)
    assert payload["snapshot"]["parent_org_node_name"] == "Buc-ee's US"
    # UUID coercion via _json_safe.
    assert payload["snapshot"]["id"] == str(node_id)
    assert payload["snapshot"]["parent_id"] == str(parent_id)

    # Stores CREATE shape: org_node_created_atomically=True per LD6.
    store_id = uuid.uuid4()
    paired_node_id = uuid.uuid4()
    store_snapshot = {
        "id": store_id,
        "name": "Buc-ee's Houston #1",
        "store_code": "BUC-HOU-001",
        "country": "United States",
        "timezone": "America/Chicago",
        "currency": "USD",
        "tax_treatment": "EXCLUSIVE",
        "status": "ACTIVE",
        "org_node_id": paired_node_id,
        "org_node_name": "Buc-ee's Houston #1",
        "org_node_created_atomically": True,
    }
    payload = build_success_details_for_create(store_snapshot)
    assert payload["snapshot"]["org_node_created_atomically"] is True
    assert payload["snapshot"]["org_node_id"] == str(paired_node_id)

    # Omitting either sub-key produces a snapshot without it (the
    # builder neither adds nor strips keys).
    bare = build_success_details_for_create({"name": "Foo"})
    assert "parent_org_node_name" not in bare["snapshot"]
    assert "org_node_created_atomically" not in bare["snapshot"]


# ---------------------------------------------------------------------------
# AE11 : Step 6.16.5 LD3 — stores set-status per-target action label
# dispatch (LOAD-BEARING : the user-facing action-label contract).
# ---------------------------------------------------------------------------


def test_ae11_label_for_action_covers_step_6_16_5_vocabulary() -> None:
    """LOAD-BEARING: stores set-status per-target action codes and the
    module-access ENABLE / DISABLE codes resolve to the locked LD3
    labels.

    OPEN_SOFT is reserved for ``target=OPENING`` per FN-AB-68 (no
    transition cell currently produces it; label stays in vocabulary
    for D-31 append-only stability).
    """
    # Stores set-status per-target codes (LD3).
    assert _label_for_action("OPEN_SOFT") == "Soft-opened"
    assert _label_for_action("ACTIVATE") == "Activated"
    assert _label_for_action("CLOSE") == "Closed"
    assert _label_for_action("DEACTIVATE") == "Deactivated"

    # Module-access ENABLE / DISABLE labels.
    assert _label_for_action("ENABLE") == "Enabled"
    assert _label_for_action("DISABLE") == "Disabled"

    # Failure-path SET_STATUS fallback. Step 6.16.7 LD8 :
    # "Status change" -> "Set status".
    assert _label_for_action("SET_STATUS") == "Set status"


# ---------------------------------------------------------------------------
# Step 6.16.7 unit tests : LD12 + LD9 + LD8 + helpers
# ---------------------------------------------------------------------------


def test_ae_n3_label_for_resource_type_non_org_node() -> None:
    """LD12: non-ORG_NODE resource_types map to the locked Type labels."""
    assert _label_for_resource_type("TENANT", None) == "Tenant"
    assert _label_for_resource_type("TENANT_USER", None) == "User"
    assert _label_for_resource_type("ROLE", None) == "Role"
    assert _label_for_resource_type("MODULE_ACCESS", None) == "Module"
    assert _label_for_resource_type("STORE", None) == "Store"
    # Subtype is ignored for non-ORG_NODE rows.
    assert _label_for_resource_type("TENANT", "REGION") == "Tenant"
    # Unknown resource_type falls back to the raw value (defensive).
    assert _label_for_resource_type("ZZZ", None) == "ZZZ"


def test_ae_n4_label_for_resource_type_org_node_subtypes() -> None:
    """LD12: ORG_NODE rows dispatch on resource_subtype for the Type label.

    Covers each of the 7 org_node_type_enum values.
    """
    assert _label_for_resource_type("ORG_NODE", "TENANT") == "Tenant root"
    assert _label_for_resource_type("ORG_NODE", "BUSINESS_UNIT") == "Business unit"
    assert _label_for_resource_type("ORG_NODE", "HQ") == "HQ"
    assert _label_for_resource_type("ORG_NODE", "COUNTRY") == "Country"
    assert _label_for_resource_type("ORG_NODE", "REGION") == "Region"
    assert _label_for_resource_type("ORG_NODE", "STORE") == "Store"
    assert _label_for_resource_type("ORG_NODE", "DEPARTMENT") == "Department"


def test_ae_n5_label_for_resource_type_org_node_null_subtype_fallback() -> None:
    """LD11: ORG_NODE rows with NULL resource_subtype (pre-6.16.7
    historical rows) render as the "Org node" fallback.
    """
    assert _label_for_resource_type("ORG_NODE", None) == "Org node"


def test_ae_n6_conflict_qualifier_dispatch_for_all_9_codes() -> None:
    """LOAD-BEARING: LD9 dispatch table covers each of the 9 CONFLICT
    error class codes. ``compose_conflict_result_label`` produces the
    "Blocked - <qualifier>" composition for known codes and falls back
    to the static "Conflict" label for unknown codes.
    """
    # Each of the 9 codes yields a non-None qualifier.
    codes = [
        "DUPLICATE_TENANT_NAME",
        "INVALID_STATE_TRANSITION",
        "DUPLICATE_TENANT_USER_EMAIL",
        "ROLE_ASSIGNMENT_CONFLICT",
        "DUPLICATE_ORG_NODE_CODE",
        "DUPLICATE_STORE_CODE",
        "ROLE_ARCHIVED",
        "LAST_OVERRIDE_HOLDER",
        "SUPER_ADMIN_PROTECTED",
    ]
    for code in codes:
        qualifier = _qualifier_for_conflict(code)
        assert qualifier is not None, f"missing qualifier for {code}"
        composed = compose_conflict_result_label(code)
        assert composed.startswith("Blocked - "), (
            f"composition shape wrong for {code}: {composed!r}"
        )
        assert qualifier in composed

    # Unknown code falls back to the static label.
    assert compose_conflict_result_label("ZZZ_UNKNOWN") == "Conflict"
    # None input behaves the same as the static fallback.
    assert compose_conflict_result_label(None) == "Conflict"
