import uuid
from datetime import datetime, timezone
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.category import UserCategory
from app.models.profile import Profile


# Create in-memory SQLite engine for isolated integration testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ============================================================================
# Health Check Tests
# ============================================================================


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    """Test unauthenticated GET /api/v1/health returns standardized response envelope."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()

    # Guardrail #3: Enforce envelope { data, error, meta }
    assert "data" in data
    assert "error" in data
    assert "meta" in data
    assert data["error"] is None
    assert data["data"]["status"] == "healthy"
    assert data["data"]["version"] == "0.1.0"


# ============================================================================
# 1. GET /api/v1/users/me Tests
# ============================================================================


@pytest.mark.asyncio
async def test_users_me_unauthenticated(client: AsyncClient):
    """Test GET /api/v1/users/me rejects unauthenticated requests with 401."""
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401
    data = response.json()

    assert data["data"] is None
    assert "error" in data
    assert "Missing Authorization header" in data["error"]


@pytest.mark.asyncio
async def test_users_me_invalid_token(client: AsyncClient):
    """Test GET /api/v1/users/me rejects invalid token with 401."""
    headers = {"Authorization": "Bearer malformed.invalid.token"}
    response = await client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 401
    data = response.json()

    assert data["data"] is None
    assert "error" in data


@pytest.mark.asyncio
async def test_users_me_authenticated_success(client: AsyncClient, test_session: AsyncSession):
    """Test GET /api/v1/users/me resolves authenticated user to profile row."""
    test_user_id = uuid.uuid4()
    test_profile = Profile(
        id=uuid.uuid4(),
        user_id=test_user_id,
        display_name="Alice User",
        email="alice@example.com",
        avatar_url="https://example.com/alice.png",
        is_shadow=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    test_session.add(test_profile)
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(
            id=test_user_id,
            email="alice@example.com",
            role="authenticated",
        )

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        response = await client.get("/api/v1/users/me", headers=headers)
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is None
        assert data["data"]["user_id"] == str(test_user_id)
        assert data["data"]["display_name"] == "Alice User"
        assert data["data"]["email"] == "alice@example.com"
        assert data["data"]["is_shadow"] is False
        assert data["meta"]["auth_email"] == "alice@example.com"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_users_me_not_found(client: AsyncClient, test_session: AsyncSession):
    """Test GET /api/v1/users/me returns 404 when user has no profile row."""
    non_existent_user_id = uuid.uuid4()

    async def override_get_current_user():
        return AuthenticatedUser(
            id=non_existent_user_id,
            email="ghost@example.com",
            role="authenticated",
        )

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        response = await client.get("/api/v1/users/me", headers=headers)
        assert response.status_code == 404
        data = response.json()

        assert data["data"] is None
        assert "not found" in data["error"].lower()
    finally:
        app.dependency_overrides.clear()


# ============================================================================
# 2. GET /api/v1/users/lookup Tests
# ============================================================================


@pytest.mark.asyncio
async def test_users_lookup_unauthenticated(client: AsyncClient):
    """Test GET /api/v1/users/lookup rejects unauthenticated requests with 401."""
    response = await client.get("/api/v1/users/lookup?email=alice@example.com")
    assert response.status_code == 401
    data = response.json()
    assert data["data"] is None
    assert "error" in data


@pytest.mark.asyncio
async def test_users_lookup_authenticated_success(client: AsyncClient, test_session: AsyncSession):
    """Test GET /api/v1/users/lookup returns active non-shadow user profile."""
    alice_user_id = uuid.uuid4()
    alice_profile = Profile(
        id=uuid.uuid4(),
        user_id=alice_user_id,
        display_name="Alice Finder",
        email="alice.finder@example.com",
        avatar_url="https://example.com/avatar.png",
        is_shadow=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    test_session.add(alice_profile)
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(
            id=uuid.uuid4(),
            email="searcher@example.com",
            role="authenticated",
        )

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        # Case-insensitive query test
        response = await client.get(
            "/api/v1/users/lookup?email=ALICE.FINDER@EXAMPLE.COM", headers=headers
        )
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is None
        assert data["data"] is not None
        assert data["data"]["user_id"] == str(alice_user_id)
        assert data["data"]["display_name"] == "Alice Finder"
        assert data["data"]["email"] == "alice.finder@example.com"
        assert data["data"]["avatar_url"] == "https://example.com/avatar.png"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_users_lookup_specific_rule_excludes_shadow_profiles(
    client: AsyncClient, test_session: AsyncSession
):
    """Specific authorization rule: shadow profiles (is_shadow == True) must NOT be returned."""
    shadow_user_id = uuid.uuid4()
    shadow_profile = Profile(
        id=uuid.uuid4(),
        user_id=shadow_user_id,
        display_name="Shadow Person",
        email="shadow@example.com",
        is_shadow=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    test_session.add(shadow_profile)
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(
            id=uuid.uuid4(),
            email="searcher@example.com",
            role="authenticated",
        )

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        response = await client.get("/api/v1/users/lookup?email=shadow@example.com", headers=headers)
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is None
        assert data["data"] is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_users_lookup_not_found(client: AsyncClient, test_session: AsyncSession):
    """Test GET /api/v1/users/lookup returns data: None for unknown email."""
    async def override_get_current_user():
        return AuthenticatedUser(
            id=uuid.uuid4(),
            email="searcher@example.com",
            role="authenticated",
        )

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        response = await client.get("/api/v1/users/lookup?email=nobody@example.com", headers=headers)
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is None
        assert data["data"] is None
    finally:
        app.dependency_overrides.clear()


# ============================================================================
# 3. GET /api/v1/categories/ Tests
# ============================================================================


@pytest.mark.asyncio
async def test_categories_unauthenticated(client: AsyncClient):
    """Test GET /api/v1/categories/ rejects unauthenticated requests with 401."""
    response = await client.get("/api/v1/categories/")
    assert response.status_code == 401
    data = response.json()
    assert data["data"] is None
    assert "error" in data


@pytest.mark.asyncio
async def test_categories_authenticated_success_and_authorization(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test GET /api/v1/categories/ returns categories strictly for current_user.id,
    ordered by created_at ascending. Validates Guardrail #5 (strict isolation).
    """
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    # User A categories
    cat_a1 = UserCategory(
        id=uuid.uuid4(),
        user_id=user_a,
        name="Groceries",
        slug="groceries",
        color_index=1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cat_a2 = UserCategory(
        id=uuid.uuid4(),
        user_id=user_a,
        name="Rent",
        slug="rent",
        color_index=2,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    # User B category (must NOT be returned to User A)
    cat_b = UserCategory(
        id=uuid.uuid4(),
        user_id=user_b,
        name="Secret Hobby",
        slug="secret_hobby",
        color_index=3,
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    test_session.add_all([cat_a1, cat_a2, cat_b])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(
            id=user_a,
            email="usera@example.com",
            role="authenticated",
        )

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        response = await client.get("/api/v1/categories/", headers=headers)
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is None
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 2

        slugs = [c["slug"] for c in data["data"]]
        assert slugs == ["groceries", "rent"]  # verified ordering
        assert "secret_hobby" not in slugs  # verified isolation from user B
        assert all(c["user_id"] == str(user_a) for c in data["data"])
    finally:
        app.dependency_overrides.clear()


# ============================================================================
# 4. GET /api/v1/friends/shadow/pending Tests
# ============================================================================


@pytest.mark.asyncio
async def test_friends_shadow_pending_unauthenticated(client: AsyncClient):
    """Test GET /api/v1/friends/shadow/pending rejects unauthenticated requests with 401."""
    response = await client.get("/api/v1/friends/shadow/pending")
    assert response.status_code == 401
    data = response.json()
    assert data["data"] is None
    assert "error" in data


@pytest.mark.asyncio
async def test_friends_shadow_pending_authenticated_success_and_filtering(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test GET /api/v1/friends/shadow/pending queries profiles where
    email == current_user.email AND is_shadow == True.
    Excludes regular active profiles and shadow profiles for other emails.
    """
    creator_id = uuid.uuid4()
    target_email = "target.user@example.com"

    # Matching shadow profile
    matching_shadow = Profile(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        display_name="Target Shadow",
        email=target_email,
        is_shadow=True,
        shadow_created_by=creator_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Same email, but is_shadow == False (should NOT be returned)
    non_shadow_profile = Profile(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        display_name="Real Target",
        email=target_email,
        is_shadow=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Shadow profile, but different email (should NOT be returned)
    other_shadow = Profile(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        display_name="Other Shadow",
        email="other@example.com",
        is_shadow=True,
        shadow_created_by=creator_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    test_session.add_all([matching_shadow, non_shadow_profile, other_shadow])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(
            id=uuid.uuid4(),
            email=target_email.upper(),  # test case-insensitive match
            role="authenticated",
        )

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        response = await client.get("/api/v1/friends/shadow/pending", headers=headers)
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is None
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 1
        assert data["data"][0]["user_id"] == str(matching_shadow.user_id)
        assert data["data"][0]["display_name"] == "Target Shadow"
        assert data["data"][0]["email"] == target_email
        assert data["data"][0]["shadow_created_by"] == str(creator_id)
    finally:
        app.dependency_overrides.clear()


# ============================================================================
# OpenAPI Schema & Guardrail #3 Validation
# ============================================================================


@pytest.mark.asyncio
async def test_openapi_docs(client: AsyncClient):
    """Test OpenAPI schema availability and compliance with all required Phase 2.1 endpoints."""
    response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    assert "paths" in schema
    assert "/api/v1/health" in schema["paths"]
    assert "/api/v1/users/me" in schema["paths"]
    assert "/api/v1/users/lookup" in schema["paths"]
    assert "/api/v1/categories/" in schema["paths"]
    assert "/api/v1/friends/shadow/pending" in schema["paths"]
