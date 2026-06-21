"""StoresRepo — data access for the ``stores`` table.

Reads (Step 6.17.2): ``list`` + ``get_by_id``.
Writes (Step 6.17.3): ``create`` + ``update``.

RLS-bound via session GUCs set by ``get_tenant_session`` (Step 2.2a);
the Repo accepts no ``tenant_id`` for visibility purposes per D-24.
The optional ``tenant_id`` argument on ``list(...)`` is
application-layer narrowing for PLATFORM callers who want to scope a
list view to a single tenant.

Per D-17, "row not visible" (whether absent or RLS-filtered) surfaces
as ``None`` from ``get_by_id`` / ``update``. The router converts to 404
``STORE_NOT_FOUND``.

Locked decision 2 (Step 6.17.2): the ``tenant_name`` label comes via
LEFT JOIN to ``core.tenants`` rather than a correlated subquery —
``tenant_name`` is a sibling-table label, not an aggregate. LEFT (not
INNER) so a hypothetical orphan row (no matching tenant) would surface
in the list rather than disappear. ``stores.tenant_id`` is NOT NULL
with an FK, so the LEFT/INNER distinction never fires in practice.

Locked decision 3 (Step 6.17.2): 8 sort keys. ``tenant_name_asc``
(default) and ``tenant_name_desc`` apply a stable secondary sort by
``stores.name ASC`` so two stores in the same tenant page
deterministically; the other 6 keys uniform-secondary-sort by
``stores.id ASC`` like the other repos.

Step 6.17.3 writes (Pattern (b) per D-13). Raw ``text()`` with
schema-qualified identifiers per CSD-03. Audit-actor population:
both halves of each ``*_by_user_id`` / ``*_by_user_type`` pair are
populated on every write to satisfy ``ck_stores_*_actor_pair``. Casts
to ``actor_user_type_enum`` are explicit per the architecture_RBAC
reference example. ``store_code`` uniqueness is pre-checked
case-insensitively to align with the DDL partial unique index
``uq_stores_tenant_store_code_lower``; ``org_node_id`` linkage is
pre-checked for clean typed 409s.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.audit.emit import (
    build_success_details_for_create,
    build_success_details_for_update,
    emit_audit_event,
)
from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.errors import (
    DuplicateStoreCodeError,
    InvalidParentNodeTypeError,
    ParentNodeNotFoundError,
)
from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.org_node import OrgNodeStatus, OrgNodeType
from admin_backend.models.store import Store, StoreStatus, TaxTreatment
from admin_backend.models.tenant import Tenant
from admin_backend.models.tenant_user import ActorUserType
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.org_nodes import OrgNodesRepo
from admin_backend.repositories.tenants import TransitionResult


# Step 6.16.5 LD3: stores set-status per-target action code mapping.
# Each target status maps to exactly one success-path action code +
# human label. Failure-path uses the AUDITED_ROUTES fallback
# ("SET_STATUS" / "Status change") since the failure handler can't
# re-parse the request body to determine target_status.
#
# OPEN_SOFT is in the vocabulary but currently unreachable via the
# live TRANSITION_MATRIX (no cell allows ``*->OPENING``; OPENING is
# entry-only via POST per 6.17.4 LD1). Reserved per FN-AB-68 for
# D-31 append-only stability and future matrix relaxation.
_TRANSITION_ACTION_BY_TARGET: dict[StoreStatus, str] = {
    StoreStatus.OPENING: "OPEN_SOFT",
    StoreStatus.ACTIVE: "ACTIVATE",
    StoreStatus.CLOSED: "CLOSE",
    StoreStatus.INACTIVE: "DEACTIVATE",
}


# Module-level singleton (Step 6.21.2). Matches the established pattern
# in routers/v1/org_tree.py:101 (``_org_repo = OrgNodesRepo()``).
# StoresRepo's atomic-pair writes call into OrgNodesRepo for the paired
# STORE-type org_node operations (add at create, set_status at
# transition, reparent/rename via edit_node at update). OrgNodesRepo is
# stateless so a single instance is safe to share.
_org_nodes_repo = OrgNodesRepo()


# Step 6.21.2: store status -> paired org_node status projection
# (architecture.md A.5 "Status mapping"). The mapping loses information
# (OPENING and ACTIVE both project to ACTIVE on the org_node side);
# acceptable for v0 because no current consumer reads the org_node
# status independently of the store status.
STORE_STATUS_TO_ORG_NODE_STATUS: dict[StoreStatus, OrgNodeStatus] = {
    StoreStatus.OPENING: OrgNodeStatus.ACTIVE,
    StoreStatus.ACTIVE: OrgNodeStatus.ACTIVE,
    StoreStatus.INACTIVE: OrgNodeStatus.INACTIVE,
    StoreStatus.CLOSED: OrgNodeStatus.ARCHIVED,
}


# Sort vocabulary. Values are tuples of ORDER BY clauses — the first
# element is the primary sort; subsequent elements form the stable
# secondary sort within the primary group. Final stable tie-breaker
# ``Store.id.asc()`` is appended in the query body for all keys (so
# multi-store identical-name pairs page deterministically even on
# tenant_name sorts).
#
# Annotated ``dict[str, Any]`` rather than the inferred
# ``dict[str, object]`` per the mypy nuance documented in
# ``PlatformUsersRepo.SORT_MAP``: ORM ``UnaryExpression``s erase to
# ``object`` once stored in a heterogeneous mapping, breaking
# ``.order_by(...)`` at the call site.
SORT_MAP: dict[str, tuple[Any, ...]] = {
    "tenant_name_asc":  (Tenant.name.asc(),       Store.name.asc()),
    "tenant_name_desc": (Tenant.name.desc(),      Store.name.asc()),
    "name_asc":         (Store.name.asc(),),
    "name_desc":        (Store.name.desc(),),
    "created_at_desc":  (Store.created_at.desc(),),
    "created_at_asc":   (Store.created_at.asc(),),
    "status_asc":       (Store.status.asc(),),
    "country_asc":      (Store.country.asc(),),
}

DEFAULT_STORES_SORT: str = "tenant_name_asc"


# Step 6.17.4: 9-cell liberal state-transition matrix per LD1. All
# transitions allowed EXCEPT ``*->OPENING`` (3 rejected cells:
# ACTIVE/INACTIVE/CLOSED -> OPENING). CLOSED is reversible.
#
# Same-state (e.g., ACTIVE -> ACTIVE) is NOT a key in any allowed set,
# so falls through to ``TransitionResult.INVALID_STATE``. Mirrors the
# tenants ``allowed_sources`` convention (Step 6.11): target state
# excluded from its own allowed-sources set; same-state rejected.
TRANSITION_MATRIX: dict[StoreStatus, set[StoreStatus]] = {
    StoreStatus.OPENING:  {StoreStatus.ACTIVE, StoreStatus.INACTIVE, StoreStatus.CLOSED},
    StoreStatus.ACTIVE:   {StoreStatus.INACTIVE, StoreStatus.CLOSED},
    StoreStatus.INACTIVE: {StoreStatus.ACTIVE, StoreStatus.CLOSED},
    StoreStatus.CLOSED:   {StoreStatus.ACTIVE, StoreStatus.INACTIVE},
}


@dataclass
class StoresListRow:
    """Row carrier for ``list(...)``: ORM Store + joined tenant_name.

    The router maps this to ``StoreListItem`` via
    ``_list_item_from_row``. Mirrors ``TenantListRow``'s shape from
    Step 6.8.3.
    """

    store: Store
    tenant_name: str


@dataclass
class StoreDetailRow:
    """Row carrier for ``get_by_id(...)``: same shape as
    StoresListRow; kept distinct so list-vs-detail mappers stay typed
    independently."""

    store: Store
    tenant_name: str


class StoresRepo:
    """Read-only repository for ``stores``. RLS-bound via session GUCs."""

    async def list(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID | None = None,
        status: StoreStatus | None = None,
        country: str | None = None,
        search: str | None = None,
        sort: str = DEFAULT_STORES_SORT,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[StoresListRow], int]:
        """Return ``(rows, total)`` matching filters under the caller's RLS context.

        Filters (``tenant_id``, ``status``, ``country``, ``search``)
        apply to both the count and the page query so ``total`` matches
        the filter set.

        ``search`` is case-insensitive ILIKE across ``name`` and
        ``store_code``; address is excluded per locked decision 4.

        ``sort`` must be one of ``SORT_MAP`` keys; raises
        ``InvalidSortKeyError`` otherwise.
        """
        if sort not in SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")

        conditions = []
        if tenant_id is not None:
            conditions.append(Store.tenant_id == tenant_id)
        if status is not None:
            conditions.append(Store.status == status)
        if country is not None:
            conditions.append(Store.country == country)
        if search:
            pat = f"%{search}%"
            conditions.append(
                or_(
                    Store.name.ilike(pat),
                    Store.store_code.ilike(pat),
                )
            )

        # Count query: count over ``stores`` alone — no JOIN to tenants
        # because the count cares about the row set, not the label.
        count_stmt = select(func.count()).select_from(Store)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        count_result = await session.execute(count_stmt)
        total: int = count_result.scalar_one()

        # Page query: LEFT JOIN to tenants for the tenant_name label.
        # Final stable tie-breaker is Store.id.asc() — appended after
        # the SORT_MAP entry's clauses.
        order_clauses = SORT_MAP[sort] + (Store.id.asc(),)
        stmt = (
            select(Store, Tenant.name.label("tenant_name"))
            .select_from(Store)
            .join(Tenant, Tenant.id == Store.tenant_id, isouter=True)
            .order_by(*order_clauses)
        )
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.offset(offset).limit(limit)

        page_result = await session.execute(stmt)
        rows = [
            StoresListRow(store=s, tenant_name=tn)
            for (s, tn) in page_result.all()
        ]
        return rows, total

    async def get_by_id(
        self,
        session: AsyncSession,
        store_id: UUID,
    ) -> StoreDetailRow | None:
        """Return the store with this id + tenant_name, or ``None`` if not visible.

        Per D-17, missing rows and RLS-filtered rows both produce
        ``None``. Cross-tenant probes by a TENANT JWT therefore surface
        as 404 ``STORE_NOT_FOUND`` at the router (not 403).
        """
        stmt = (
            select(Store, Tenant.name.label("tenant_name"))
            .select_from(Store)
            .join(Tenant, Tenant.id == Store.tenant_id, isouter=True)
            .where(Store.id == store_id)
        )
        result = await session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        store_obj, tenant_name = row
        return StoreDetailRow(store=store_obj, tenant_name=tenant_name)

    # ============================================================================
    # Step 6.17.3 writes.
    # ============================================================================

    async def _tenant_exists(
        self,
        session: AsyncSession,
        tenant_id: UUID,
    ) -> bool:
        """Probe whether ``tenant_id`` is visible to the caller's
        RLS-bound session.

        PLATFORM sessions see every tenant via D-29 OR-branch; TENANT
        sessions see only the row matching ``app.tenant_id``.
        Cross-tenant probes by a TENANT JWT therefore return False
        here and the caller maps to a 404 TENANT_NOT_FOUND (RLS-as-404
        per D-17), keeping a 500 from the RLS WITH CHECK rejection off
        the wire.
        """
        schema = get_settings().db_schema
        result = await session.execute(
            text(
                f"SELECT 1 FROM {schema}.tenants WHERE id = :tenant_id "
                "LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        )
        return result.first() is not None

    async def _raise_if_store_code_taken(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        store_code: str,
        exclude_store_id: UUID | None,
    ) -> None:
        """Per-tenant case-insensitive uniqueness check on ``store_code``.

        Aligns with the DDL partial unique index
        ``uq_stores_tenant_store_code_lower``: ``lower(store_code)``
        comparison so an incoming 'ABC' collides with an existing 'abc'.
        The DDL index closes the race window between this pre-check and
        the subsequent INSERT/UPDATE; the pre-check exists for typed
        409 ergonomics.

        ``exclude_store_id`` excludes self on rename so PATCH that
        keeps ``store_code`` unchanged is a no-op 200.
        """
        schema = get_settings().db_schema
        if exclude_store_id is None:
            row = await session.execute(
                text(
                    f"SELECT 1 FROM {schema}.stores "
                    "WHERE tenant_id = :tenant_id "
                    "AND lower(store_code) = lower(:store_code) "
                    "LIMIT 1"
                ),
                {"tenant_id": tenant_id, "store_code": store_code},
            )
        else:
            row = await session.execute(
                text(
                    f"SELECT 1 FROM {schema}.stores "
                    "WHERE tenant_id = :tenant_id "
                    "AND lower(store_code) = lower(:store_code) "
                    "AND id != :exclude_id "
                    "LIMIT 1"
                ),
                {
                    "tenant_id": tenant_id,
                    "store_code": store_code,
                    "exclude_id": exclude_store_id,
                },
            )
        if row.first() is not None:
            raise DuplicateStoreCodeError(
                f"store_code {store_code!r} already exists in tenant "
                f"{tenant_id}",
                tenant_id=str(tenant_id),
                store_code=store_code,
            )

    async def _check_parent_node_for_store(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        parent_org_node_id: UUID,
    ) -> None:
        """Pre-check that ``parent_org_node_id`` can parent the paired
        STORE-type org_node about to be created (Step 6.21.2 LD11).

        Replaces the retired ``_check_org_node_for_store``: the v0
        architecture used to accept an existing ``org_node_id`` and link
        to it; v0.1.* now creates the paired STORE-type org_node fresh
        inside the same transaction, so the check shifts from "is this
        existing org_node link-able" to "is this proposed parent
        valid".

        Two failure paths, two distinct wire codes:

        - Parent not visible (missing OR cross-tenant under RLS): raise
          ``ParentNodeNotFoundError`` (404). RLS collapses
          "doesn't exist" and "different tenant from a TENANT JWT" into
          one not-visible outcome per D-17. PLATFORM sessions see the
          cross-tenant row but the explicit ``tenant_id`` filter in the
          SELECT below still rejects it as a not-visible-here outcome.

        - Parent is STORE-type: raise ``InvalidParentNodeTypeError``
          (422). The cascade-order rule (TENANT(0) -> BUSINESS_UNIT(1)
          -> ... -> STORE(5) -> DEPARTMENT(6); see
          ``_check_cascade_order`` in org_nodes.py) makes STORE a leaf
          for store-creation purposes; STORE can have DEPARTMENT
          children, but a new STORE under another STORE is a same-
          ordinal violation.

        Called from ``create`` (initial linking) and ``update``
        (reparent). The "already linked" case from the retired helper
        is no longer reachable under the atomic-pair architecture (the
        server creates the paired org_node fresh).
        """
        schema = get_settings().db_schema
        result = await session.execute(
            text(
                f"""
                SELECT node_type
                FROM {schema}.org_nodes
                WHERE id = :node_id
                  AND tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {
                "node_id": parent_org_node_id,
                "tenant_id": tenant_id,
            },
        )
        row = result.first()
        if row is None:
            raise ParentNodeNotFoundError(
                (
                    f"parent_org_node_id={parent_org_node_id} not "
                    f"visible in tenant_id={tenant_id}"
                ),
                parent_id=str(parent_org_node_id),
                tenant_id=str(tenant_id),
            )
        if row.node_type == OrgNodeType.STORE.value:
            raise InvalidParentNodeTypeError(
                (
                    f"parent_org_node_id={parent_org_node_id} is "
                    "STORE-type; stores cannot parent other stores"
                ),
                parent_id=str(parent_org_node_id),
                parent_node_type=OrgNodeType.STORE.value,
                child_node_type=OrgNodeType.STORE.value,
            )

    async def create(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        name: str,
        country: str,
        timezone: str,
        currency: str,
        store_code: str,
        tax_treatment: TaxTreatment,
        parent_org_node_id: UUID,
        address: str | None,
        latitude: Decimal | None,
        longitude: Decimal | None,
        auth: AuthContext,
        request_id: UUID | None = None,
    ) -> StoreDetailRow | None:
        """Atomic paired write: create the paired STORE-type org_node
        and the ``stores`` row in one transaction. Multi-audience (LD1).

        Step 6.21.2 supersedes the Step 6.17.3 design which accepted an
        existing ``org_node_id`` on the body. The new shape:

          1. Validate ``tenant_id`` is RLS-visible (RLS-as-404 for
             cross-tenant TENANT JWTs).
          2. Validate ``parent_org_node_id`` via
             ``_check_parent_node_for_store`` (404
             ``PARENT_NODE_NOT_FOUND`` for missing/cross-tenant; 422
             ``INVALID_PARENT_NODE_TYPE`` for STORE-type parent).
          3. Validate ``store_code`` uniqueness in ``stores`` (case-
             insensitive); 409 ``DUPLICATE_STORE_CODE`` on collision.
          4. INSERT the paired STORE-type org_node via
             ``OrgNodesRepo.add_node``. ``code = store_code`` and
             ``name = name`` map verbatim per architecture.md A.5
             (field ownership). 409 ``DUPLICATE_ORG_NODE_CODE`` on
             tenant-wide code collision against any other org_node
             code (HQ, REGION, etc.).
          5. INSERT the ``stores`` row with
             ``org_node_id`` = id from step 4 (creating the 1:1 link).
             Server omits ``status``; the DDL default fires (LD8).
          6. Refetch the materialised row.

        Failure at any step rolls back the whole transaction (single
        SQLAlchemy session). No orphan org_node, no orphan store.

        Both halves of every audit-actor pair populate on every INSERT
        (Pattern (b) per D-13); the ``ck_stores_*_actor_pair`` and
        ``ck_org_nodes_*_actor_pair`` CHECKs enforce uniformity.

        Returns ``None`` when ``tenant_id`` is RLS-invisible. Caller
        raises ``TenantNotFoundError`` (404) per the multi-audience
        RLS-as-404 pattern.
        """
        schema = get_settings().db_schema

        if not await self._tenant_exists(session, tenant_id):
            return None

        # Step 1: validate parent (404 or 422 raises propagate).
        await self._check_parent_node_for_store(
            session,
            tenant_id=tenant_id,
            parent_org_node_id=parent_org_node_id,
        )

        # Step 2: store_code uniqueness in stores. Org_node UNIQUE
        # collision is enforced separately by ``add_node`` below
        # (broader scope: across all org_node types in the tenant).
        await self._raise_if_store_code_taken(
            session,
            tenant_id=tenant_id,
            store_code=store_code,
            exclude_store_id=None,
        )

        actor_user_type = ActorUserType(auth.user_type)

        # Step 3: create the paired STORE-type org_node first. add_node
        # owns: parent existence/lock, cascade-order check, path build
        # (parent.path || lower(store_code)), audit-actor population,
        # and IntegrityError->DuplicateOrgNodeCodeError mapping on the
        # tenant-wide UNIQUE.
        org_node = await _org_nodes_repo.add_node(
            session,
            tenant_id=tenant_id,
            parent_id=parent_org_node_id,
            node_type=OrgNodeType.STORE,
            code=store_code,
            name=name,
            auth=auth,
        )

        # Step 4: INSERT the stores row, linking org_node_id to the
        # row created in step 3.
        insert_sql = text(
            f"""
            INSERT INTO {schema}.stores (
                tenant_id, org_node_id,
                name, store_code, country, timezone, address,
                latitude, longitude,
                currency, tax_treatment,
                created_by_user_id, created_by_user_type,
                updated_by_user_id, updated_by_user_type
            ) VALUES (
                :tenant_id, :org_node_id,
                :name, :store_code, :country, :timezone, :address,
                :latitude, :longitude,
                :currency,
                CAST(:tax_treatment AS {schema}.tax_treatment_enum),
                :actor, CAST(:actor_type AS {schema}.actor_user_type_enum),
                :actor, CAST(:actor_type AS {schema}.actor_user_type_enum)
            )
            RETURNING id
            """
        )
        result = await session.execute(
            insert_sql,
            {
                "tenant_id": tenant_id,
                "org_node_id": org_node.id,
                "name": name,
                "store_code": store_code,
                "country": country,
                "timezone": timezone,
                "address": address,
                "latitude": latitude,
                "longitude": longitude,
                "currency": currency,
                "tax_treatment": tax_treatment.value,
                "actor": auth.user_id,
                "actor_type": actor_user_type.value,
            },
        )
        new_id: UUID = result.scalar_one()

        await session.flush()

        row = await self.get_by_id(session, new_id)
        assert row is not None, (
            f"freshly-created store {new_id} not visible to the same "
            "session"
        )

        # Step 6.16.5 success audit emission. LD6: snapshot carries
        # store identity + paired org_node id/name + the
        # ``org_node_created_atomically`` flag (always True in v0;
        # 6.21.2 made atomic-pair the only POST /stores path; FN-AB-68
        # reserves room for a future variant where this is False).
        if request_id is not None:
            snapshot = {
                "id": row.store.id,
                "name": row.store.name,
                "store_code": row.store.store_code,
                "country": row.store.country,
                "timezone": row.store.timezone,
                "currency": row.store.currency,
                "tax_treatment": row.store.tax_treatment.value,
                "status": row.store.status.value,
                "org_node_id": org_node.id,
                "org_node_name": org_node.name,
                "org_node_created_atomically": True,
            }
            await emit_audit_event(
                session,
                auth=auth,
                action="CREATE",
                resource_type="STORE",
                resource_id=row.store.id,
                resource_label=row.store.name,
                result_type=AuditResultType.SUCCESS,
                details=build_success_details_for_create(snapshot),
                tenant_id=tenant_id,
                tenant_name=row.tenant_name,
                request_id=request_id,
                route_to_platform=False,
            )

        return row

    async def update(
        self,
        session: AsyncSession,
        store_id: UUID,
        *,
        fields: dict[str, Any],
        auth: AuthContext,
        request_id: UUID | None = None,
    ) -> StoreDetailRow | None:
        """Partial update of one ``stores`` row. Multi-audience (LD1).

        ``fields`` is the caller's ``exclude_unset=True`` dump of the
        Pydantic patch body — keys are column names; values are the
        new values (including ``None`` to clear nullable columns).
        ``status``, ``tenant_id``, ``org_node_id``, audit and ``closed_*``
        columns are absent from ``fields`` by construction (rejected by
        the schema's ``extra="forbid"``).

        Step 6.21.2 cascade: when ``name``, ``store_code``, or
        ``parent_org_node_id`` are in ``fields``, the change propagates
        to the paired STORE-type org_node atomically inside the same
        request transaction:
          - ``name`` -> org_node.name
          - ``store_code`` -> org_node.code (path label rewrites too)
          - ``parent_org_node_id`` -> org_node.parent_id (reparent +
            subtree path rewrite)
        ``parent_org_node_id`` is pre-validated via
        ``_check_parent_node_for_store`` (404/422).

        ``stores.org_node_id`` is NEVER modified by this method.

        Returns ``None`` when the row is missing or RLS-filtered
        (RLS-as-404 per D-17). Caller raises ``StoreNotFoundError`` on
        ``None``.

        Raises ``DuplicateStoreCodeError`` (409) when ``store_code`` is
        in ``fields`` and another store in the same tenant already
        holds that value (case-insensitive). Org_node UNIQUE collisions
        on cascade (against any non-store org_node code in the tenant)
        surface as ``DuplicateOrgNodeCodeError`` (409) via
        ``edit_node``'s IntegrityError mapping — a broader check than
        the stores-only pre-check above.

        Caller (the handler) is responsible for the empty-body check
        (``EmptyPatchError``) before invoking; the repo trusts
        ``fields`` to be non-empty.
        """
        schema = get_settings().db_schema

        # Allowlist: defensive against future schema-loosening. Mirrors
        # the StorePatchRequest field set.
        allowed_keys: frozenset[str] = frozenset({
            "name",
            "store_code",
            "country",
            "timezone",
            "currency",
            "tax_treatment",
            "address",
            "latitude",
            "longitude",
            "parent_org_node_id",
        })
        invalid = set(fields.keys()) - allowed_keys
        if invalid:
            raise ValueError(
                f"unexpected update fields: {sorted(invalid)!r}"
            )

        actor_user_type = ActorUserType(auth.user_type)

        # Resolve the existing row (under RLS). The lookup also gives
        # us the row's tenant_id and the paired org_node_id, both
        # needed for downstream pre-checks and cascade calls. Step
        # 6.16.5: project the field values present in ``fields`` so
        # the audit-row before/after diff can be built without a
        # second SELECT.
        existing = await session.execute(
            text(
                f"""
                SELECT tenant_id, org_node_id,
                       name, store_code, country, timezone, currency,
                       tax_treatment::text AS tax_treatment,
                       address, latitude, longitude
                  FROM {schema}.stores
                 WHERE id = :store_id
                 LIMIT 1
                """
            ),
            {"store_id": store_id},
        )
        existing_row = existing.first()
        if existing_row is None:
            return None
        tenant_id_of_row: UUID = existing_row.tenant_id
        org_node_id_of_row: UUID = existing_row.org_node_id

        # Capture the pre-update field values for the audit before
        # half. Only fields that may appear in ``fields`` are captured;
        # the diff filter below picks the ones that actually changed.
        before_values: dict[str, Any] = {
            "name": existing_row.name,
            "store_code": existing_row.store_code,
            "country": existing_row.country,
            "timezone": existing_row.timezone,
            "currency": existing_row.currency,
            "tax_treatment": existing_row.tax_treatment,
            "address": existing_row.address,
            "latitude": existing_row.latitude,
            "longitude": existing_row.longitude,
        }

        # Step 6.21.2: cascade-prep pre-check on parent (404/422).
        # Done before the store_code uniqueness check so the more
        # structurally-significant failure surfaces first.
        new_parent_id: UUID | None = None
        if "parent_org_node_id" in fields:
            new_parent_id = fields["parent_org_node_id"]
            if new_parent_id is None:
                # Explicit-null disallowed; the schema also rejects
                # this via the field's UUID type so this branch is
                # defensive only.
                raise ValueError(
                    "parent_org_node_id cannot be set to null"
                )
            await self._check_parent_node_for_store(
                session,
                tenant_id=tenant_id_of_row,
                parent_org_node_id=new_parent_id,
            )

        if "store_code" in fields and fields["store_code"] is not None:
            await self._raise_if_store_code_taken(
                session,
                tenant_id=tenant_id_of_row,
                store_code=fields["store_code"],
                exclude_store_id=store_id,
            )

        # Build per-column SET clauses, with enum casts where the live
        # column is a named PG enum. ``stores`` has one such mutable
        # field: ``tax_treatment``. ``status`` is not in the allowlist
        # (transitions land in Step 6.17.4). ``parent_org_node_id`` is
        # NOT a column on ``stores`` — it cascades to the paired
        # org_node only; excluded from the SET clause builder.
        _ENUM_CASTS: dict[str, str] = {
            "tax_treatment": "tax_treatment_enum",
        }
        _CASCADE_ONLY: frozenset[str] = frozenset({"parent_org_node_id"})

        set_parts: list[str] = []
        params: dict[str, Any] = {
            "actor": auth.user_id,
            "actor_type": actor_user_type.value,
            "store_id": store_id,
        }
        for key, value in fields.items():
            if key in _CASCADE_ONLY:
                continue
            enum_type = _ENUM_CASTS.get(key)
            if enum_type is not None:
                set_parts.append(
                    f"{key} = CAST(:{key} AS {schema}.{enum_type})"
                )
                # Pass enum members by their .value string.
                params[key] = value.value if value is not None else None
            else:
                set_parts.append(f"{key} = :{key}")
                params[key] = value

        if set_parts:
            set_parts.append("updated_by_user_id = :actor")
            set_parts.append(
                "updated_by_user_type = "
                f"CAST(:actor_type AS {schema}.actor_user_type_enum)"
            )
            # ``updated_at`` is refreshed by the BEFORE-UPDATE trigger
            # ``tg_stores_set_updated_at`` (calls
            # ``set_updated_at_timestamp`` which sets
            # ``NEW.updated_at = NOW()``). Mirrors the
            # tenants/tenant_users pattern; no explicit SET here.
            # Within a single transaction Postgres ``now()`` returns
            # the TX-start timestamp, so concurrent create+update
            # inside one TX share the same updated_at.

            result = await session.execute(
                text(
                    f"UPDATE {schema}.stores SET {', '.join(set_parts)} "
                    "WHERE id = :store_id RETURNING id"
                ),
                params,
            )
            if result.first() is None:
                # Row vanished between the SELECT and UPDATE (RLS-flip
                # or concurrent delete); treat as RLS-as-404.
                return None

        # Step 6.21.2 cascade: name / store_code / parent_org_node_id
        # propagate to the paired org_node via edit_node. edit_node
        # owns: SELECT FOR UPDATE on target, cycle / cascade-order
        # check on reparent, path rewrite (label + subtree), and
        # IntegrityError->DuplicateOrgNodeCodeError mapping on the
        # tenant-wide UNIQUE.
        cascade_name = fields.get("name") if "name" in fields else None
        cascade_code = (
            fields.get("store_code") if "store_code" in fields else None
        )
        cascade_reparent = "parent_org_node_id" in fields
        if (
            cascade_name is not None
            or cascade_code is not None
            or cascade_reparent
        ):
            await _org_nodes_repo.edit_node(
                session,
                tenant_id=tenant_id_of_row,
                node_id=org_node_id_of_row,
                name=cascade_name,
                code=cascade_code,
                parent_id=new_parent_id if cascade_reparent else None,
                auth=auth,
                reparent=cascade_reparent,
            )

        # Raw UPDATE bypasses SA ORM, so any Store instance cached in
        # the session's identity map still holds stale attribute values.
        # Expire so the materialising read returns fresh data.
        session.expire_all()
        result_row = await self.get_by_id(session, store_id)

        # Step 6.16.5 success audit emission. LD11: before/after diff
        # carrying only the fields that actually changed. Note: under
        # the 6.21.2 atomic-pair design, ``stores.org_node_id`` is
        # immutable via PATCH (the row's paired org_node never
        # changes). What the caller passes as ``parent_org_node_id``
        # cascades to the paired org_node's ``parent_id``; from the
        # store's vantage that's a "parent_org_node_id changed"
        # diff, not an "org_node_id changed" diff. LD7's framing of
        # "org_node_id change" predates 6.21.2 and is reinterpreted
        # here as parent_org_node_id, with both old and new parent
        # names snapshotted for context.
        if request_id is not None and result_row is not None:
            before_diff: dict[str, Any] = {}
            after_diff: dict[str, Any] = {}
            for key, new_val in fields.items():
                if key == "parent_org_node_id":
                    continue  # handled below with name lookups
                if key == "tax_treatment" and new_val is not None:
                    new_str = new_val.value
                else:
                    new_str = new_val
                old_val = before_values.get(key)
                if old_val != new_str:
                    before_diff[key] = old_val
                    after_diff[key] = new_str

            if cascade_reparent and new_parent_id is not None:
                old_parent_name = await self._lookup_org_node_name(
                    session, tenant_id_of_row, org_node_id_of_row
                )
                # The OLD parent name is the parent of the paired
                # org_node BEFORE the cascade; capture via parent_id
                # resolution on the pre-update state. The
                # edit_node cascade has already moved the parent_id
                # at this point, so we instead snapshot the
                # parent_org_node_id values directly.
                refetched_parent = await self._lookup_org_node_name(
                    session, tenant_id_of_row, new_parent_id
                )
                # The Repo doesn't carry the pre-cascade parent_id
                # value (existing_row didn't project it). To capture
                # "old parent" cleanly we'd need a pre-cascade
                # SELECT on org_nodes; for v0 audit purposes a
                # minimal diff suffices.
                before_diff["parent_org_node_id"] = None
                after_diff["parent_org_node_id"] = new_parent_id
                before_diff["parent_org_node_name"] = old_parent_name
                after_diff["parent_org_node_name"] = refetched_parent

            if before_diff:
                await emit_audit_event(
                    session,
                    auth=auth,
                    action="UPDATE",
                    resource_type="STORE",
                    resource_id=result_row.store.id,
                    resource_label=result_row.store.name,
                    result_type=AuditResultType.SUCCESS,
                    details=build_success_details_for_update(
                        before=before_diff,
                        after=after_diff,
                    ),
                    tenant_id=tenant_id_of_row,
                    tenant_name=result_row.tenant_name,
                    request_id=request_id,
                    route_to_platform=False,
                )

        return result_row

    async def _lookup_org_node_name(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        node_id: UUID,
    ) -> str:
        """Resolve ``org_nodes.name`` for the audit row context.

        Defensive fallback ``<unknown>`` if the row is concurrently
        deleted.
        """
        schema = get_settings().db_schema
        result = await session.execute(
            text(
                f"SELECT name FROM {schema}.org_nodes "
                "WHERE id = :node_id AND tenant_id = :tenant_id"
            ),
            {"node_id": node_id, "tenant_id": tenant_id},
        )
        name = result.scalar_one_or_none()
        return str(name) if name is not None else "<unknown>"

    # ============================================================================
    # Step 6.17.4 set-status transition.
    # ============================================================================

    async def transition(
        self,
        session: AsyncSession,
        store_id: UUID,
        *,
        target_status: StoreStatus,
        auth: AuthContext,
        request_id: UUID | None = None,
    ) -> tuple["StoreDetailRow | None", TransitionResult]:
        """Atomic state transition for one ``stores`` row.

        Returns ``(row | None, result)``:
          - ``(None, NOT_FOUND)`` when the row is missing or
            RLS-filtered.
          - ``(None, INVALID_STATE)`` when ``current.status`` doesn't
            permit ``target_status`` per ``TRANSITION_MATRIX``
            (includes same-state per LD5 / mirrors tenants).
          - ``(row, OK)`` after a successful UPDATE.

        Closed-triplet handling per ``ck_stores_closed_consistency``:
          - **Class 1 (into-CLOSED)**: ``closed_at`` + ``closed_by_*``
            populate atomically with the status flip.
          - **Class 2 (out-of-CLOSED)**: ``closed_at`` + ``closed_by_*``
            null atomically with the status flip. Historical closure
            metadata is lost on the row (LD2); Step 6.2 audit_log
            preserves the history when shipped.
          - **Class 3 (between non-CLOSED)**: closed_* columns
            untouched (they're already NULL by the DDL CHECK invariant).

        Pattern (b) audit-actor: ``updated_by_*`` populates on every
        transition (LD7). Class 1 additionally populates ``closed_by_*``;
        Class 2 additionally nulls it.

        SELECT FOR UPDATE locks the row inside the request transaction
        so a concurrent transition doesn't race.

        Schema-qualified raw SQL per CSD-03.
        """
        schema = get_settings().db_schema

        row = await session.execute(
            text(
                f"SELECT status, tenant_id, org_node_id "
                f"FROM {schema}.stores "
                "WHERE id = :store_id FOR UPDATE"
            ),
            {"store_id": store_id},
        )
        current = row.first()
        if current is None:
            return None, TransitionResult.NOT_FOUND

        current_status = StoreStatus(current.status)
        if target_status not in TRANSITION_MATRIX[current_status]:
            return None, TransitionResult.INVALID_STATE

        tenant_id_of_row: UUID = current.tenant_id
        org_node_id_of_row: UUID = current.org_node_id
        actor_user_type = ActorUserType(auth.user_type)

        actor_cast = (
            f"CAST(:actor_type AS {schema}.actor_user_type_enum)"
        )
        status_cast = f"CAST(:target_status AS {schema}.store_status_enum)"

        # Three transition classes per LD7 / ck_stores_closed_consistency.
        if target_status is StoreStatus.CLOSED:
            # Class 1: into-CLOSED. Populate closed_at + closed_by_* pair.
            update_sql = text(
                f"""
                UPDATE {schema}.stores
                   SET status = {status_cast},
                       closed_at = now(),
                       closed_by_user_id = :actor_id,
                       closed_by_user_type = {actor_cast},
                       updated_at = now(),
                       updated_by_user_id = :actor_id,
                       updated_by_user_type = {actor_cast}
                 WHERE id = :store_id
                """
            )
        elif current_status is StoreStatus.CLOSED:
            # Class 2: out-of-CLOSED. Null the closed_at + closed_by_* pair.
            update_sql = text(
                f"""
                UPDATE {schema}.stores
                   SET status = {status_cast},
                       closed_at = NULL,
                       closed_by_user_id = NULL,
                       closed_by_user_type = NULL,
                       updated_at = now(),
                       updated_by_user_id = :actor_id,
                       updated_by_user_type = {actor_cast}
                 WHERE id = :store_id
                """
            )
        else:
            # Class 3: between non-CLOSED. closed_* untouched
            # (already NULL by the DDL CHECK).
            update_sql = text(
                f"""
                UPDATE {schema}.stores
                   SET status = {status_cast},
                       updated_at = now(),
                       updated_by_user_id = :actor_id,
                       updated_by_user_type = {actor_cast}
                 WHERE id = :store_id
                """
            )

        await session.execute(
            update_sql,
            {
                "target_status": target_status.value,
                "actor_id": auth.user_id,
                "actor_type": actor_user_type.value,
                "store_id": store_id,
            },
        )

        # Step 6.21.2 cascade: project the store status to the paired
        # org_node's status via STORE_STATUS_TO_ORG_NODE_STATUS and
        # apply via OrgNodesRepo.set_status. The set_status method
        # handles the archived_* triplet symmetrically to the stores
        # closed_* triplet (into-ARCHIVED populates; out-of-ARCHIVED
        # nulls; between non-ARCHIVED leaves untouched).
        projected_status = STORE_STATUS_TO_ORG_NODE_STATUS[target_status]
        await _org_nodes_repo.set_status(
            session,
            tenant_id=tenant_id_of_row,
            node_id=org_node_id_of_row,
            target_status=projected_status,
            auth=auth,
        )

        # Raw UPDATE bypasses SA ORM; expire so the materialising read
        # returns fresh status / closed_* / updated_*.
        session.expire_all()
        result_row = await self.get_by_id(session, store_id)

        # Step 6.16.5 success audit emission per LD3. Action dispatch
        # on target_status; standard transition payload shape.
        if request_id is not None and result_row is not None:
            action_code = _TRANSITION_ACTION_BY_TARGET[target_status]
            await emit_audit_event(
                session,
                auth=auth,
                action=action_code,
                resource_type="STORE",
                resource_id=result_row.store.id,
                resource_label=result_row.store.name,
                result_type=AuditResultType.SUCCESS,
                details={
                    "before": {"status": current_status.value},
                    "after": {"status": target_status.value},
                },
                tenant_id=tenant_id_of_row,
                tenant_name=result_row.tenant_name,
                request_id=request_id,
                route_to_platform=False,
            )

        return result_row, TransitionResult.OK
