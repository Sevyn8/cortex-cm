# Step CI-4b: provision Auth0 user on tenant-user creation (post-commit background task)

## Context

Wire Auth0 provisioning into tenant-user creation as a POST-COMMIT background task.
When CM creates a tenant_user (INVITED), AFTER the DB row commits, a background task
uses the Step 4a Auth0ManagementClient to create the Auth0 user (with app_metadata)
and issue an invitation ticket. 4c (invite-accept callback, records auth0_sub +
INVITED->ACTIVE) and 4d (Login Action) are SEPARATE later steps, NOT done here.

Verified ordering: `get_tenant_session` commits in dependency teardown; FastAPI runs
BackgroundTasks after the response and after teardown, so the task runs post-commit
(the row is durably committed before the task fires).

## Standing discipline

- Pre-flight: `./scripts/check_setup.sh` (note unrelated FAILs, proceed).
- Read docs/architecture.md + docs/architecture_RBAC.md; re-read
  repositories/tenant_users.py create(), db/session.py get_tenant_session,
  routers/v1/tenant_users.py create_tenant_user.
- Commit: `Step CI-4b: provision Auth0 user on tenant-user creation (post-commit background task)`.
- Bundle: work + this prompt + BUILD_PLAN note + doc note.
- No Co-Authored-By. No em-dashes. Branch off main; local commit only; no push/PR.

## Decisions (approved)

A. Construct Auth0ManagementClient on `app.state.auth0_management_client` only when
   the M2M config (client_id + secret) is present; None otherwise. The background
   task no-ops when None, so STUB/dev and existing tests need no Auth0 config.
   Optional shutdown `aclose()` on the owned client.
B. Provisioning-outcome audit: structured log only for 4b. A DB audit event (fresh
   session) is deferred; the create itself is already audited in-transaction.
C. Test the task function directly with a fake client: no network, no DB.

## Post-commit safety (verified during build)

`create()` returns `get_by_id(...)`, whose `.user` is a session-bound `TenantUser`
ORM instance; `id/tenant_id/email` are mapped column attributes. The session factory
uses `expire_on_commit=False`, so those attributes would not reload post-commit, but
to be unconditionally safe (independent of that setting and of holding a session-bound
object) the handler extracts them into local primitives (UUID/UUID/str) WHILE the
session is open and passes those locals to `add_task`. The task holds only detached
primitives; nothing it reads can touch the closed request session.

## Wiring

1. Lifespan (main.py): construct the client per decision A; `app.state.auth0_management_client`.
2. Handler (create_tenant_user): additive `background_tasks: BackgroundTasks` param;
   after the existing create + 404 guard and BEFORE the unchanged `return`, extract
   detached primitives and schedule `provision_auth0_user`. The create transaction,
   in-repo audit, and 201 response shape are unchanged.
3. Task (auth/provisioning.py): `provision_auth0_user(client, *, user_id, tenant_id,
   email, user_type, request_id)`. None client -> log-and-skip. Else create_user
   (app_metadata `{tenant_id, user_type:"TENANT", user_id}` as strings) then
   create_invitation_ticket. On Auth0ManagementError: log (operation/user_id/
   tenant_id/request_id, NO secret) and return. Never raises, never rolls back, never
   writes auth0_sub.

## Tests (tests/unit, DB-free, no network)

Direct calls to `provision_auth0_user` with a fake client: success (create_user with
the right app_metadata then create_invitation_ticket), create_user failure (swallowed,
ticket not reached), ticket failure (swallowed), unconfigured (None client no-op).

## Out of scope (NOT touched)

The create transaction / in-repo audit / 201 response; auth0_sub (4c); the invite-accept
callback (4c); the Login Action (4d); platform-user provisioning (no endpoint; future).
