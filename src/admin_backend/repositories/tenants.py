"""TenantsRepo — read-only data access for the ``tenants`` table.

The Repo class owns SELECT queries on ``tenants``. It does NOT set
tenant context, NOT begin transactions, NOT handle commits/rollbacks.
The session passed in already carries ``app.tenant_id`` and
``app.user_type`` GUCs set by ``get_tenant_session`` (Step 2.2a); RLS
filtering is therefore automatic. The Repo is unaware of multi-tenancy
mechanics.

Per D-17, "row not visible" (whether absent or RLS-filtered) surfaces
as ``None`` from ``get_by_id`` / ``get_by_id_with_aggregates``. The
router layer converts ``None`` to a 404 response — keeping the Repo
from raising lets callers distinguish "not visible" from "DB error"
cleanly.

Per D-24, this Repo MUST NOT accept a ``tenant_id`` argument on any
visibility-bearing method. Tenant context flows through the session,
never through method parameters. Adding such an argument would create
a second source of tenant identity that bypasses RLS.

Step 3.3 added the aggregate-shaped methods (``list_with_aggregates``,
``get_by_id_with_aggregates``, ``count_for_stats``) and the row
dataclasses that carry the per-row aggregate values back to the
router. The aggregate subqueries are scalar, correlated to the outer
``Tenant`` row, and inherit the same RLS filtering as the outer
select — D-29's PLATFORM OR-branch makes platform-wide aggregates
behave correctly, and TENANT-scoped aggregates filter to the tenant's
own row.

Step 3.4.5 (FN-AB-16 RESOLVED) replaced the per-tenant module-list
stub with a real per-row scalar subquery against
``tenant_module_access`` joined to ``lookups`` for display-name
resolution. The subquery uses ``jsonb_agg(... ORDER BY ...)`` so the
modules array comes back already shaped as
``[{"code": ..., "name": ...}, ...]`` ordered by
``lookups.display_order``; the COALESCE wrapping turns the
zero-rows case (a tenant with no enabled modules) into ``[]``
rather than the SQL NULL ``jsonb_agg`` would otherwise return.
"""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import String, and_, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.audit.emit import (
    build_success_details_for_create,
    build_success_details_for_transition,
    build_success_details_for_update,
    emit_audit_event,
)
from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.errors import (
    DuplicateTenantNameError,
    InvalidTenantNameForSlugError,
)
from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.lookup import Lookup
from admin_backend.models.store import Store
from admin_backend.models.tenant import Tenant, TenantStatus, TenantTier
from admin_backend.models.tenant_module_access import (
    ModuleAccessStatus,
    ModuleCode,
    TenantModuleAccess,
)
from admin_backend.models.tenant_user import TenantUser, TenantUserStatus
from admin_backend.repositories._errors import InvalidSortKeyError


# Step 6.20.1: mechanical slug for tenant-root org_node (code, path)
# derivation. Pure-function, module-level so unit tests can exercise
# without DB. The rule is intentionally simpler than the seed shape;
# editorial overrides (Buc-ee's -> BUC-EES) are not in scope here. See
# LD3 in prompts/step-6_20_1-impl-2026-05-18.md.
_SLUG_NON_ALPHANUMERIC_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX_LEN = 64


def slug_for_tenant_root(
    name: str,
    display_code: str | None,
) -> tuple[str, str]:
    """Derive ``(code, path)`` for a tenant-root org_node.

    Input is ``display_code`` if non-None, else ``name``. Steps:

    1. NFKD decompose + drop combining marks (diacritic strip).
    2. ASCII-only encode/decode.
    3. lowercase.
    4. collapse runs of non-alphanumerics to single ``-``.
    5. trim leading/trailing ``-``.
    6. truncate to 64 chars; re-trim trailing ``-`` if truncation lands
       on one.
    7. if result is empty, raise ``InvalidTenantNameForSlugError`` named
       at the field that produced the empty slug.

    Returns ``(code, path)`` where ``code`` is the uppercased slug
    (DDL ``ck_org_nodes_code_format`` compliant) and ``path`` is the
    same slug with ``-`` -> ``_`` (ltree label requirement; ltree
    disallows hyphens).
    """
    source = display_code if display_code is not None else name
    field: Literal["name", "display_code"] = (
        "display_code" if display_code is not None else "name"
    )

    normalised = unicodedata.normalize("NFKD", source)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    collapsed = _SLUG_NON_ALPHANUMERIC_RE.sub("-", lowered)
    trimmed = collapsed.strip("-")

    if len(trimmed) > _SLUG_MAX_LEN:
        trimmed = trimmed[:_SLUG_MAX_LEN].rstrip("-")

    if not trimmed:
        raise InvalidTenantNameForSlugError(
            f"slug for {field}={source!r} is empty after normalisation",
            field=field,
        )

    return trimmed.upper(), trimmed.replace("-", "_")


# Step 6.4 sort vocabulary. Six column-based keys land at module level
# (the column expressions are stable across calls). Four aggregate-based
# keys (num_users_active_*, num_stores_*) are built per-call inside
# ``list_with_aggregates`` because their underlying scalar subqueries
# are constructed there — see Step 3.3 / Step 5.2's TenantUser stub
# swap. The full 10-key set is enumerated by ``TENANTS_SORT_KEYS`` for
# validation; the resolution to ORDER BY clauses happens inside the
# method.
#
# ``dict[str, Any]`` rather than the inferred ``dict[str, object]`` per
# the same mypy nuance documented in PlatformUsersRepo.SORT_MAP — ORM
# UnaryExpressions erase to ``object`` once stored in a heterogeneous
# mapping, and that erasure breaks ``.order_by(...)`` at the call site.
_BASE_TENANTS_SORT_MAP: dict[str, Any] = {
    "created_at_asc": Tenant.created_at.asc(),
    "created_at_desc": Tenant.created_at.desc(),
    "name_asc": Tenant.name.asc(),
    "name_desc": Tenant.name.desc(),
    "tier_asc": Tenant.tier.asc(),
    "tier_desc": Tenant.tier.desc(),
}

_AGGREGATE_TENANTS_SORT_KEYS: frozenset[str] = frozenset({
    "num_users_active_asc",
    "num_users_active_desc",
    "num_stores_asc",
    "num_stores_desc",
})

# Public — the full set of accepted sort keys. Validation uses this.
# Resolution to a SQL clause happens inside ``list_with_aggregates``.
TENANTS_SORT_KEYS: frozenset[str] = frozenset(
    _BASE_TENANTS_SORT_MAP.keys()
) | _AGGREGATE_TENANTS_SORT_KEYS

# Default sort. Mirrors PlatformUsersRepo / TenantUsersRepo precedent.
# Step 6.4's note: pre-Step-6.4 the endpoint had NO sort param and the
# Repo hardcoded ``name ASC``. Callers who don't pass ``sort`` now
# receive ``created_at_desc`` (newest first) — a deliberate behaviour
# change rather than preserving the prior implicit ordering.
DEFAULT_TENANTS_SORT: str = "created_at_desc"


def _modules_subq() -> Any:
    """Per-tenant modules as ``jsonb_agg`` of ``{code, name}``, ordered.

    Returns a scalar subquery correlated to the outer ``Tenant`` row.
    Yields a JSONB array (decoded by psycopg as ``list[dict[str, str]]``)
    where each element is ``{"code": <module_code>, "name": <display_name>}``,
    ordered by ``lookups.display_order ASC``. ENABLED rows only;
    DISABLED entitlements do not surface in API responses.

    Empty case: ``jsonb_agg(...)`` over zero rows returns SQL NULL,
    which would break the response schema (``list[Module]`` doesn't
    accept None). The COALESCE wraps it to ``'[]'::jsonb`` so the
    handler always sees an empty list.

    The JOIN to ``lookups`` resolves display names: ``lookups.list_name
    = 'module_code'`` and ``lookups.code = tenant_module_access.module``.
    """
    # ``aggregate_order_by`` produces SQL of the form
    # ``jsonb_agg(<expr> ORDER BY <col>)`` — the ORDER BY lives inside
    # the aggregate, controlling the order of elements in the resulting
    # JSON array. An outer ``.order_by(...)`` on a scalar subquery
    # would be ignored.
    # Cast tenant_module_access.module (PG enum module_code_enum) to
    # text so it can be compared to lookups.code (TEXT). Postgres does
    # not implicitly cast varchar/text vs a named enum type — same
    # gotcha that bit Step 3.3's TenantUser.status (see "Note on PG
    # enum columns" in CLAUDE.md). Casting once in the JOIN is cleaner
    # than declaring lookups.code as the enum type (which would couple
    # the platform-global lookups table to one specific enum).
    module_as_text = cast(TenantModuleAccess.module, String)
    ordered_module_object = aggregate_order_by(
        func.jsonb_build_object(
            "code", module_as_text,
            "name", Lookup.display_name,
        ),
        Lookup.display_order.asc(),
    )
    return (
        select(
            func.coalesce(
                func.jsonb_agg(ordered_module_object),
                text("'[]'::jsonb"),
            )
        )
        .select_from(TenantModuleAccess)
        .join(
            Lookup,
            and_(
                Lookup.list_name == "module_code",
                Lookup.code == module_as_text,
            ),
        )
        .where(
            TenantModuleAccess.tenant_id == Tenant.id,
            TenantModuleAccess.status == ModuleAccessStatus.ENABLED,
        )
        .correlate(Tenant)
        .scalar_subquery()
    )


@dataclass
class TenantListRow:
    """Row carrier for ``list_with_aggregates``: ORM Tenant + aggregates."""

    tenant: Tenant
    num_stores: int
    num_users_active: int
    modules: list[dict[str, str]]


@dataclass
class TenantDetailRow:
    """Row carrier for ``get_by_id_with_aggregates``: same shape as
    TenantListRow; kept distinct so list-vs-detail mappers stay typed
    independently.
    """

    tenant: Tenant
    num_stores: int
    num_users_active: int
    modules: list[dict[str, str]]


class TransitionResult(StrEnum):
    """Outcome enum for ``TenantsRepo.transition`` (Step 6.11.1).

    Three values: ``OK`` (UPDATE succeeded), ``NOT_FOUND`` (row missing
    or RLS-filtered), ``INVALID_STATE`` (current status doesn't permit
    the requested transition).
    """

    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    INVALID_STATE = "INVALID_STATE"


class TenantsRepo:
    """Read-only repository for ``tenants``. RLS-bound via session GUCs."""

    async def get_by_id(
        self,
        session: AsyncSession,
        tenant_id: UUID,
    ) -> Tenant | None:
        """Return the tenant with this id, or ``None`` if not visible."""
        stmt = select(Tenant).where(Tenant.id == tenant_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self,
        session: AsyncSession,
    ) -> list[Tenant]:
        """Return all tenants visible to the current session, ordered by name."""
        stmt = select(Tenant).order_by(Tenant.name.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_status(
        self,
        session: AsyncSession,
        status: TenantStatus,
    ) -> list[Tenant]:
        """Return tenants with the given status, visible to this session, ordered by name."""
        stmt = (
            select(Tenant)
            .where(Tenant.status == status)
            .order_by(Tenant.name.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Step 3.3 aggregate-shaped methods
    # ------------------------------------------------------------------

    async def list_with_aggregates(
        self,
        session: AsyncSession,
        *,
        tier: TenantTier | None = None,
        search: str | None = None,
        sort: str = DEFAULT_TENANTS_SORT,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[TenantListRow], int]:
        """Return ``(rows, total)`` under the caller's RLS context.

        ``rows`` carries each visible Tenant ORM object with its
        per-row ``num_stores`` and ``num_users_active`` aggregates.
        Both queries inherit RLS via the session GUCs; no special
        handling needed here.

        Filters (``tier``, ``search``) apply to both the count and the
        page query, so ``total`` matches the filter set.

        Sort: one of ``TENANTS_SORT_KEYS`` (10 keys total — 6 column-
        based, 4 aggregate-based). Default ``created_at_desc`` mirrors
        PlatformUsersRepo / TenantUsersRepo. Stable secondary sort by
        ``Tenant.id ASC`` so identical primary-sort values page
        deterministically — relevant for ``num_*`` keys where ties are
        common (e.g., several tenants with 0 active users).

        Aggregate sort clauses reference the same scalar subqueries
        used in the SELECT list. SQLAlchemy inlines the subquery in
        both places, which means PG executes it twice per outer row;
        at v0 fleet scale (7 tenants) the cost is negligible. Both
        executions inherit the same RLS via session GUCs, so the sort
        is RLS-correct in both PLATFORM and TENANT contexts.
        """
        if sort not in TENANTS_SORT_KEYS:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")

        conditions = []
        if tier is not None:
            conditions.append(Tenant.tier == tier)
        if search is not None:
            pat = f"%{search}%"
            conditions.append(
                or_(
                    Tenant.name.ilike(pat),
                    Tenant.display_code.ilike(pat),
                    Tenant.contact_email.ilike(pat),
                )
            )

        # Count query: same WHERE, no LIMIT/OFFSET. Inherits RLS.
        count_stmt = select(func.count()).select_from(Tenant)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total_result = await session.execute(count_stmt)
        total: int = total_result.scalar_one()

        # Per-row aggregate subqueries. Each scalar subquery
        # correlates to the outer Tenant row via .correlate(Tenant)
        # so it filters per-row, not all-rows-at-once. Each subquery
        # inherits RLS independently from the outer select.
        num_stores_subq = (
            select(func.count(Store.id))
            .where(Store.tenant_id == Tenant.id)
            .correlate(Tenant)
            .scalar_subquery()
        )
        num_users_active_subq = (
            select(func.count(TenantUser.id))
            .where(
                TenantUser.tenant_id == Tenant.id,
                TenantUser.status == TenantUserStatus.ACTIVE,
            )
            .correlate(Tenant)
            .scalar_subquery()
        )

        # Resolve the sort key to a SQL ORDER BY clause. Column-based
        # keys come from the module-level dict; aggregate keys are
        # built here from the per-call subqueries.
        if sort in _BASE_TENANTS_SORT_MAP:
            primary_order: Any = _BASE_TENANTS_SORT_MAP[sort]
        elif sort == "num_users_active_asc":
            primary_order = num_users_active_subq.asc()
        elif sort == "num_users_active_desc":
            primary_order = num_users_active_subq.desc()
        elif sort == "num_stores_asc":
            primary_order = num_stores_subq.asc()
        else:  # sort == "num_stores_desc" — exhaustive per TENANTS_SORT_KEYS
            primary_order = num_stores_subq.desc()

        stmt = (
            select(
                Tenant,
                num_stores_subq.label("num_stores"),
                num_users_active_subq.label("num_users_active"),
                _modules_subq().label("modules"),
            )
            .order_by(primary_order, Tenant.id.asc())
            .limit(limit)
            .offset(offset)
        )
        if conditions:
            stmt = stmt.where(*conditions)

        page_result = await session.execute(stmt)
        rows = [
            TenantListRow(
                tenant=t,
                num_stores=ns,
                num_users_active=nua,
                modules=mods,
            )
            for (t, ns, nua, mods) in page_result.all()
        ]
        return rows, total

    async def get_by_id_with_aggregates(
        self,
        session: AsyncSession,
        tenant_id: UUID,
    ) -> TenantDetailRow | None:
        """Single-row variant of ``list_with_aggregates``.

        RLS-filtered or genuinely-missing rows both surface as
        ``None`` per D-17; the router converts to 404.
        """
        num_stores_subq = (
            select(func.count(Store.id))
            .where(Store.tenant_id == Tenant.id)
            .correlate(Tenant)
            .scalar_subquery()
        )
        num_users_active_subq = (
            select(func.count(TenantUser.id))
            .where(
                TenantUser.tenant_id == Tenant.id,
                TenantUser.status == TenantUserStatus.ACTIVE,
            )
            .correlate(Tenant)
            .scalar_subquery()
        )
        stmt = (
            select(
                Tenant,
                num_stores_subq.label("num_stores"),
                num_users_active_subq.label("num_users_active"),
                _modules_subq().label("modules"),
            )
            .where(Tenant.id == tenant_id)
        )
        result = await session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        tenant_obj, num_stores, num_users_active, modules = row
        return TenantDetailRow(
            tenant=tenant_obj,
            num_stores=num_stores,
            num_users_active=num_users_active,
            modules=modules,
        )

    async def count_for_stats(
        self,
        session: AsyncSession,
    ) -> tuple[int, int]:
        """Return ``(total_tenants, total_stores)`` under the caller's RLS context.

        For PLATFORM callers the counts reflect platform totals; for
        TENANT callers they reflect what the tenant can see (typically
        1 tenant and that tenant's store count).
        """
        tenants_result = await session.execute(
            select(func.count()).select_from(Tenant)
        )
        total_tenants: int = tenants_result.scalar_one()
        stores_result = await session.execute(
            select(func.count()).select_from(Store)
        )
        total_stores: int = stores_result.scalar_one()
        return total_tenants, total_stores

    # ------------------------------------------------------------------
    # Step 6.11.1 write methods
    # ------------------------------------------------------------------
    #
    # All three methods use raw ``text()`` SQL with explicit schema
    # qualification per the convention. The handler owns transaction
    # scope: ``get_tenant_session_dep`` opens one transaction per
    # request and commits on clean exit. Method bodies issue statements
    # but never COMMIT or ROLLBACK.
    #
    # ``create`` writes one row to ``tenants`` and zero-or-more rows to
    # ``tenant_module_access`` in the same transaction. The session
    # carries ``app.tenant_id=NULL`` / ``app.user_type='PLATFORM'`` per
    # AI-MT-03; D-29's OR-branch on both tables admits the writes.
    #
    # ``update`` and ``transition`` UPDATE ``tenants``. RLS-as-404
    # surfaces via ``None`` return / ``TransitionResult.NOT_FOUND``.

    async def _raise_if_name_taken(
        self,
        session: AsyncSession,
        name: str,
        *,
        exclude_tenant_id: UUID | None,
    ) -> None:
        """App-layer uniqueness check on ``tenants.name``.

        v0 has no DB-level UNIQUE on ``name`` (see FN-AB on tenant name
        UNIQUE). Race window non-zero under concurrency; the check is
        SELECT-then-INSERT/UPDATE in the same transaction. When the
        UNIQUE constraint forward note lands, the same query benefits
        from the unique index.

        ``exclude_tenant_id`` lets ``update`` skip the rename-to-self
        case: a PATCH that keeps the name unchanged would otherwise
        always reject.
        """
        schema = get_settings().db_schema
        if exclude_tenant_id is None:
            row = await session.execute(
                text(
                    f"SELECT 1 FROM {schema}.tenants "
                    "WHERE name = :name LIMIT 1"
                ),
                {"name": name},
            )
        else:
            row = await session.execute(
                text(
                    f"SELECT 1 FROM {schema}.tenants "
                    "WHERE name = :name AND id != :exclude_id LIMIT 1"
                ),
                {"name": name, "exclude_id": exclude_tenant_id},
            )
        if row.first() is not None:
            raise DuplicateTenantNameError(
                f"tenant name already taken: {name!r}",
                name=name,
            )

    async def create(
        self,
        session: AsyncSession,
        *,
        name: str,
        region: str,
        tier: str,
        industry: str,
        country: str,
        primary_contact_name: str,
        contact_email: str,
        number_of_stores: int,
        number_of_stores_as_of_date: date,
        display_code: str | None,
        monthly_revenue_usd: Decimal | None,
        monthly_revenue_as_of_date: date | None,
        modules_enabled: list[ModuleCode],
        actor_user_id: UUID,
        auth: AuthContext | None = None,
        request_id: UUID | None = None,
    ) -> TenantDetailRow:
        """Insert one ``tenants`` row + N ``tenant_module_access`` rows.

        Server-forces ``status='TRIAL'`` per locked decision 3.
        ``actor_user_id`` is the JWT user_id; it must be a valid
        ``platform_users.id`` (Pattern (a) FK per D-13). The PATCH /
        POST surface is platform-only at Step 6.11 so this is satisfied
        by construction; FK violation would surface as 500 if a
        non-PLATFORM caller somehow reached this method.

        Caller has already deduped and force-included ADMIN in
        ``modules_enabled`` via the Pydantic schema validator.

        Raises ``DuplicateTenantNameError`` (409) if a tenant with
        ``name`` already exists.

        Step 6.20.1: also inserts the tenant-root ``org_nodes`` row in
        the same transaction. The row's ``(code, path)`` is derived from
        ``display_code`` (if provided) else ``name`` via
        ``slug_for_tenant_root``. Empty-slug input raises
        ``InvalidTenantNameForSlugError`` (422) before the ``tenants``
        INSERT, so a 422 leaves no partial state behind. The org_node
        row is the load-bearing anchor every ``get_tenant_anchor``-gated
        endpoint relies on; its absence was the original bug.
        """
        schema = get_settings().db_schema

        await self._raise_if_name_taken(
            session, name, exclude_tenant_id=None
        )

        # Derive (code, path) BEFORE the tenants INSERT so a 422 from an
        # empty slug rejects the request without side effects. Per
        # refined LD2 in the Step 6.20.1 impl prompt.
        org_node_code, org_node_path = slug_for_tenant_root(
            name, display_code
        )

        insert_tenant = await session.execute(
            text(
                f"""
                INSERT INTO {schema}.tenants (
                    name, region, tier, industry, country,
                    primary_contact_name, contact_email,
                    number_of_stores, number_of_stores_as_of_date,
                    display_code,
                    monthly_revenue_usd, monthly_revenue_as_of_date,
                    status,
                    created_by_user_id, updated_by_user_id
                ) VALUES (
                    :name,
                    CAST(:region AS {schema}.tenant_region_enum),
                    CAST(:tier AS {schema}.tenant_tier_enum),
                    CAST(:industry AS {schema}.tenant_industry_enum),
                    :country,
                    :primary_contact_name, :contact_email,
                    :number_of_stores, :number_of_stores_as_of_date,
                    :display_code,
                    :monthly_revenue_usd, :monthly_revenue_as_of_date,
                    CAST('TRIAL' AS {schema}.tenant_status_enum),
                    :actor, :actor
                )
                RETURNING id
                """
            ),
            {
                "name": name,
                "region": region,
                "tier": tier,
                "industry": industry,
                "country": country,
                "primary_contact_name": primary_contact_name,
                "contact_email": contact_email,
                "number_of_stores": number_of_stores,
                "number_of_stores_as_of_date": number_of_stores_as_of_date,
                "display_code": display_code,
                "monthly_revenue_usd": monthly_revenue_usd,
                "monthly_revenue_as_of_date": monthly_revenue_as_of_date,
                "actor": actor_user_id,
            },
        )
        new_tenant_id: UUID = insert_tenant.scalar_one()

        # Step 6.20.1: tenant-root org_node row. Same transaction as the
        # tenants INSERT above and the per-module loop below; rollback
        # on any failure leaves no partial state. Pattern (b) audit-actor
        # pair per D-13 / LD7; ``app.user_type='PLATFORM'`` is set by
        # ``get_tenant_session`` and the gate's ``audience='PLATFORM'``
        # invariant means the actor is always a PLATFORM user here.
        # ``ck_org_nodes_root_parent_consistency`` enforces the
        # ``node_type='TENANT' <-> parent_id IS NULL`` invariant.
        await session.execute(
            text(
                f"""
                INSERT INTO {schema}.org_nodes (
                    tenant_id, parent_id, path, node_type,
                    name, code, status,
                    created_by_user_id, created_by_user_type,
                    updated_by_user_id, updated_by_user_type
                ) VALUES (
                    :tenant_id, NULL,
                    CAST(:path AS ltree),
                    CAST('TENANT' AS {schema}.org_node_type_enum),
                    :name, :code,
                    CAST('ACTIVE' AS {schema}.org_node_status_enum),
                    :actor, CAST('PLATFORM' AS {schema}.actor_user_type_enum),
                    :actor, CAST('PLATFORM' AS {schema}.actor_user_type_enum)
                )
                """
            ),
            {
                "tenant_id": new_tenant_id,
                "path": org_node_path,
                "name": name,
                "code": org_node_code,
                "actor": actor_user_id,
            },
        )

        # Per-module INSERT loop. Module counts per tenant are 1-6 at
        # v0 scale; a per-row loop is clearer than building a multi-row
        # VALUES clause and stays uniform with the existing seed loader.
        for module in modules_enabled:
            await session.execute(
                text(
                    f"""
                    INSERT INTO {schema}.tenant_module_access (
                        tenant_id, module, status,
                        enabled_at, enabled_by_user_id,
                        created_by_user_id, updated_by_user_id
                    ) VALUES (
                        :tenant_id,
                        CAST(:module AS {schema}.module_code_enum),
                        CAST('ENABLED' AS {schema}.module_access_status_enum),
                        now(), :actor,
                        :actor, :actor
                    )
                    """
                ),
                {
                    "tenant_id": new_tenant_id,
                    "module": module.value,
                    "actor": actor_user_id,
                },
            )

        # Flush so the aggregate-shaped read sees the writes (the
        # session has not committed yet, the request-scope session
        # commits on clean handler return).
        await session.flush()

        result_row = await self.get_by_id_with_aggregates(
            session, new_tenant_id
        )
        # The row was just inserted under PLATFORM session; D-29
        # OR-branch admits read. Cannot be None unless RLS is broken;
        # assertion guards against that.
        assert result_row is not None, (
            f"freshly-created tenant {new_tenant_id} not visible to "
            "the same session"
        )

        # Step 6.16.2 audit emission. Success row goes to
        # platform_activity_audit_logs per the design-doc-named
        # exception (route_to_platform=True). Same-transaction with
        # the data write per LD7. Both `auth` and `request_id` are
        # required together: providing only one is a developer bug;
        # repo-level tests that pass neither skip emission cleanly.
        if auth is not None and request_id is not None:
            snapshot = {
                "id": new_tenant_id,
                "name": name,
                "region": region,
                "tier": tier,
                "industry": industry,
                "country": country,
                "status": "TRIAL",
                "modules_enabled": [m.value for m in modules_enabled],
            }
            await emit_audit_event(
                session,
                auth=auth,
                action="CREATE",
                resource_type="TENANT",
                resource_id=new_tenant_id,
                resource_label=name,
                result_type=AuditResultType.SUCCESS,
                details=build_success_details_for_create(snapshot),
                tenant_id=new_tenant_id,
                tenant_name=name,
                request_id=request_id,
                route_to_platform=True,
            )
        elif auth is not None or request_id is not None:
            raise ValueError(
                "auth and request_id must be provided together for audit "
                "emission, or both omitted for repo-level test paths"
            )

        return result_row

    async def update(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        *,
        fields: dict[str, Any],
        actor_user_id: UUID,
        auth: AuthContext | None = None,
        request_id: UUID | None = None,
    ) -> TenantDetailRow | None:
        """Partial update of one ``tenants`` row.

        ``fields`` is the caller's ``exclude_unset=True`` dump of the
        Pydantic patch body — keys are column names; values are the
        new values (including ``None`` to clear nullable columns).

        Returns ``None`` when the row is missing or RLS-filtered
        (RLS-as-404 per D-17). Caller raises ``TenantNotFoundError`` on
        ``None``.

        Raises ``DuplicateTenantNameError`` (409) when ``name`` is in
        ``fields`` and another tenant already has that name. The check
        excludes ``tenant_id`` from the pre-check so rename-to-self is
        a no-op (200 OK with no name change).

        Caller (the handler) is responsible for the empty-body check
        before invoking; the repo trusts ``fields`` to be non-empty.
        """
        schema = get_settings().db_schema

        # Allowlist: columns the patch may touch. Belt-and-suspenders
        # against the schema's ``extra="forbid"``; protects against an
        # accidental future schema-loosening from blowing through to
        # the SQL.
        allowed_keys: frozenset[str] = frozenset({
            "name",
            "display_code",
            "country",
            "tier",
            "industry",
            "primary_contact_name",
            "contact_email",
            "monthly_revenue_usd",
            "monthly_revenue_as_of_date",
            "number_of_stores",
            "number_of_stores_as_of_date",
        })
        invalid = set(fields.keys()) - allowed_keys
        if invalid:
            raise ValueError(
                f"unexpected update fields: {sorted(invalid)!r}"
            )

        if "name" in fields:
            await self._raise_if_name_taken(
                session, fields["name"], exclude_tenant_id=tenant_id
            )

        # Capture BEFORE-update snapshot for the audit row. Read only
        # the columns that are about to be modified so the audit row
        # records exactly the diff. None if the row is missing /
        # RLS-filtered (the UPDATE below returns no rows and we exit
        # without emission).
        before_values: dict[str, Any] = {}
        if auth is not None and request_id is not None:
            before_select_cols = ", ".join(fields.keys())
            before_result = await session.execute(
                text(
                    f"SELECT {before_select_cols} FROM {schema}.tenants "
                    "WHERE id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
            before_row = before_result.mappings().first()
            if before_row is not None:
                before_values = dict(before_row)

        # Build per-column SET clauses with enum casts where the live
        # column type is a named PG enum. The tenants table has three:
        # tier, industry, status (status not in allowed_keys; transitions
        # go through the dedicated endpoints). region is immutable.
        _ENUM_CASTS: dict[str, str] = {
            "tier": "tenant_tier_enum",
            "industry": "tenant_industry_enum",
        }

        set_parts: list[str] = []
        params: dict[str, Any] = {"actor": actor_user_id, "tenant_id": tenant_id}
        for key in fields:
            enum_type = _ENUM_CASTS.get(key)
            if enum_type is not None:
                set_parts.append(f"{key} = CAST(:{key} AS {enum_type})")
            else:
                set_parts.append(f"{key} = :{key}")
            params[key] = fields[key]
        set_parts.append("updated_by_user_id = :actor")
        # updated_at is refreshed by the BEFORE-UPDATE trigger
        # ``tg_tenants_set_updated_at`` per the DDL; no need to set it
        # explicitly in the UPDATE clause.

        result = await session.execute(
            text(
                f"UPDATE {schema}.tenants SET {', '.join(set_parts)} "
                "WHERE id = :tenant_id RETURNING id"
            ),
            params,
        )
        if result.first() is None:
            return None

        # The raw UPDATE bypasses SA ORM, so any Tenant instance cached
        # in this session's identity map still carries stale attribute
        # values. Expire so the re-read returns fresh data rather than
        # the cached pre-update object.
        session.expire_all()
        result_row = await self.get_by_id_with_aggregates(session, tenant_id)

        # Step 6.16.2 audit emission. Normal routing (tenant_id set,
        # not the named exception). Same-transaction success row.
        if auth is not None and request_id is not None and result_row is not None:
            after_values = {k: fields[k] for k in fields}
            tenant_name_now = result_row.tenant.name
            await emit_audit_event(
                session,
                auth=auth,
                action="UPDATE",
                resource_type="TENANT",
                resource_id=tenant_id,
                resource_label=tenant_name_now,
                result_type=AuditResultType.SUCCESS,
                details=build_success_details_for_update(
                    before_values, after_values
                ),
                tenant_id=tenant_id,
                tenant_name=tenant_name_now,
                request_id=request_id,
                route_to_platform=False,
            )
        elif auth is not None or request_id is not None:
            if not (auth is not None and request_id is not None):
                raise ValueError(
                    "auth and request_id must be provided together"
                )

        return result_row

    async def transition(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        *,
        target_status: Literal["SUSPENDED", "ACTIVE"],
        actor_user_id: UUID,
        auth: AuthContext | None = None,
        request_id: UUID | None = None,
    ) -> tuple[TenantDetailRow | None, TransitionResult]:
        """Atomic status transition for one ``tenants`` row.

        Returns ``(row | None, result)``:
          - ``(None, NOT_FOUND)`` when the row is missing or RLS-filtered.
          - ``(None, INVALID_STATE)`` when current status doesn't permit
            the requested transition.
          - ``(row, OK)`` after a successful UPDATE.

        Allowed sources per locked decision 6:
          - SUSPENDED <= TRIAL or ACTIVE
          - ACTIVE    <= TRIAL or SUSPENDED

        SUSPENDED -> ACTIVE clears ``suspended_at`` and
        ``suspended_by_user_id`` atomically with the status flip
        (required by ``ck_tenants_suspended_consistency``).

        SELECT FOR UPDATE locks the row inside the request transaction
        so a concurrent suspend / activate doesn't race.
        """
        schema = get_settings().db_schema

        row = await session.execute(
            text(
                f"SELECT status FROM {schema}.tenants "
                "WHERE id = :tenant_id FOR UPDATE"
            ),
            {"tenant_id": tenant_id},
        )
        current = row.first()
        if current is None:
            return None, TransitionResult.NOT_FOUND

        allowed_sources: dict[str, frozenset[str]] = {
            "SUSPENDED": frozenset({"TRIAL", "ACTIVE"}),
            "ACTIVE": frozenset({"TRIAL", "SUSPENDED"}),
        }
        if current.status not in allowed_sources[target_status]:
            return None, TransitionResult.INVALID_STATE

        if target_status == "SUSPENDED":
            await session.execute(
                text(
                    f"""
                    UPDATE {schema}.tenants
                       SET status = CAST('SUSPENDED' AS {schema}.tenant_status_enum),
                           suspended_at = now(),
                           suspended_by_user_id = :actor,
                           updated_by_user_id = :actor
                     WHERE id = :tenant_id
                    """
                ),
                {"actor": actor_user_id, "tenant_id": tenant_id},
            )
        else:  # target_status == "ACTIVE"
            await session.execute(
                text(
                    f"""
                    UPDATE {schema}.tenants
                       SET status = CAST('ACTIVE' AS {schema}.tenant_status_enum),
                           suspended_at = NULL,
                           suspended_by_user_id = NULL,
                           updated_by_user_id = :actor
                     WHERE id = :tenant_id
                    """
                ),
                {"actor": actor_user_id, "tenant_id": tenant_id},
            )

        # Raw UPDATE bypasses SA ORM; expire so the in-session Tenant
        # identity-map entry doesn't return stale status / suspended_*.
        session.expire_all()
        result_row = await self.get_by_id_with_aggregates(
            session, tenant_id
        )

        # Step 6.16.2 audit emission. Normal routing (tenant_id set);
        # action is SUSPEND or ACTIVATE per the target. Same-transaction
        # success row.
        if auth is not None and request_id is not None and result_row is not None:
            action_code = (
                "SUSPEND" if target_status == "SUSPENDED" else "ACTIVATE"
            )
            tenant_name_now = result_row.tenant.name
            await emit_audit_event(
                session,
                auth=auth,
                action=action_code,
                resource_type="TENANT",
                resource_id=tenant_id,
                resource_label=tenant_name_now,
                result_type=AuditResultType.SUCCESS,
                details=build_success_details_for_transition(
                    before_status=str(current.status),
                    after_status=target_status,
                ),
                tenant_id=tenant_id,
                tenant_name=tenant_name_now,
                request_id=request_id,
                route_to_platform=False,
            )
        elif auth is not None or request_id is not None:
            if not (auth is not None and request_id is not None):
                raise ValueError(
                    "auth and request_id must be provided together"
                )

        return result_row, TransitionResult.OK
