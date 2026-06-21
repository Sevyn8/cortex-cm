#!/usr/bin/env bash
# Generate a 7-day-expiry JWT for a given user and save to a file.
# Usage:
#   ./scripts/jwt/generate_7d.sh anjali@ithina.ai      # platform user
#   ./scripts/jwt/generate_7d.sh marcus.t@bucees.com   # tenant user
#
# Outputs to scripts/jwt/tokens/<email-prefix>-7d.jwt
#
# Differs from generate.sh only in expiry (7 days vs 1 hour) and output
# filename suffix. Use for frontend dev integration where token refresh
# would otherwise be a daily annoyance, until Auth0 lands.

# NOTE: This script reads tenant_user records from the LOCAL Postgres
# instance and stamps the LOCAL tenant_id into the minted JWT. This is
# correct for local-DB testing but produces JWTs that won't see any
# rows in cloud DBs (cloud has different tenant UUIDs since UUIDv7
# substitution at load time generates fresh IDs).
#
# For cloud cross-tenant testing, mint JWTs inline with hardcoded
# cloud-side UUIDs — see prompts/step-4_4-cloud-run-deploy-dev.md
# section 5 for the pattern.
#
# PLATFORM JWTs (no tenant_id claim) are unaffected — same script
# output works against local and cloud.

set -e

if [ -z "$1" ]; then
  echo "Usage: $0 <email>"
  exit 1
fi

EMAIL="$1"
cd "$(dirname "$0")/../.."
mkdir -p scripts/jwt/tokens

# Look up user details (id, user_type, tenant_id) by email
USER_INFO=$(uv run python -c "
import asyncio, json
from admin_backend.config import get_settings
from admin_backend.db.engine import create_engine
from sqlalchemy import text

async def main():
    engine = create_engine(get_settings())
    async with engine.connect() as conn:
        await conn.execute(text(\"SELECT set_config('app.user_type', 'PLATFORM', false)\"))
        # Try platform_users first
        r = await conn.execute(text(\"SELECT id FROM platform_users WHERE email = :e\"), {'e': '$EMAIL'})
        row = r.fetchone()
        if row:
            print(json.dumps({'id': str(row.id), 'user_type': 'PLATFORM', 'tenant_id': None}))
            await engine.dispose()
            return
        # Fall through to tenant_users
        r = await conn.execute(text(\"SELECT id, tenant_id FROM tenant_users WHERE email = :e\"), {'e': '$EMAIL'})
        row = r.fetchone()
        if row:
            print(json.dumps({'id': str(row.id), 'user_type': 'TENANT', 'tenant_id': str(row.tenant_id)}))
            await engine.dispose()
            return
        print(json.dumps({'error': 'user not found: $EMAIL'}))
    await engine.dispose()

asyncio.run(main())
")

# Bail if user not found
if echo "$USER_INFO" | grep -q '"error"'; then
  echo "$USER_INFO" >&2
  exit 1
fi

# Mint the JWT — 7 days = 604800 seconds
JWT=$(uv run python -c "
import json
from uuid import UUID
from admin_backend.config import get_settings
from admin_backend.auth.testing import make_test_jwt

info = json.loads('$USER_INFO')
kwargs = {
    'user_type': info['user_type'],
    'user_id': UUID(info['id']),
    'exp_offset_seconds': 604800,
}
if info['tenant_id']:
    kwargs['tenant_id'] = UUID(info['tenant_id'])

print(make_test_jwt(get_settings(), **kwargs))
")

# Save to file with -7d suffix to coexist with 1-hour tokens from generate.sh
EMAIL_PREFIX=$(echo "$EMAIL" | sed 's/@.*//' | sed 's/[^a-zA-Z0-9_-]/-/g')
OUTFILE="scripts/jwt/tokens/${EMAIL_PREFIX}-7d.jwt"
echo "$JWT" > "$OUTFILE"

echo "Generated: $OUTFILE"
echo "User type: $(echo "$USER_INFO" | python3 -c 'import sys, json; print(json.load(sys.stdin)["user_type"])')"
echo "Expires in: 7 days"
