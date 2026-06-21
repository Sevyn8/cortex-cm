"""DashboardRepo — read-only data access for the dashboard stats endpoints.

The dashboard isn't a CRUD resource; it's a UI-shaped query bundle.
This Repo deliberately departs from the one-Repo-per-resource shape
used elsewhere (TenantsRepo, PlatformUsersRepo, etc.) — the cohesion
of "all dashboard queries in one place" outweighs the consistency
cost. The endpoints under ``/api/v1/dashboard/*`` are aggregate-only,
RLS-driven, and persona-projected; they're better grouped by their
shared product surface than by the underlying tables they touch.

Two methods:

  - ``fleet_stats(session)`` — single CTE producing 4 cards' worth
    of aggregates (active tenants, platform users, stores, MRR).
    One Postgres round-trip.
  - ``governance_stats(session)`` — single small query for the only
    real card (``modules_deployed``); the other 3 governance cards
    are returned as constants by the router.

Visibility flows from session GUCs (D-29 OR-clause on each
underlying RLS-protected table). PLATFORM JWT sees fleet-wide
aggregates; TENANT JWT sees own-tenant aggregates. Same SQL runs
for both user types — RLS does the persona projection.

Step 6.5 amendment (2026-05-06): the ``tenants.status`` enum has
**five** values (ONBOARDING, TRIAL, ACTIVE, SUSPENDED, TERMINATED),
not the four the prompt's CTE assumed. The CTE adds an explicit
``onboarding`` filter so the router's sub_text helper can break out
ONBOARDING as a distinct lifecycle segment. ``total`` continues to
use ``status != 'TERMINATED'``, which already covered ONBOARDING
correctly via the broad filter.

Step 6.5.1 amendment (2026-05-06): both ``text()`` queries
schema-qualify every table reference via ``get_settings().db_schema``
interpolation, mirroring ``repositories/permission_matrix.py``.
Pre-Step-6.5.1 the queries were module-level constants with
unqualified table names; they worked locally because the role-
default search_path included ``core``, but failed on Cloud SQL
with ``relation "tenants" does not exist``. Schema qualification
removes the search_path dependency. See CLAUDE.md "Note on raw
text() SQL".
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.config import get_settings


# =============================================================================
# Aggregate row carriers
# =============================================================================


@dataclass(frozen=True)
class FleetStatsRow:
    """Carrier for the fleet-stats CTE's single-row result.

    All fields RLS-scoped: under PLATFORM JWT these are platform-wide
    aggregates; under TENANT JWT they're own-tenant aggregates (the
    inner SELECTs inherit RLS from the session GUCs).

    ``mrr_sum`` is normalised to ``Decimal`` on construction; the
    driver may return ``Decimal``, ``float``, ``int``, or ``str``
    depending on driver/dialect quirks for ``SUM(NUMERIC)``. The
    router formats this with ``f"{x:.2f}"`` — explicit 2dp regardless
    of input precision.
    """

    tenants_active: int
    tenants_onboarding: int
    tenants_trial: int
    tenants_suspended: int
    tenants_total: int
    tenants_new_7d: int
    mrr_sum: Decimal
    users_active: int
    users_new_30d: int
    stores_total: int
    stores_distinct_countries: int


@dataclass(frozen=True)
class GovernanceStatsRow:
    """Carrier for the governance-stats real aggregates.

    Only ``modules_deployed`` data lives here. The 3 stub cards
    (``pending_approvals``, ``guardrails_fired_24h``, ``custom_roles``)
    are returned as constants by the router; this row carries no
    fields for them. As each forward note resolves
    (PENDING-APPROVALS-REAL, GUARDRAILS-FIRED-REAL, CUSTOM-ROLES-REAL),
    fields land here.
    """

    modules_enabled: int
    modules_visible_tenant_count: int


# =============================================================================
# DashboardRepo
# =============================================================================


class DashboardRepo:
    """Read-only repository for dashboard stats."""

    async def fleet_stats(
        self, session: AsyncSession
    ) -> FleetStatsRow:
        """Run the fleet-stats CTE; return the single-row aggregate carrier.

        The session must already carry ``app.tenant_id`` /
        ``app.user_type`` GUCs (set by ``get_tenant_session``); RLS
        filters each inner SELECT independently.

        Schema-qualified per Step 6.5.1 — every table reference uses
        the configured ``db_schema`` rather than relying on
        ``search_path``. See CLAUDE.md "Note on raw text() SQL".
        """
        schema = get_settings().db_schema
        sql = text(
            f"""
            WITH
            tenant_counts AS (
                SELECT
                    COUNT(*) FILTER (WHERE status = 'ACTIVE')                 AS active,
                    COUNT(*) FILTER (WHERE status = 'ONBOARDING')             AS onboarding,
                    COUNT(*) FILTER (WHERE status = 'TRIAL')                  AS trial,
                    COUNT(*) FILTER (WHERE status = 'SUSPENDED')              AS suspended,
                    COUNT(*) FILTER (WHERE status != 'TERMINATED')            AS total,
                    COUNT(*) FILTER (
                        WHERE status != 'TERMINATED'
                          AND created_at >= NOW() - INTERVAL '7 days'
                    )                                                         AS new_7d,
                    COALESCE(
                        SUM(monthly_revenue_usd) FILTER (WHERE status != 'TERMINATED'),
                        0
                    )                                                         AS mrr_sum
                FROM {schema}.tenants
            ),
            user_counts AS (
                SELECT
                    COUNT(*) FILTER (WHERE status = 'ACTIVE') AS active,
                    COUNT(*) FILTER (
                        WHERE status = 'ACTIVE'
                          AND created_at >= NOW() - INTERVAL '30 days'
                    ) AS new_30d
                FROM {schema}.tenant_users
            ),
            store_counts AS (
                SELECT
                    COUNT(*)                AS total,
                    COUNT(DISTINCT country) AS countries
                FROM {schema}.stores
            )
            SELECT
                tc.active     AS tenants_active,
                tc.onboarding AS tenants_onboarding,
                tc.trial      AS tenants_trial,
                tc.suspended  AS tenants_suspended,
                tc.total      AS tenants_total,
                tc.new_7d     AS tenants_new_7d,
                tc.mrr_sum    AS mrr_sum,
                uc.active     AS users_active,
                uc.new_30d    AS users_new_30d,
                sc.total      AS stores_total,
                sc.countries  AS stores_distinct_countries
            FROM tenant_counts tc, user_counts uc, store_counts sc
            """
        )
        result = await session.execute(sql)
        row = result.one()
        # `mrr_sum` may come back as Decimal, int, or other numeric
        # depending on driver behaviour for SUM(NUMERIC) over a possibly
        # empty filter. Normalise to Decimal so the dataclass field
        # type holds (and the router formats consistently).
        mrr_raw = row.mrr_sum
        if isinstance(mrr_raw, Decimal):
            mrr_decimal = mrr_raw
        else:
            mrr_decimal = Decimal(str(mrr_raw))

        return FleetStatsRow(
            tenants_active=int(row.tenants_active),
            tenants_onboarding=int(row.tenants_onboarding),
            tenants_trial=int(row.tenants_trial),
            tenants_suspended=int(row.tenants_suspended),
            tenants_total=int(row.tenants_total),
            tenants_new_7d=int(row.tenants_new_7d),
            mrr_sum=mrr_decimal,
            users_active=int(row.users_active),
            users_new_30d=int(row.users_new_30d),
            stores_total=int(row.stores_total),
            stores_distinct_countries=int(row.stores_distinct_countries),
        )

    async def governance_stats(
        self, session: AsyncSession
    ) -> GovernanceStatsRow:
        """Run the governance-stats real-card query.

        Stub cards are not queried here — the router emits them as
        constants. As each forward note resolves, this method
        widens to cover the newly-real cards.

        Schema-qualified per Step 6.5.1 (see ``fleet_stats`` docstring).
        """
        schema = get_settings().db_schema
        sql = text(
            f"""
            SELECT
                COUNT(*)                  AS modules_enabled,
                COUNT(DISTINCT tenant_id) AS visible_tenant_count
            FROM {schema}.tenant_module_access
            WHERE status = 'ENABLED'
            """
        )
        result = await session.execute(sql)
        row = result.one()
        return GovernanceStatsRow(
            modules_enabled=int(row.modules_enabled),
            modules_visible_tenant_count=int(row.visible_tenant_count),
        )
