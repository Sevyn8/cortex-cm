"""Step 2.3 integration tests for middleware + dependencies + error handler.

10 tests:
    T1: public path (/api/v1/health) reachable without Authorization;
        X-Request-Id header still set.
    T2: protected path with no Authorization -> 401 AUTH_MISSING.
    T3: protected path with empty Bearer token -> 401 AUTH_MISSING.
    T4: protected path with malformed JWT -> 401 AUTH_INVALID.
    T5: protected path with valid TENANT JWT -> 200; body shows tenant.
    T6: every response carries an X-Request-Id (UUID format) header.
    T7: per-request JSON log line emitted with all expected fields.
    T8: same request_id on the response header AND in the log line.
    T9: full-stack injection attempt: header X-User-Type=PLATFORM is
        ignored; the DB session sees TENANT (AI-MT-03 source-binding).
    T10: ServerError subclass returns generic INTERNAL_ERROR to the
         client; subclass-specific info is in the log only.

Shared fixtures (settings, app_with_test_routes, client,
valid_tenant_jwt, json_log_buffer, error_log_buffer) live in
tests/integration/conftest.py since Step 2.4.
"""
import json
import re

from fastapi.testclient import TestClient


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_t1_public_path_no_auth(client: TestClient) -> None:
    """/api/v1/health is reachable without Authorization; X-Request-Id set."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert "x-request-id" in {k.lower() for k in resp.headers.keys()}
    assert _UUID_RE.match(resp.headers["x-request-id"])


def test_t2_protected_no_auth_returns_401(client: TestClient) -> None:
    resp = client.get("/v1/_test_protected")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "AUTH_MISSING"
    assert body["request_id"] is not None
    assert _UUID_RE.match(body["request_id"])
    assert "x-request-id" in {k.lower() for k in resp.headers.keys()}


def test_t3_protected_empty_bearer_returns_401(client: TestClient) -> None:
    resp = client.get(
        "/v1/_test_protected", headers={"Authorization": "Bearer "}
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "AUTH_MISSING"


def test_t4_protected_malformed_jwt_returns_401(client: TestClient) -> None:
    resp = client.get(
        "/v1/_test_protected",
        headers={"Authorization": "Bearer invalid.token.string"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "AUTH_INVALID"


def test_t5_protected_valid_tenant_jwt(
    client: TestClient,
    valid_tenant_jwt: tuple[str, str],
) -> None:
    token, tenant_id = valid_tenant_jwt
    resp = client.get(
        "/v1/_test_protected",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == tenant_id
    assert body["user_type"] == "TENANT"


def test_t6_x_request_id_on_every_response(
    client: TestClient,
    valid_tenant_jwt: tuple[str, str],
) -> None:
    """Both success and failure responses carry X-Request-Id."""
    token, _ = valid_tenant_jwt

    success = client.get(
        "/v1/_test_protected",
        headers={"Authorization": f"Bearer {token}"},
    )
    failure = client.get("/v1/_test_protected")  # 401

    for resp in (success, failure):
        rid = resp.headers.get("x-request-id")
        assert rid is not None, "X-Request-Id missing"
        assert _UUID_RE.match(rid), f"X-Request-Id not UUID: {rid!r}"


def test_t7_log_line_shape(
    client: TestClient,
    json_log_buffer,
    valid_tenant_jwt: tuple[str, str],
) -> None:
    """Per-request log line has all expected fields."""
    token, _ = valid_tenant_jwt
    resp = client.get(
        "/v1/_test_protected",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    raw = json_log_buffer.getvalue().strip()
    assert raw, "no log line emitted"
    last = json.loads(raw.split("\n")[-1])

    expected_keys = {
        "request_id",
        "method",
        "path",
        "status",
        "latency_ms",
        "ip",
        "user_agent",
        "tenant_id",
        "user_id",
        "user_type",
        "exception",
    }
    assert expected_keys.issubset(last.keys()), (
        f"missing keys: {expected_keys - last.keys()}"
    )
    assert last["status"] == 200
    assert last["method"] == "GET"
    assert last["path"] == "/v1/_test_protected"
    assert last["exception"] is None


def test_t8_request_id_consistent_header_and_log(
    client: TestClient,
    json_log_buffer,
    valid_tenant_jwt: tuple[str, str],
) -> None:
    """Same request_id on X-Request-Id header AND log line."""
    token, _ = valid_tenant_jwt
    resp = client.get(
        "/v1/_test_protected",
        headers={"Authorization": f"Bearer {token}"},
    )
    header_rid = resp.headers["x-request-id"]
    raw = json_log_buffer.getvalue().strip()
    last = json.loads(raw.split("\n")[-1])
    assert last["request_id"] == header_rid


def test_t9_user_type_header_is_ignored(
    client: TestClient,
    valid_tenant_jwt: tuple[str, str],
) -> None:
    """Full-stack injection: X-User-Type header cannot override JWT.

    The DB session must see TENANT (from the JWT's AuthContext) even
    though the request sets X-User-Type: PLATFORM. Verifies AI-MT-03.
    """
    token, _ = valid_tenant_jwt
    resp = client.get(
        "/v1/_test_db_user_type",
        headers={
            "Authorization": f"Bearer {token}",
            "X-User-Type": "PLATFORM",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"db_user_type": "TENANT"}


def test_t10_server_error_returns_generic_response(
    client: TestClient,
    error_log_buffer,
    valid_tenant_jwt: tuple[str, str],
) -> None:
    """ServerError subclass: generic 500 to client, specifics to log."""
    token, _ = valid_tenant_jwt
    resp = client.get(
        "/v1/_test_server_error",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["message"] == "An internal error occurred"
    # Anti-information-disclosure: the internal_message must not leak.
    assert "database is on fire" not in json.dumps(body)
    assert "_SecretLeakError" not in json.dumps(body)

    # The log captures the subclass identity and the internal_message.
    log_raw = error_log_buffer.getvalue().strip()
    assert log_raw, "no error log emitted"
    log_entry = json.loads(log_raw.split("\n")[-1])
    assert log_entry["exception_type"] == "_SecretLeakError"
    assert "database is on fire" in log_entry["internal_message"]
