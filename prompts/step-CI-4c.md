# Step CI-4c: invite-accept endpoint (INVITED to ACTIVE, record auth0_sub)

## Context

The self-service flow that flips a tenant_user INVITED -> ACTIVE and records
`auth0_sub` on first login (step 4c of the CM<->Auth0 provisioning arc). Design
decided: self-service via the user's OWN verified Auth0 token. The SPA calls this
on first login; CM takes `auth0_sub` from the VERIFIED token sub (never the body),
finds the caller's own record via the verified `user_id`/`tenant_id`, and atomically
activates it.

Ground truth: `ck_tenant_users_auth0_sub_consistency` requires INVITED => auth0_sub
NULL, ACTIVE/SUSPENDED => auth0_sub NOT NULL, so the status flip + auth0_sub write
MUST be one atomic UPDATE. `transition()` hardcodes SUSPENDED<->ACTIVE and never
writes auth0_sub, so 4c needs a NEW repo method. The `/me/*` endpoints have no admin
gate and the middleware does not gate on DB status, so an INVITED user with a valid
token reaches this endpoint (the window 4c operates in).

## Standing discipline

- Pre-flight: `./scripts/check_setup.sh` (note unrelated FAILs, proceed).
- Commit: `Step CI-4c: invite-accept endpoint (INVITED to ACTIVE, record auth0_sub)`.
- Bundle: work + this prompt + BUILD_PLAN note + doc note.
- No Co-Authored-By. No em-dashes. Branch off main; local commit only; no push/PR.

## Decisions (approved)

A. Audit action `ACCEPT_INVITATION` (additive `_ACTION_LABELS` entry), distinct from
   the admin `ACTIVATE` so the log separates self-accept from admin reactivation.
B. Guarded-UPDATE-then-classify: the happy path is one atomic UPDATE
   `WHERE status='INVITED'`; on 0 rows, a SELECT classifies the outcome.
C. Self-activation audit actor = the user themselves (pattern (b) updated_by).
   Already-active: uniform 200 + `activated` flag (true on the flip, false on repeat).
   PLATFORM caller: explicit 403 guard (tenant-user path only).

## Holds honored

1. The rowcount-0 classify gives a still-INVITED read its OWN outcome (`CONFLICT`,
   a concurrent-accept race, 409 retryable), never misclassified as ALREADY_ACTIVE or
   NOT_INVITED. SUSPENDED -> NOT_INVITED -> 409 (intended).
2. The PLATFORM-guard unit test passes a tripwire session whose every attribute access
   raises, proving the guard runs before any DB access (genuinely DB-free, ci/unit fit).
- Four critical properties: `auth0_sub` only from `auth.sub`; the flip + auth0_sub
  write are ONE atomic UPDATE; the endpoint acts only on `auth.user_id` (self-only,
  no target input); `transition()` untouched.

## Implementation

- `repositories/tenant_users.py`: `AcceptInvitationResult` (ACTIVATED / ALREADY_ACTIVE
  / NOT_INVITED / CONFLICT / NOT_FOUND) + `accept_invitation(session, *, user_id,
  tenant_id, auth0_sub, auth=None, request_id=None)`: the atomic guarded UPDATE, the
  0-row classify, and the same-transaction ACCEPT_INVITATION audit on the activated case.
- `audit/emit.py`: `_ACTION_LABELS["ACCEPT_INVITATION"] = "Accepted invitation"`.
- `routers/v1/me.py`: `POST /me/accept-invitation` (no admin gate; PLATFORM-guard first;
  reads auth.sub / auth.user_id / auth.tenant_id; maps outcomes to 200 / 404 / 409).
- `schemas/me.py`: `AcceptInvitationResponse {user_id, tenant_id, status, activated}`.

## Tests

- Unit (ci/unit): the PLATFORM-guard tripwire test (DB-free).
- Integration (DB tier): repo activate (+ columns + constraint), idempotent
  already-active, not-found, same-transaction audit row; endpoint happy + idempotent,
  platform-403, not-found.
