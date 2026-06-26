# Step CI-4a: Auth0 Management API client (provisioning plumbing)

## Context

The standalone plumbing that lets CM create/invite Auth0 users (step 4a of the
CM<->Auth0 provisioning arc). 4b wires it into user-creation post-commit, 4c is the
invite-accept callback, 4d the Login Action: all SEPARATE later steps, NOT done here.

The FIRST outbound HTTP from CM (confirmed: no httpx usage in src/ before this).
`httpx>=0.27` is already a dependency (no new dep); CM's stack is async, so
httpx.AsyncClient.

M2M credentials (provisioned): token endpoint `https://sevyn8.us.auth0.com/oauth/token`;
Management audience `https://sevyn8.us.auth0.com/api/v2/`; client id
`bn1GD4qRoNohOaJans7xZ9H1u9m2Zz9N`; secret from env (never committed); scopes
create:users, read:users, update:users, create:users_app_metadata, create:user_tickets.

## Standing discipline

- Pre-flight: `./scripts/check_setup.sh` (note unrelated FAILs, proceed; no DB needed).
- Read docs/architecture.md + docs/architecture_RBAC.md (auth/JWT), the D-07 CI-3
  update, and the auth0_sub / INVITED-ACTIVE invite-accept context.
- Commit: `Step CI-4a: Auth0 Management API client (provisioning plumbing)`.
- Bundle: work + this prompt + BUILD_PLAN note + the architecture.md doc note.
- No Co-Authored-By. No em-dashes. Branch off main; local commit only; no push/PR.

## Decisions (approved)

1. `SecretStr` for `auth0_m2m_client_secret` (the rest plain str). Read once via
   `.get_secret_value()` at the token request; never logged.
2. `auth0_db_connection` configurable, default `Username-Password-Authentication`.
3. New `Auth0ManagementError(ServerError)` (upstream-dependency failure; the wire
   stays generic INTERNAL_ERROR; context carries operation/status/cause-type only).
4. Test seam: constructor-injected `httpx.AsyncClient` over `httpx.MockTransport`,
   plus an injected `clock` for deterministic token-cache expiry.
5. Update `docs/architecture.md`'s outbound-network line to include the Auth0
   Management API (it previously said outbound was only Cloud SQL + Secret Manager).

## Holds

A. The secret and the bearer token NEVER enter error context, logs, or repr.
   Auth0ManagementError context = operation / status / cause-type only. No bare
   httpx error escapes `_request`.
B. Defensive token-response shape: a missing/non-numeric `expires_in` is fail-safe
   (use the token for this call, re-fetch next call); never cache-forever, never
   crash. A missing `access_token` is a typed error.

## Client (src/admin_backend/auth/auth0_management.py)

Auth0ManagementClient: `_get_token` (client-credentials grant, cached until the
refresh margin before expiry, REQUIRED for the 1000-tokens/month quota),
`create_user(email, app_metadata)`, `create_invitation_ticket(auth0_user_id)`,
`get_user(auth0_user_id)`, `find_user_by_email(email)`. A single `_request` egress
chokepoint maps every httpx failure and every non-2xx to Auth0ManagementError.
Explicit httpx timeout (10s). Domain-agnostic: the caller passes app_metadata.

## Config (additive, all optional/None; STUB mode + existing tests need nothing)

`auth0_m2m_client_id`, `auth0_m2m_client_secret` (SecretStr), `auth0_mgmt_audience`
(derive `issuer + api/v2/`), `auth0_token_endpoint` (derive `issuer + oauth/token`),
`auth0_db_connection` (default `Username-Password-Authentication`). Documented in
.env.example with placeholders only (never the real secret).

## Tests (tests/unit, DB-free, no network, no secret)

MockTransport handler dispatching by path + an injected fake clock. Token caching
proof (fetch-once, cache-hit-no-refetch, post-expiry-refetch), malformed-expires_in
fail-safe, missing-access_token + missing-config typed errors, create_user body +
parse, create_invitation_ticket URL, get_user, find_user_by_email (match + none),
and fail-safe mapping of 4xx / 5xx / network error to Auth0ManagementError.

## Out of scope (NOT touched)

Wiring into user-creation (4b), the user-write repos, the invite-accept callback
(4c), the Login Action (4d). This PR is the client + tests + config + docs only.
