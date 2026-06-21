"""Pydantic v2 schemas for the dashboard stats endpoints (Step 6.5).

Two endpoints back the Platform Dashboard's KPI grid (Frontend spec
7.1):

  - E1 ``GET /api/v1/dashboard/fleet-stats``      — KPIs 1-4
  - E2 ``GET /api/v1/dashboard/governance-stats`` — KPIs 5-8

Both endpoints return **card-shaped** response objects (deliberate
D-30 exception — the dashboard is a UI-shaped query bundle, not a
paginatable collection). Every card carries a common ``available``
flag plus card-specific aggregate fields. When ``available: false``
the card carries an ``unavailable_reason`` machine code from a fixed
v0 vocabulary; consumers MUST NOT read ``value`` as meaningful in
that case (it's a type-stable sentinel — typically ``0`` for
integer-valued cards).

Append-only per D-31: when a stub card flips to real, only
``available``, ``value``, and ``unavailable_reason`` change. Field
sets and types stay the same.

Both schemas use ``ConfigDict(extra="forbid")`` to guard against
accidental shape drift — a future PR adding an undocumented field
fails Pydantic validation immediately rather than silently shipping.

**MRR formatting note.** ``MrrAggregatedCard.value`` is a 2-decimal-
place string (e.g., ``"308100.00"``), produced via explicit
``f"{x:.2f}"`` formatting in the dashboard router. This differs from
``schemas/tenant.py``'s ``field_serializer("monthly_revenue_usd",
when_used="json")`` returning ``str(v)``: the tenants endpoints rely
on Postgres NUMERIC's canonical-string representation flowing through
the driver; the dashboard endpoint is an aggregate ``SUM(...)`` whose
Decimal precision can be ``Decimal('0E-2')`` or similar on edge
paths, so the explicit format is the safer guarantee for this
contract. Different contracts, different posture (Q2 confirmed at
Step 6.5 design review, 2026-05-06).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# DeltaBlock — common across card types that surface period-over-period change
# =============================================================================


class DeltaBlock(BaseModel):
    """Period-over-period delta on a card's primary value.

    When ``available: false``, ``value``, ``direction``, and ``window``
    may be ``null`` (consumers MUST NOT read them as meaningful).
    """

    model_config = ConfigDict(extra="forbid")

    value: int | None = Field(
        description=(
            "Magnitude of the change over the window. Sign convention "
            "matches direction. ``null`` when the card's delta is "
            "stub (``available: false``)."
        ),
    )
    direction: Literal["up", "down", "flat"] | None = Field(
        description=(
            "Derived from value: ``up`` if value > 0, ``down`` if "
            "value < 0, ``flat`` if value == 0. ``null`` when the "
            "delta is stub."
        ),
    )
    window: Literal["7d", "30d", "24h", "monthly"] | None = Field(
        description=(
            "The lookback window the delta is measured over. Card-"
            "specific (e.g., active-tenants uses 7d, platform-users "
            "uses 30d). ``null`` is permitted on stub deltas; see "
            "MrrAggregatedCard for the canonical example where "
            "window is preserved (``monthly``) even while the "
            "delta is unavailable."
        ),
    )
    available: bool = Field(
        description=(
            "True when the delta is real and consumable. False when "
            "the underlying snapshot data isn't yet shipped (e.g., "
            "MRR delta requires a per-period snapshot table not "
            "available in v0). Append-only contract: shape stays "
            "identical when this flips from false to true."
        ),
    )


# =============================================================================
# E1: fleet-stats cards
# =============================================================================


class ActiveTenantsCard(BaseModel):
    """KPI card 1: Active tenants — value/total + status breakdown sub_text.

    ``value`` counts ``status = 'ACTIVE'``. ``total`` counts ``status
    != 'TERMINATED'`` — i.e., all four non-terminated states (ONBOARDING,
    TRIAL, ACTIVE, SUSPENDED). The ``sub_text`` breaks out non-zero
    onboarding / trial / suspended segments in lifecycle order. Empty
    string when all three breakout segments are zero.

    Step 6.5 design-review amendment (2026-05-06): the sub_text
    vocabulary covers all five tenant_status values. The original
    prompt's locked rules covered only TRIAL and SUSPENDED; ONBOARDING
    was added at design-review time because it's a real product state
    (the lifecycle's first state) and silent rollup under-served the
    Super Admin reader.
    """

    model_config = ConfigDict(extra="forbid")

    value: int = Field(
        description=(
            "Count of tenants with status='ACTIVE'. RLS-scoped: a "
            "TENANT JWT receives 0 or 1."
        ),
    )
    total: int = Field(
        description=(
            "Count of tenants with status != 'TERMINATED' — covers "
            "the four non-terminated states (ONBOARDING, TRIAL, "
            "ACTIVE, SUSPENDED). Treat as 'visible total' under "
            "the caller's RLS scope."
        ),
    )
    sub_text: str = Field(
        description=(
            'Backend-formatted breakdown. Format: "<n> onboarding · '
            '<m> trial · <k> suspended" with each segment present '
            'only when its count > 0. Empty string when all three '
            'are zero. Lifecycle ordering: onboarding → trial → '
            "suspended."
        ),
    )
    delta: DeltaBlock = Field(
        description=(
            "7d delta: count of non-terminated tenants created in "
            "the last 7 days. v0 semantic is 'new entities created "
            "in window', NOT 'net active-count change' (which would "
            "require snapshots)."
        ),
    )
    available: bool = Field(
        description="Always true on this card.",
    )


class PlatformUsersCard(BaseModel):
    """KPI card 2: Platform users (active tenant_users count).

    Field name retained even on TENANT side for response-shape
    consistency. The frontend can rename per persona; backend returns
    the same field.
    """

    model_config = ConfigDict(extra="forbid")

    value: int = Field(
        description=(
            "Count of tenant_users with status='ACTIVE'. RLS-scoped: "
            "TENANT JWT sees only own-tenant users."
        ),
    )
    sub_text: str = Field(
        description=(
            "Scope-aware. PLATFORM: 'across all tenants'. TENANT: "
            "'in your organization'."
        ),
    )
    delta: DeltaBlock = Field(
        description=(
            "30d delta: count of ACTIVE tenant_users created in the "
            "last 30 days."
        ),
    )
    available: bool = Field(description="Always true on this card.")


class StoresCard(BaseModel):
    """KPI card 3: Stores under management. No delta block."""

    model_config = ConfigDict(extra="forbid")

    value: int = Field(
        description=(
            "Count of stores. RLS-scoped: TENANT JWT sees only own-"
            "tenant stores."
        ),
    )
    distinct_countries: int = Field(
        description=(
            "Count of distinct ``country`` values across the visible "
            "stores. RLS-scoped: TENANT JWT typically gets 1 (or 0 "
            "if the tenant has no stores)."
        ),
    )
    sub_text: str = Field(
        description=(
            'Backend-formatted: "<N> country" (singular when N=1) or '
            '"<N> countries" otherwise. Same rule for both user types.'
        ),
    )
    delta: None = Field(
        default=None,
        description=(
            "No delta on this card. Field is reserved at type-level "
            "(always ``null``) so the card shape stays in the common "
            "card family (``value``, ``sub_text``, ``delta``, "
            "``available``) without requiring frontend special-cases."
        ),
    )
    available: bool = Field(description="Always true on this card.")


class MrrAggregatedCard(BaseModel):
    """KPI card 4: Aggregated tenant MRR.

    ``value`` is a 2-decimal-place string (e.g., ``"308100.00"``)
    formatted by the router via ``f"{x:.2f}"``. This differs from
    ``schemas/tenant.py``'s ``field_serializer`` returning ``str(v)``
    on per-row monthly_revenue_usd — see module docstring for the
    rationale.

    The delta is permanently stubbed in v0 (no per-period MRR
    snapshot table exists). Tracked as MRR-DELTA-REAL forward note.
    """

    model_config = ConfigDict(extra="forbid")

    value: str = Field(
        description=(
            "Aggregated MRR in the response currency, formatted as a "
            "2-decimal-place string (e.g., '308100.00'). RLS-scoped: "
            "TENANT JWT sees own-tenant MRR. Empty visible-tenant "
            "set returns '0.00'."
        ),
    )
    currency: str = Field(
        description="Currency code. Always 'USD' in v0.",
    )
    sub_text: str = Field(
        description="Always 'recurring' for both user types in v0.",
    )
    delta: DeltaBlock = Field(
        description=(
            "Stubbed in v0: ``available: false``, ``value: null``, "
            "``direction: null``, ``window: 'monthly'``. Window is "
            "preserved (the intended cadence) so the shape doesn't "
            "change when MRR-DELTA-REAL ships."
        ),
    )
    available: bool = Field(description="Always true on this card.")


class FleetStatsResponse(BaseModel):
    """E1 response: 4 cards, no envelope wrapper (D-30 exception)."""

    model_config = ConfigDict(extra="forbid")

    active_tenants: ActiveTenantsCard
    platform_users: PlatformUsersCard
    stores: StoresCard
    mrr_aggregated: MrrAggregatedCard


# =============================================================================
# E2: governance-stats cards
# =============================================================================


# v0 unavailable_reason vocabulary. Fixed set; new codes land as
# forward notes resolve (PENDING-APPROVALS-REAL, GUARDRAILS-FIRED-REAL,
# CUSTOM-ROLES-REAL).
UnavailableReasonCode = Literal[
    "approvals_table_not_built",
    "audit_logs_or_guardrails_not_wired",
    "custom_role_creation_not_shipped",
]


class PendingApprovalsCard(BaseModel):
    """KPI card 5: Pending approvals across guardrails.

    Stubbed in v0: no approvals table exists. ``unavailable_reason:
    'approvals_table_not_built'``.
    """

    model_config = ConfigDict(extra="forbid")

    value: int = Field(
        description=(
            "Count of pending approvals. Type-stable sentinel ``0`` "
            "while ``available: false`` — consumers MUST NOT read "
            "this as meaningful in v0."
        ),
    )
    sub_text: str = Field(
        description=(
            "Scope-aware. PLATFORM: 'across guardrails'. TENANT: "
            "'across your organization'."
        ),
    )
    delta: None = Field(
        default=None,
        description="No delta on this card.",
    )
    available: bool = Field(description="False in v0.")
    unavailable_reason: UnavailableReasonCode | None = Field(
        default=None,
        description=(
            "Machine code explaining why the card is unavailable. "
            "Fixed v0 vocabulary documented in dashboard.md."
        ),
    )


class GuardrailsFired24hCard(BaseModel):
    """KPI card 6: Guardrails fired in the last 24h.

    Stubbed in v0: audit_logs ships at Step 6.2 and guardrail-fire
    events aren't yet emitted. ``unavailable_reason:
    'audit_logs_or_guardrails_not_wired'``.
    """

    model_config = ConfigDict(extra="forbid")

    value: int = Field(
        description=(
            "Count of guardrail fires in the last 24h. Type-stable "
            "sentinel ``0`` while ``available: false``."
        ),
    )
    escalations: int = Field(
        description=(
            "Of the fires, how many escalated. Type-stable sentinel "
            "``0`` while ``available: false``."
        ),
    )
    sub_text: str = Field(
        description=(
            'Format: "<N> escalations". Stub literal "0 escalations" '
            "in v0; same format applies once real."
        ),
    )
    delta: None = Field(
        default=None,
        description="No delta on this card.",
    )
    available: bool = Field(description="False in v0.")
    unavailable_reason: UnavailableReasonCode | None = Field(
        default=None,
        description="Machine code explaining unavailability.",
    )


class CustomRolesCard(BaseModel):
    """KPI card 7: Custom roles (non-system) over total roles.

    Stubbed in v0: Step 6.1 shipped RBAC *read* endpoints (the
    ``roles`` table is reachable, queryable, RLS-correct), but the
    *write* surface to create custom roles is not on the v0 plan.
    With no path to create them, ``COUNT(*) FILTER (WHERE is_system =
    false)`` is structurally zero — flipping ``available: true`` while
    the count cannot meaningfully change misrepresents platform state.
    Stays stubbed until the create-custom-role write surface ships.
    ``unavailable_reason: 'custom_role_creation_not_shipped'``.
    """

    model_config = ConfigDict(extra="forbid")

    value: int = Field(
        description=(
            "Count of non-system roles. Type-stable sentinel ``0`` "
            "while ``available: false`` (custom-role write surface "
            "not shipped)."
        ),
    )
    total: int = Field(
        description=(
            "Total count of roles (system + custom). Type-stable "
            "sentinel ``0`` while ``available: false``."
        ),
    )
    sub_text: str = Field(
        description='Format: "of <total> total". Same for both user types.',
    )
    delta: None = Field(
        default=None,
        description="No delta on this card.",
    )
    available: bool = Field(description="False in v0.")
    unavailable_reason: UnavailableReasonCode | None = Field(
        default=None,
        description="Machine code explaining unavailability.",
    )


class ModulesDeployedCard(BaseModel):
    """KPI card 8: Modules deployed.

    Real in v0: queries ``tenant_module_access`` filtered to
    ``status = 'ENABLED'``. RLS-scoped: PLATFORM JWT counts across
    all tenants; TENANT JWT counts within own tenant.
    """

    model_config = ConfigDict(extra="forbid")

    value: int = Field(
        description=(
            "Count of ENABLED tenant_module_access rows. RLS-scoped."
        ),
    )
    sub_text: str = Field(
        description=(
            "Scope-aware. PLATFORM: 'across <N> tenant(s)' "
            "(singular/plural). TENANT: 'enabled for your "
            "organization'."
        ),
    )
    delta: None = Field(
        default=None,
        description="No delta on this card.",
    )
    available: bool = Field(description="True in v0.")


class GovernanceStatsResponse(BaseModel):
    """E2 response: 4 cards, no envelope wrapper (D-30 exception)."""

    model_config = ConfigDict(extra="forbid")

    pending_approvals: PendingApprovalsCard
    guardrails_fired_24h: GuardrailsFired24hCard
    custom_roles: CustomRolesCard
    modules_deployed: ModulesDeployedCard
