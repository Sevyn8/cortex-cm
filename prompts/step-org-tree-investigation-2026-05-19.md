# Investigation : org-tree response shape + tenant-root immutability

**Date drafted:** 2026-05-19
**Investigator:** Claude Code
**Output:** single chat reply, structured per bucket
**No source edits.** Read-only investigation.

This investigation answers structural and behavioural questions about the existing `/api/v1/tenants/{tenant_id}/org-tree` surface before scoping a fix step. Operator concern: the frontend renders org-tree without the tenant root visually anchoring the tree (single-store tenants look "floating"); also wants tenant-root rename behavior locked down.

## Standing discipline

- **A8 cite-or-verify**: every finding cites file:line or query output.
- **No code changes.**
- **Surface contradictions** with operator's working assumptions.
- One comprehensive sweep, one reply.

## Working assumptions to verify

1. `OrgTreeResponse` carries `tenant_id`, `tenant_name`, `stats`, `tree[]` (per openapi.json).
2. The tenant root org_node (node_type='TENANT') is excluded from `tree[]`; only its children appear.
3. The tenant root org_node is created automatically when a tenant is provisioned (per Step 6.20.1 retro).
4. `PATCH /api/v1/tenants/{tenant_id}/org-tree/{node_id}` allows renaming the tenant root (`name` change) but rejects reparenting it (TENANT_ROOT_NOT_REPARENTABLE).
5. `tenants.name` and the tenant-root `org_nodes.name` are stored separately; renaming one does NOT automatically rename the other.

Verify each as a finding. Surface contradictions.

## Investigation buckets

### Bucket 1 : Current response shape

**1a. Read the OrgTreeResponse schema and the handler that returns it.**

```
grep -n "OrgTreeResponse\|tree:\|tenant_name" src/admin_backend/schemas/org_tree.py
grep -n "get_org_tree\|OrgTreeResponse" src/admin_backend/routers/v1/org_tree.py
```

Report:
- Exact schema fields and types
- How `tenant_name` is sourced (separate query? JOIN? from tenants table?)
- Whether the tenant-root org_node is in any form excluded from `tree[]` or carried elsewhere

**1b. Read the repo method that builds `tree[]`.**

```
grep -n "list_active_with_child_counts\|list_grouped\|tree" src/admin_backend/repositories/org_nodes.py
```

Report:
- The SQL filter that excludes (or doesn't) the TENANT-type node from `tree[]`
- Whether the root node's id/name/type is fetched separately for any purpose

**1c. Run live query: what's in `tree[]` for a single-store tenant?**

Find a tenant with a small tree (1-2 stores):
```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "
SELECT t.id, t.name, COUNT(o.id) AS node_count
FROM core.tenants t
LEFT JOIN core.org_nodes o ON o.tenant_id = t.id AND o.node_type != 'TENANT' AND o.status = 'ACTIVE'
GROUP BY t.id, t.name
HAVING COUNT(o.id) <= 2
ORDER BY node_count
LIMIT 5;"
```

Report the tenants. Pick one with 1 store; run a curl against the local server's GET /org-tree for it (or paste the equivalent SQL that the repo would run) and report the actual `tree[]` content.

### Bucket 2 : Tenant root creation + storage

**2a. Tenant-root org_node DDL.**

```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "
SELECT t.id, t.name AS tenant_name, o.id AS root_node_id, o.code AS root_code, o.name AS root_name, o.node_type, o.parent_id, o.path
FROM core.tenants t
JOIN core.org_nodes o ON o.tenant_id = t.id AND o.node_type = 'TENANT'
LIMIT 10;"
```

Report:
- Does every tenant have exactly one node_type='TENANT' org_node?
- Are `tenant.name` and the root `org_node.name` identical at creation, or different?
- What's the `code` convention for the tenant root?
- Is `parent_id` NULL for the tenant root?

**2b. Tenant provisioning logic.**

```
grep -n "create_with_root\|root org_node\|TENANT.*node_type" src/admin_backend/repositories/tenants.py
```

Find the code that creates the tenant root org_node (Step 6.20.1 per session memory). Report:
- The exact INSERT for the root node
- How `name` and `code` are derived (from tenant.name? slug? hardcoded?)
- Whether tenant.name updates cascade to root org_node.name (likely not; surface either way)

**2c. Tenant rename behavior.**

```
grep -n "PATCH\|patch_tenant\|update.*name" src/admin_backend/routers/v1/tenants.py src/admin_backend/repositories/tenants.py
```

Look at the PATCH /api/v1/tenants/{id} handler + repo. Report:
- Whether renaming a tenant updates only `tenants.name` or also propagates to the root `org_nodes.name`
- If they're decoupled today: surface as the current behavior; not necessarily wrong, just informational

### Bucket 3 : Tenant root edit/protect behavior

**3a. PATCH org_tree node logic for tenant root.**

```
grep -n "TENANT_ROOT\|TenantRootNotReparentable\|node_type.*TENANT" src/admin_backend/repositories/org_nodes.py src/admin_backend/routers/v1/org_tree.py
```

Find the existing protections. Report:
- The exact check (parent_id change vs name change vs code change)
- Whether tenant root CAN be renamed today via PATCH
- Whether tenant root CAN have its code changed today via PATCH
- The error class used (TenantRootNotReparentableError per project knowledge)

**3b. PATCH org_tree request schema.**

```
grep -n "OrgNodeUpdateRequest\|class.*Update" src/admin_backend/schemas/org_tree.py
```

Report:
- Fields editable on PATCH (name, parent_id, code, ...)
- Whether `extra="forbid"` is set
- Whether there's any field-by-field tenant-root-specific guard at schema level

**3c. Run live PATCH probe (dry-run via SQL UPDATE rollback or repo direct).**

Skip if unable to execute safely. If feasible, document what a PATCH on tenant root with `{name: "x"}` would do today: succeed, fail with what error.

Alternatively: review the test suite for any test asserting tenant-root rename behavior.

```
grep -rn "tenant.*root\|TENANT.*root\|root_node" tests/integration/ | grep -i "rename\|name\|patch" | head -20
```

### Bucket 4 : Frontend rendering inference

**4a. Read the current openapi.json for the OrgTreeResponse description.**

Report any frontend-facing language that suggests the tree includes vs excludes the root.

**4b. Check architecture_RBAC.md or architecture.md for any frontend-rendering convention.**

```
grep -n "org.tree\|tree.render\|organization.tree" docs/architecture.md docs/architecture_RBAC.md
```

Report any documented expectation for how the frontend renders the tree (does the doc assume the frontend shows the tenant name as a virtual root, or that the response embeds the root explicitly?).

### Bucket 5 : Anything adjacent

Codebase observations beyond bucket scope:
- Stale references in CLAUDE.md to org-tree shape
- Existing FN-AB entries adjacent to org-tree
- Tests that may break if response shape changes
- Any frontend coordination notes in docs

## Output format

```
# Investigation report : org-tree response shape + tenant-root immutability

## Working assumptions verification
- Assumption 1 (response shape): confirmed / contradicted (evidence)
- Assumption 2 (tenant root excluded from tree[]): confirmed / contradicted
- Assumption 3 (tenant root auto-created): confirmed / contradicted
- Assumption 4 (root rename allowed, reparent rejected): confirmed / contradicted
- Assumption 5 (tenant.name and root.name decoupled): confirmed / contradicted

## Bucket 1 : current response shape
- 1a: OrgTreeResponse + handler
- 1b: tree[] repo build
- 1c: live tree[] for a small tenant

## Bucket 2 : tenant root creation + storage
- 2a: DDL state for all tenants
- 2b: provisioning code
- 2c: tenant rename behavior

## Bucket 3 : tenant root edit/protect
- 3a: existing protections
- 3b: PATCH schema
- 3c: live behavior or test coverage

## Bucket 4 : frontend rendering inference
- 4a: openapi description
- 4b: architecture docs

## Bucket 5 : codebase observations beyond scope
- FYI items

## Summary
- N findings confirm assumptions
- M findings contradict assumptions (each with evidence)
- K open design questions for the fix scoping conversation
```

## Don't

- Don't write or modify any source file.
- Don't propose code changes; surface them as findings if they emerge.
- Don't run multi-stage investigation; one comprehensive sweep, one reply.
- Don't fabricate findings if a query fails; report the failure and move on.
