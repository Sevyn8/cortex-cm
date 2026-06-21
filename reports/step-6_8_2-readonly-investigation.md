# Read-only investigation — pre-Step 6.8.2 surface

Date: 2026-05-09
Scope: Q1-Q5 verbatim file content with line numbers. No edits, no
interpretation.

---

## Q1 — ActorUserType enum location

```
src/admin_backend/models/tenant_user.py:73:class ActorUserType(str, Enum):
```

`class UserRoleAssignmentStatus` — **zero matches** in
`src/admin_backend/`.

---

## Q2 — `src/admin_backend/models/_lightweight_stubs.py` full content

### Module docstring (lines 1-23)

```
 1	"""Lightweight ORM stubs for tables whose full ORM hasn't landed yet.
 2	
 3	Originally housed two stubs (``Store`` and ``TenantUser``) used by
 4	``TenantsRepo``'s correlated subqueries since Step 3.3. Step 5.2 landed
 5	the full ``TenantUser`` model at ``models/tenant_user.py`` and removed
 6	its stub. ``Store`` remains here until Step 4.5 ships the full Store
 7	model. Step 6.1 added a ``UserRoleAssignment`` stub for the user_count
 8	correlated subquery in ``RolesRepo`` (full model deferred until E4/E5
 9	land per BUILD_PLAN's Step 6.1 "Known follow-ups (RBAC)").
10	
11	Each stub declares only the columns referenced by the consuming Repo's
12	query. Adding columns the live table has is allowed; removing or
13	renaming any of these breaks the subquery.
14	
15	CRITICAL — Alembic-autogenerate trap. These stubs are DELIBERATELY
16	INCOMPLETE relative to their live tables. Pointing Alembic autogenerate
17	at ``Base.metadata`` while any stub exists would propose
18	``ALTER TABLE DROP`` statements for every column the stub doesn't
19	declare. ``migrations/env.py`` keeps ``target_metadata = None`` until
20	all stubs are gone. Do NOT "complete" a stub to make autogenerate
21	happy; the right fix is the full ORM model in the appropriate step.
22	Tracked discipline-wise via the FN-AB-15 regenerator-staleness note.
23	"""
```

### Module-level constants

```
34	_DB_SCHEMA = get_settings().db_schema
```

### Class `Store` (lines 37-44)

```
37	class Store(Base):
38	    """Lightweight stub for ``stores`` (full model at Step 4.5)."""
39	
40	    __tablename__ = "stores"
41	    __table_args__ = {"schema": _DB_SCHEMA}
42	
43	    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
44	    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
```

### Class `UserRoleAssignment` (lines 47-92)

```
47	class UserRoleAssignment(Base):
48	    """Lightweight stub for ``user_role_assignments`` (full model at E4/E5).
49	
50	    Used by ``RolesRepo``'s ``user_count`` correlated subquery (Step
51	    6.1). The full ORM lands when the first of E4 (assignments list)
52	    or E5 (assignment single-fetch) ships per BUILD_PLAN's Step 6.1
53	    "Known follow-ups (RBAC)" — at which point this stub gets removed
54	    entirely (same lifecycle as Step 3.3's TenantUser stub that
55	    Step 5.2 swapped out).
56	
57	    Columns declared:
58	      - ``id`` (PK, required by SA)
59	      - ``role_id`` (the join key for user_count's correlation to roles)
60	      - ``status`` (filter to ACTIVE assignments)
61	      - ``tenant_id`` (RLS-relevant; the table has RLS, and the
62	        IS-NULL-gated D-29 OR-clause needs this column to exist for
63	        the policy expression's WHERE-side to compile)
64	      - ``platform_user_id`` and ``tenant_user_id`` (the table's
65	        XOR CHECK makes them functionally non-optional; including
66	        them keeps SA from generating insert paths that would fail
67	        the CHECK)
68	
69	    Skipped: granted_at/revoked_at, granted_by/revoked_by audit-actor
70	    pairs, org_node_id, the partial UNIQUE indexes. None of these
71	    feature in the user_count read query. The full E4/E5 model adds
72	    them when its turn comes.
73	    """
74	
75	    __tablename__ = "user_role_assignments"
76	    __table_args__ = {"schema": _DB_SCHEMA}
77	
78	    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
79	    role_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
80	    tenant_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
81	    platform_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
82	    tenant_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
83	    status: Mapped[str] = mapped_column(
84	        PG_ENUM(
85	            "ACTIVE",
86	            "INACTIVE",
87	            name="user_role_assignment_status_enum",
88	            create_type=False,
89	            native_enum=True,
90	        ),
91	        nullable=False,
92	    )
```

No other classes in the file.

---

## Q3 — `_user_count_subquery` shape (`src/admin_backend/repositories/roles.py`)

### Helper definition (lines 58-75)

```
58	def _user_count_subquery() -> Any:
59	    """Correlated scalar subquery: COUNT(*) of ACTIVE assignments for
60	    the outer ``Role`` row.
61	
62	    ``.correlate(Role)`` is load-bearing — without it the count
63	    collapses to a platform-wide aggregate. The same pattern as Step
64	    3.3's ``num_users_active`` and Step 5.3's child-count subquery.
65	
66	    Returns a SA ScalarSelect that callers wrap in ``.label(...)``.
67	    """
68	    return (
69	        select(func.count())
70	        .select_from(UserRoleAssignment)
71	        .where(UserRoleAssignment.role_id == Role.id)
72	        .where(UserRoleAssignment.status == "ACTIVE")
73	        .correlate(Role)
74	        .scalar_subquery()
75	    )
```

### Call site in `list_grouped` — line 151; consumed in `select(...)` lines 152-158

```
151	            user_count_col = _user_count_subquery().label("user_count")
152	            stmt = (
153	                select(Role, user_count_col)
154	                .where(*conditions)
155	                .order_by(SORT_MAP[sort], Role.id.asc())
156	                .offset(offset)
157	                .limit(limit)
158	            )
```

### Call site in `get_by_id` — line 181; consumed in `select(...)` line 182

```
181	        user_count_col = _user_count_subquery().label("user_count")
182	        stmt = select(Role, user_count_col).where(Role.id == role_id)
```

### Import line for `UserRoleAssignment` (line 39)

```
39	from admin_backend.models._lightweight_stubs import UserRoleAssignment
```

---

## Q4 — `tests/integration/test_seed_loader.py`

### `EXPECTED_VISIBLE_COUNTS_PLATFORM` dict (lines 33-46)

```
33	EXPECTED_VISIBLE_COUNTS_PLATFORM = {
34	    "platform_users": 3,
35	    "tenants": 7,
36	    "org_nodes": 49,
37	    "stores": 25,
38	    "tenant_users": 17,
39	    "roles": 15,
40	    "permissions": 23,
41	    "role_permissions": 113,
42	    "tenant_module_access": 27,
43	    # user_role_assignments shows only the 3 PLATFORM-audience rows
44	    # under PLATFORM-without-impersonation visibility (D-29).
45	    "user_role_assignments": 3,
46	}
```

### `test_l2b_user_role_assignments_total_across_tenants` (lines 96-130)

```
 96	async def test_l2b_user_role_assignments_total_across_tenants(
 97	    platform_session, tenant_session_factory
 98	):
 99	    """user_role_assignments total = PLATFORM-audience + sum-per-tenant.
100	
101	    Validates the IS-NULL-gated form of D-29: PLATFORM-without-
102	    impersonation visibility is restricted to tenant_id-IS-NULL
103	    rows; TENANT-side rows show up only under each tenant's session.
104	    """
105	    # PLATFORM-audience count (PLATFORM session, no impersonation).
106	    result = await platform_session.execute(
107	        text(
108	            "SELECT count(*) FROM user_role_assignments "
109	            "WHERE tenant_id IS NULL"
110	        )
111	    )
112	    platform_audience = result.scalar_one()
113	
114	    # Per-tenant counts.
115	    result = await platform_session.execute(text("SELECT id FROM tenants"))
116	    tenant_ids = [r[0] for r in result.all()]
117	    tenant_side_total = 0
118	    for tid in tenant_ids:
119	        async with tenant_session_factory(tid) as session:
120	            r = await session.execute(
121	                text("SELECT count(*) FROM user_role_assignments")
122	            )
123	            tenant_side_total += r.scalar_one()
124	
125	    total = platform_audience + tenant_side_total
126	    assert total == EXPECTED_URA_TOTAL, (
127	        f"user_role_assignments total: PLATFORM-audience="
128	        f"{platform_audience} + tenant-side={tenant_side_total} "
129	        f"= {total}, expected {EXPECTED_URA_TOTAL}"
130	    )
```

### `test_l3_seed_sentinel_rows` (lines 134-200)

```
134	async def test_l3_seed_sentinel_rows(platform_session):
135	    """Spot-checks for known-tricky values across the seed."""
136	
137	    # Buc-ee's: ENTERPRISE tier; monthly_revenue_usd is the
138	    # snapshot value as a Decimal-shaped string.
139	    result = await platform_session.execute(
140	        text(
141	            "SELECT name, tier, monthly_revenue_usd::text "
142	            "FROM tenants WHERE name = 'Buc-ee''s'"
143	        )
144	    )
145	    row = result.one()
146	    assert row.name == "Buc-ee's"
147	    assert row.tier == "ENTERPRISE"
148	    assert row.monthly_revenue_usd is not None
149	
150	    # tenant_module_access for Buc-ee's: the loader synthesised
151	    # the audit-actor columns via the seed's universal "system actor"
152	    # (Anjali). All three audit-actor FKs MUST be populated.
153	    result = await platform_session.execute(
154	        text(
155	            "SELECT count(*) FROM tenant_module_access tma "
156	            "JOIN tenants t ON t.id = tma.tenant_id "
157	            "WHERE t.name = 'Buc-ee''s'"
158	        )
159	    )
160	    assert result.scalar_one() >= 5
161	
162	    result = await platform_session.execute(
163	        text(
164	            "SELECT count(*) FROM tenant_module_access "
165	            "WHERE enabled_by_user_id IS NULL "
166	            "OR created_by_user_id IS NULL "
167	            "OR updated_by_user_id IS NULL"
168	        )
169	    )
170	    assert result.scalar_one() == 0, (
171	        "Audit-actor synthesis didn't populate every row"
172	    )
173	
174	    # PLATFORM-audience user_role_assignments: tenant_id NULL,
175	    # platform_user_id populated. Verifies the dual-FK XOR for
176	    # PLATFORM-side rows.
177	    result = await platform_session.execute(
178	        text(
179	            "SELECT count(*) FROM user_role_assignments "
180	            "WHERE platform_user_id IS NOT NULL "
181	            "AND tenant_user_id IS NULL "
182	            "AND tenant_id IS NULL"
183	        )
184	    )
185	    assert result.scalar_one() >= 3, (
186	        "Expected at least 3 PLATFORM-audience role assignments"
187	    )
188	
189	    # org_nodes ltree paths: every non-root path begins with its
190	    # parent's path + '.'. Validated via a self-join.
191	    result = await platform_session.execute(
192	        text(
193	            "SELECT count(*) FROM org_nodes child "
194	            "JOIN org_nodes parent ON parent.id = child.parent_id "
195	            "WHERE NOT (child.path::text LIKE parent.path::text || '.%')"
196	        )
197	    )
198	    assert result.scalar_one() == 0, (
199	        "Some org_nodes have paths that don't start with parent.path"
200	    )
```

### All `user_role_assignments` references in the file

```
tests/integration/test_seed_loader.py:17:every row. ``user_role_assignments`` uses the IS-NULL-gated form
tests/integration/test_seed_loader.py:43:    # user_role_assignments shows only the 3 PLATFORM-audience rows
tests/integration/test_seed_loader.py:45:    "user_role_assignments": 3,
tests/integration/test_seed_loader.py:48:# Total rows in user_role_assignments across all tenants (the
tests/integration/test_seed_loader.py:82:    NULL). user_role_assignments visibility is IS-NULL-gated; see
tests/integration/test_seed_loader.py:96:async def test_l2b_user_role_assignments_total_across_tenants(
tests/integration/test_seed_loader.py:99:    """user_role_assignments total = PLATFORM-audience + sum-per-tenant.
tests/integration/test_seed_loader.py:108:            "SELECT count(*) FROM user_role_assignments "
tests/integration/test_seed_loader.py:121:                text("SELECT count(*) FROM user_role_assignments")
tests/integration/test_seed_loader.py:127:        f"user_role_assignments total: PLATFORM-audience="
tests/integration/test_seed_loader.py:174:    # PLATFORM-audience user_role_assignments: tenant_id NULL,
tests/integration/test_seed_loader.py:179:            "SELECT count(*) FROM user_role_assignments "
```

---

## Q5 — `tests/integration/test_rbac_router.py` helpers

### `_insert_active_platform_ura` (lines 138-172)

```
138	async def _insert_active_platform_ura(
139	    session_factory: Any,
140	    platform_auth: Any,
141	    *,
142	    role_id: UUID,
143	    platform_user_id: UUID,
144	) -> UUID:
145	    """Insert one ACTIVE platform-audience user_role_assignment row.
146	
147	    PLATFORM-audience URAs have tenant_id NULL and org_node_id NULL.
148	    The PLATFORM session's WITH CHECK admits the INSERT via the
149	    FN-AB-14 IS-NULL-gated OR-branch.
150	
151	    Returns the new id. Tests must clean up via DELETE in a fixture
152	    teardown — provided here as a one-off helper (not promoted to
153	    conftest because the full URA fixture lands with E4/E5).
154	    """
155	    new_id = uuid.uuid4()
156	    async for session in get_tenant_session(platform_auth, session_factory):
157	        await session.execute(
158	            text(
159	                "INSERT INTO user_role_assignments ("
160	                "  id, platform_user_id, tenant_user_id, role_id,"
161	                "  tenant_id, org_node_id, status,"
162	                "  granted_by_user_id, granted_by_user_type"
163	                ") VALUES ("
164	                "  :id, :pu_id, NULL, :role_id,"
165	                "  NULL, NULL,"
166	                "  CAST('ACTIVE' AS user_role_assignment_status_enum),"
167	                "  NULL, NULL"
168	                ")"
169	            ),
170	            {"id": new_id, "pu_id": platform_user_id, "role_id": role_id},
171	        )
172	    return new_id
```

### `_delete_uras_by_id` (lines 175-188)

```
175	async def _delete_uras_by_id(
176	    session_factory: Any,
177	    platform_auth: Any,
178	    ids: list[UUID],
179	) -> None:
180	    if not ids:
181	        return
182	    async for session in get_tenant_session(platform_auth, session_factory):
183	        await session.execute(
184	            text(
185	                "DELETE FROM user_role_assignments WHERE id = ANY(:ids)"
186	            ),
187	            {"ids": ids},
188	        )
```

### Call sites of both helpers in the same file

```
tests/integration/test_rbac_router.py:138:async def _insert_active_platform_ura(
tests/integration/test_rbac_router.py:175:async def _delete_uras_by_id(
tests/integration/test_rbac_router.py:315:            await _insert_active_platform_ura(
tests/integration/test_rbac_router.py:321:            await _insert_active_platform_ura(
tests/integration/test_rbac_router.py:327:            await _insert_active_platform_ura(
tests/integration/test_rbac_router.py:347:        await _delete_uras_by_id(session_factory, platform_auth, ura_ids)
```

Three call sites of `_insert_active_platform_ura` (lines 315, 321, 327)
plus the definition (line 138). One call site of `_delete_uras_by_id`
(line 347) plus the definition (line 175).

---

End of report.
