# Step CI-3: implement Auth0Client (production JWKS verifier)

## Context

main.py's lifespan raised NotImplementedError for `AUTH_CLIENT_MODE != "STUB"`.
This step implements `Auth0Client`, the production verifier that validates real
Auth0 RS256 tokens against the Auth0 JWKS endpoint. The roles model is decided
(D-24): identity-only token, NO roles claim; permissions resolve from the DB. So
Auth0Client extracts the SAME identity claims as StubAuthClient and no roles.

Provisioned Auth0 tenant (the real values): issuer `https://sevyn8.us.auth0.com/`;
audience `https://api.cortex.sevyn8.com`; JWKS
`https://sevyn8.us.auth0.com/.well-known/jwks.json`; claim namespace
`https://sevyn8.com` (already in place from Step CI-2).

## Standing discipline

- Pre-flight: `./scripts/check_setup.sh` (note unrelated FAILs, proceed; no DB needed).
- Read docs/architecture.md and docs/architecture_RBAC.md (auth/JWT sections).
- Commit: `Step CI-3: implement Auth0Client (production JWKS verifier)`.
- Bundle: source + tests + stale-fixture edits + this prompt + a BUILD_PLAN.md note
  + a D-07/D-24 doc note.
- No Co-Authored-By. No em-dashes anywhere. Branch off main; local commit only;
  no push, no PR.

## Decisions (approved)

1. Shared `AuthClient` Protocol (auth/base.py); both clients satisfy it
   structurally; middleware + main annotate against it.
2. `auth0_jwks_url: str | None = None` setting (env `AUTH0_JWKS_URL`); when unset,
   Auth0Client derives the URL from `jwt_issuer` (Auth0 convention). Documented in
   .env.example.
3. PyJWKClient (from pyjwt[crypto], already a dependency) caches keys by kid and
   fetches on cache-miss. NO new dependency.
4. Extract the shared `claims_to_auth_context` helper into auth/claims.py (owns
   NAMESPACE + CLAIM_*); stub.py re-exports the constants (explicit `as` form) so
   the existing import sites are unchanged and mypy --strict is satisfied.
5. Stale-fixture cleanup folded in (the real values come alive here).

## Security invariant: FAIL CLOSED

Every key-acquisition or verification failure (kid not found, JWKS unreachable,
any PyJWKClientError, any jwt exception, any doubt) raises a typed error and
REJECTS. No code path returns an AuthContext on failure. Proven by the
JWKS-unreachable and kid-not-found tests, which assert rejection.

## Behavior-preserving extraction

The claim-extraction logic moved verbatim from stub.py into
`claims_to_auth_context`. The proof that the extraction changed nothing is the
stub's existing 21 tests (tests/unit/test_stub_auth.py) staying green after the
rewire. The CLAIM_* re-export keeps the 4 existing import sites working with zero
edits.

## Tests (DB-free, no network, no secrets)

Seam: an in-test RSA keypair signs tokens; a fake jwk_client (injected into
Auth0Client) returns the test public key, or raises PyJWKClientError for the
fail-closed cases. Explicit test Settings (env-independent). Groups mirror
test_stub_auth: happy (TENANT / PLATFORM / impersonation), signature attacks
(tampered / garbage / foreign key), claim validation (expired / wrong aud / wrong
iss / missing user_id / invalid user_type / non-UUID tenant_id), FAIL CLOSED (kid
not found, JWKS unreachable), missing JWT (empty, None). They fit tests/unit and
the ci/unit gate.

## Stale-fixture cleanup

- tests/integration/test_lifespan.py: `ithina.us.auth0.com` -> `sevyn8.us.auth0.com`.
- tests/integration/test_stores_repo_writes.py: `api.ithina.com` ->
  `api.cortex.sevyn8.com` (the synthetic AuthContext `aud`).
- tests/integration/test_seed_loader.py: the prod-valid issuer placeholder
  `auth.ithina.com` -> `sevyn8.us.auth0.com` (confirmed not asserted on the literal).
- .github/workflows/ci.yml: `JWT_AUDIENCE` -> `https://api.cortex.sevyn8.com`.
- .env.example: `JWT_AUDIENCE` and the commented Auth0 example (`AUTH0_DOMAIN`,
  `AUTH0_JWKS_URL`) -> the real sevyn8 values.

Out of scope (deferred): the `iss` stub issuer in synthetic contexts, `@ithina.*`
fixture emails, the generic `prod.auth0.com` validator-test issuer, GCP/DB/hostnames.
Do NOT touch the production_must_use_auth0 / issuer validators. Do NOT wire DIS or
write the Auth0 Login Action.
