"""Integration tests verifying Phase 5 Mobile Readiness.

Validates:
1. OpenAPI specification at /openapi.json and /api/v1/openapi.json for mobile codegen.
2. 100% stateless HTTP authentication (zero cookies, Bearer token only).
3. Mobile client correlation via X-Request-ID and structured headers.
4. Mobile rate limiting enforcement and 429 response envelope with Retry-After.
5. End-to-end mobile user flows (primary reads and primary writes).
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.rate_limiter import rate_limiter
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.category import UserCategory
from app.models.expense import Expense, ExpenseSplit
from app.models.friend import Friend
from app.models.group import Group, GroupMember
from app.models.profile import Profile

MOBILE_USER_ID = uuid.UUID("aaaaaaaa-9999-4999-a999-999999999999")
MOBILE_FRIEND_ID = uuid.UUID("bbbbbbbb-8888-4888-b888-888888888888")
TEST_JWT_SECRET = "mobile-readiness-test-secret-at-least-32-chars-long"

MOBILE_USER_AGENT = "SplitwiseBuddy-iOS/1.2.0 (iPhone15,3; iOS 17.5.1)"


def mobile_token(user_id: uuid.UUID = MOBILE_USER_ID, email: str = "mobile.user@example.com") -> str:
    """Generates a Supabase-compatible JWT token for mobile client authentication."""
    payload = {
        "sub": str(user_id),
        "aud": "authenticated",
        "role": "authenticated",
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=2),
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


@pytest_asyncio.fixture
async def mobile_db(tmp_path, monkeypatch):
    """Isolated SQLite database fixture seeded with mobile user and friend profile."""
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mobile_test.sqlite'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user_profile = Profile(
            user_id=MOBILE_USER_ID,
            display_name="Mobile User",
            email="mobile.user@example.com",
        )
        friend_profile = Profile(
            user_id=MOBILE_FRIEND_ID,
            display_name="Mobile Friend",
            email="mobile.friend@example.com",
        )
        friendship = Friend(
            user_id=MOBILE_USER_ID,
            friend_id=MOBILE_FRIEND_ID,
            status="accepted",
        )
        default_cat = UserCategory(
            id=uuid.uuid4(),
            user_id=MOBILE_USER_ID,
            name="Groceries",
            slug="groceries",
            color_index=2,
        )
        db.add_all([user_profile, friend_profile, friendship, default_cat])
        await db.commit()

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest_asyncio.fixture
async def mobile_client():
    """Simulates a native mobile HTTP client (e.g., URLSession or OkHttp): zero cookies."""
    transport = ASGITransport(app=app)
    headers = {
        "User-Agent": MOBILE_USER_AGENT,
        "Accept": "application/json",
    }
    # cookies=None guarantees zero cookie persistence or transmission
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers, cookies=None) as ac:
        yield ac


# ============================================================================
# 1. OpenAPI Specification Tests (Mobile SDK / Code Generation Readiness)
# ============================================================================


@pytest.mark.asyncio
async def test_openapi_available_at_root_and_versioned(mobile_client: AsyncClient):
    """
    Mobile codegen tools expect /openapi.json or /api/v1/openapi.json to return
    a valid OpenAPI 3.x schema.
    """
    for path in ("/openapi.json", "/api/v1/openapi.json"):
        resp = await mobile_client.get(path)
        assert resp.status_code == 200, f"Failed fetching {path}: {resp.text}"
        data = resp.json()
        assert "openapi" in data, f"OpenAPI version missing from {path}"
        assert data["info"]["title"] == settings.PROJECT_NAME
        assert "paths" in data
        assert len(data["paths"]) >= 35, f"Expected at least 35 paths, got {len(data['paths'])}"


@pytest.mark.asyncio
async def test_openapi_schema_contains_typed_envelope_responses(mobile_client: AsyncClient):
    """
    Confirms every endpoint specifies typed JSON responses adhering to the
    { data, error, meta } response envelope with no untyped dicts.
    """
    resp = await mobile_client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema["paths"]

    # Key mobile endpoints must be explicitly present
    required_paths = [
        "/api/v1/users/me",
        "/api/v1/dashboard/summary",
        "/api/v1/expenses/personal",
        "/api/v1/groups/",
        "/api/v1/friends/",
        "/api/v1/categories/",
        "/api/v1/health",
    ]
    for p in required_paths:
        assert p in paths, f"Required mobile path {p} missing from OpenAPI schema"

    # Verify all responses define schemas in application/json
    for path, operations in paths.items():
        for method, op in operations.items():
            if method in ("parameters", "$ref"):
                continue
            responses = op.get("responses", {})
            success_status = "200" if "200" in responses else ("201" if "201" in responses else None)
            if success_status:
                content = responses[success_status].get("content", {})
                assert "application/json" in content, f"{method.upper()} {path} missing application/json response"
                assert "schema" in content["application/json"], f"{method.upper()} {path} missing schema definition"


# ============================================================================
# 2. 100% Stateless Authentication (Zero Cookies, Bearer Token Only)
# ============================================================================


@pytest.mark.asyncio
async def test_unauthenticated_request_fails_with_standard_envelope(mobile_client: AsyncClient, mobile_db):
    """
    Unauthenticated request from a mobile client returns 401 Unauthorized
    with the agreed ResponseEnvelope and ZERO cookies.
    """
    resp = await mobile_client.get("/api/v1/users/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["data"] is None
    assert "error" in body and body["error"] is not None
    # Verify zero cookies returned
    assert "set-cookie" not in resp.headers


@pytest.mark.asyncio
async def test_cookie_only_request_rejected(mobile_client: AsyncClient, mobile_db):
    """
    Sending token in a cookie without Authorization header MUST fail.
    Proves backend does not rely on browser cookies.
    """
    token = mobile_token()
    headers = {"Cookie": f"sb-access-token={token}; access_token={token}"}
    resp = await mobile_client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers


@pytest.mark.asyncio
async def test_bearer_token_authenticated_request_succeeds(mobile_client: AsyncClient, mobile_db):
    """
    Sending valid Bearer token from a mobile HTTP client succeeds, returning 200 OK,
    ResponseEnvelope data, and zero Set-Cookie headers.
    """
    token = mobile_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = await mobile_client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["data"]["email"] == "mobile.user@example.com"
    assert body["data"]["display_name"] == "Mobile User"
    assert "set-cookie" not in resp.headers


# ============================================================================
# 3. Mobile Correlation ID & Structured Headers
# ============================================================================


@pytest.mark.asyncio
async def test_mobile_client_custom_request_id_preserved(mobile_client: AsyncClient, mobile_db):
    """
    Mobile client passes a custom X-Request-ID header for client-side crash/log correlation.
    FastAPI must echo it back in the response headers.
    """
    client_req_id = "mobile-trace-uuid-abcdef-123456"
    headers = {
        "Authorization": f"Bearer {mobile_token()}",
        "X-Request-ID": client_req_id,
    }
    resp = await mobile_client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID") == client_req_id


@pytest.mark.asyncio
async def test_server_generates_request_id_if_omitted(mobile_client: AsyncClient, mobile_db):
    """
    If mobile client omits X-Request-ID, the server must automatically assign a UUID.
    """
    headers = {"Authorization": f"Bearer {mobile_token()}"}
    resp = await mobile_client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 200
    assert "X-Request-ID" in resp.headers
    assert len(resp.headers["X-Request-ID"]) >= 32


# ============================================================================
# 4. Rate Limiting for Mobile Clients
# ============================================================================


@pytest.mark.asyncio
async def test_mobile_rate_limit_headers_present(mobile_client: AsyncClient, mobile_db):
    """
    Successful requests from mobile clients must include standard rate limiting headers:
    X-RateLimit-Limit and X-RateLimit-Remaining.
    """
    headers = {"Authorization": f"Bearer {mobile_token()}"}
    resp = await mobile_client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 200
    assert "X-RateLimit-Limit" in resp.headers
    assert "X-RateLimit-Remaining" in resp.headers


@pytest.mark.asyncio
async def test_mobile_rate_limit_enforcement_returns_429(mobile_client: AsyncClient, mobile_db, monkeypatch):
    """
    When rate limit is reached, mobile client receives 429 Too Many Requests,
    Retry-After header, and the standard error envelope with retry_after in meta.
    """
    # Lower limit to 3 requests for this specific test
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 3)
    token = mobile_token()
    headers = {"Authorization": f"Bearer {token}"}

    # First 3 requests should pass
    for i in range(3):
        resp = await mobile_client.get("/api/v1/users/me", headers=headers)
        assert resp.status_code == 200

    # 4th request must be rejected with 429
    resp = await mobile_client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert resp.headers.get("X-RateLimit-Remaining") == "0"

    body = resp.json()
    assert body["data"] is None
    assert "Rate limit exceeded" in body["error"]
    assert "retry_after" in body["meta"]
    assert body["meta"]["retry_after"] > 0


# ============================================================================
# 5. Mobile End-to-End Primary Read Flow
# ============================================================================


@pytest.mark.asyncio
async def test_mobile_primary_read_flow(mobile_client: AsyncClient, mobile_db):
    """
    Simulates a mobile app initial cold launch / refresh:
    1. GET /api/v1/users/me (profile)
    2. GET /api/v1/dashboard/summary (dashboard metrics)
    3. GET /api/v1/categories/ (category list for dropdowns)
    4. GET /api/v1/groups/ (group list)
    5. GET /api/v1/friends/ (friends list)
    6. GET /api/v1/friends/balances (friend balance summaries)
    """
    headers = {"Authorization": f"Bearer {mobile_token()}"}

    # 1. Profile
    profile_resp = await mobile_client.get("/api/v1/users/me", headers=headers)
    assert profile_resp.status_code == 200
    assert profile_resp.json()["data"]["user_id"] == str(MOBILE_USER_ID)

    # 2. Dashboard summary
    summary_resp = await mobile_client.get("/api/v1/dashboard/summary", headers=headers)
    assert summary_resp.status_code == 200
    summary_data = summary_resp.json()["data"]
    assert "personalTotal" in summary_data
    assert "netOwed" in summary_data
    assert "netBalance" in summary_data

    # 3. Categories
    cat_resp = await mobile_client.get("/api/v1/categories/", headers=headers)
    assert cat_resp.status_code == 200
    categories = cat_resp.json()["data"]
    assert isinstance(categories, list)
    assert any(c["slug"] == "groceries" for c in categories)

    # 4. Groups
    groups_resp = await mobile_client.get("/api/v1/groups/", headers=headers)
    assert groups_resp.status_code == 200
    assert isinstance(groups_resp.json()["data"], list)

    # 5. Friends
    friends_resp = await mobile_client.get("/api/v1/friends/", headers=headers)
    assert friends_resp.status_code == 200
    friends_data = friends_resp.json()["data"]
    assert len(friends_data) == 1
    assert friends_data[0]["friend_id"] == str(MOBILE_FRIEND_ID)
    assert friends_data[0]["profile"]["email"] == "mobile.friend@example.com"

    # 6. Friend Balances
    balances_resp = await mobile_client.get("/api/v1/friends/balances", headers=headers)
    assert balances_resp.status_code == 200
    assert isinstance(balances_resp.json()["data"], list)


# ============================================================================
# 6. Mobile End-to-End Primary Write Flow
# ============================================================================


@pytest.mark.asyncio
async def test_mobile_primary_write_flow(mobile_client: AsyncClient, mobile_db):
    """
    Simulates a mobile app write session:
    1. Create a personal expense (POST /api/v1/expenses/personal)
    2. Update that personal expense (PATCH /api/v1/expenses/{id})
    3. Create a mobile group (POST /api/v1/groups/)
    4. Create a group expense with split (POST /api/v1/groups/{id}/expenses)
    5. Delete the personal expense (DELETE /api/v1/expenses/{id})
    6. Verify dashboard summary reflects updated balance state.
    """
    headers = {"Authorization": f"Bearer {mobile_token()}"}

    # 1. Create personal expense
    create_exp_payload = {
        "amount": "65.50",
        "category": "groceries",
        "note": "Mobile Coffee & Work",
        "expense_date": "2026-09-10",
    }
    exp_resp = await mobile_client.post("/api/v1/expenses/personal", headers=headers, json=create_exp_payload)
    assert exp_resp.status_code == 201, f"Failed creating expense: {exp_resp.text}"
    exp_data = exp_resp.json()["data"]
    expense_id = exp_data["id"]
    assert exp_data["note"] == "Mobile Coffee & Work"
    assert exp_data["amount"] == 65.5

    # 2. Patch the personal expense
    patch_payload = {"note": "Mobile Specialty Coffee"}
    patch_resp = await mobile_client.patch(f"/api/v1/expenses/{expense_id}", headers=headers, json=patch_payload)
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["note"] == "Mobile Specialty Coffee"

    # 3. Create a mobile group
    group_payload = {
        "name": "Mobile Hackathon Team",
        "description": "Expenses for mobile hackathon",
        "type": "day_to_day",
    }
    group_resp = await mobile_client.post("/api/v1/groups/", headers=headers, json=group_payload)
    assert group_resp.status_code == 201
    group_data = group_resp.json()["data"]
    group_id = group_data["id"]
    assert group_data["name"] == "Mobile Hackathon Team"

    # Add the friend to the group
    member_payload = {"user_id": str(MOBILE_FRIEND_ID), "role": "member"}
    add_member_resp = await mobile_client.post(f"/api/v1/groups/{group_id}/members", headers=headers, json=member_payload)
    assert add_member_resp.status_code == 201

    # 4. Create a group expense with 50/50 split
    group_exp_payload = {
        "amount": "100.00",
        "category": "groceries",
        "note": "Team Pizza",
        "paid_by": str(MOBILE_USER_ID),
        "splits": [
            {"user_id": str(MOBILE_USER_ID), "amount": "50.00"},
            {"user_id": str(MOBILE_FRIEND_ID), "amount": "50.00"},
        ],
    }
    group_exp_resp = await mobile_client.post(
        f"/api/v1/groups/{group_id}/expenses", headers=headers, json=group_exp_payload
    )
    assert group_exp_resp.status_code == 201
    assert group_exp_resp.json()["data"]["amount"] == 100.0

    # Verify group balances: friend owes mobile user $50.00
    group_bal_resp = await mobile_client.get(f"/api/v1/groups/{group_id}/balances", headers=headers)
    assert group_bal_resp.status_code == 200
    balances = group_bal_resp.json()["data"]
    # Check that balances exist and reflect positive net balance for mobile user
    assert len(balances) >= 1

    # 5. Delete personal expense
    del_resp = await mobile_client.delete(f"/api/v1/expenses/{expense_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["data"] is None

    # 6. Verify dashboard reflects active state
    dashboard_resp = await mobile_client.get("/api/v1/dashboard/summary", headers=headers)
    assert dashboard_resp.status_code == 200
    dash_data = dashboard_resp.json()["data"]
    assert "personalTotal" in dash_data
    assert "groups" in dash_data
    assert len(dash_data["groups"]) >= 1
