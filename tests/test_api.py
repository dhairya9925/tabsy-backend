import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.category import UserCategory
from app.models.expense import Expense, ExpenseSplit
from app.models.friend import Friend
from app.models.group import Group, GroupMember
from app.models.profile import Profile
from app.models.settlement import (
    MemberMonthlyExclusion,
    MemberMonthlyStatus,
    MonthlySettlement,
)


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
# 5. GET /api/v1/expenses/personal Tests
# ============================================================================


@pytest.mark.asyncio
async def test_expenses_personal_unauthenticated(client: AsyncClient):
    """Test GET /api/v1/expenses/personal rejects unauthenticated requests with 401."""
    response = await client.get("/api/v1/expenses/personal")
    assert response.status_code == 401
    data = response.json()
    assert data["data"] is None
    assert "error" in data


@pytest.mark.asyncio
async def test_expenses_personal_authenticated_success_no_filters(
    client: AsyncClient, test_session: AsyncSession
):
    """Test GET /api/v1/expenses/personal returns personal expenses ordered by expense_date desc."""
    user_id = uuid.uuid4()

    exp1 = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("45.50"),
        category="food",
        note="Lunch with colleague",
        expense_date=date(2026, 3, 1),
        group_id=None,
        created_at=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    )
    exp2 = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("120.00"),
        category="groceries",
        note="Weekly supplies",
        expense_date=date(2026, 3, 5),
        group_id=None,
        created_at=datetime(2026, 3, 5, 15, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 5, 15, 0, tzinfo=timezone.utc),
    )
    test_session.add_all([exp1, exp2])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(
            id=user_id,
            email="shopper@example.com",
            role="authenticated",
        )

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        response = await client.get("/api/v1/expenses/personal", headers=headers)
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is None
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 2
        # Verify ordering: exp2 (March 5) comes before exp1 (March 1)
        assert data["data"][0]["id"] == str(exp2.id)
        assert data["data"][0]["amount"] == 120.0
        assert data["data"][1]["id"] == str(exp1.id)
        assert data["data"][1]["amount"] == 45.5
        assert data["meta"]["count"] == 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_expenses_personal_category_filter(
    client: AsyncClient, test_session: AsyncSession
):
    """Test GET /api/v1/expenses/personal filtering by category and handling 'all'."""
    user_id = uuid.uuid4()

    exp_food = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("20.00"),
        category="food",
        note="Snacks",
        expense_date=date(2026, 3, 2),
        group_id=None,
    )
    exp_travel = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("60.00"),
        category="travel",
        note="Cab ride",
        expense_date=date(2026, 3, 3),
        group_id=None,
    )
    test_session.add_all([exp_food, exp_travel])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=user_id, email="u@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}

        # Filter by category "travel"
        res = await client.get("/api/v1/expenses/personal?category=travel", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]
        assert len(data) == 1
        assert data[0]["category"] == "travel"

        # Filter by category "all" should return both
        res_all = await client.get("/api/v1/expenses/personal?category=all", headers=headers)
        assert res_all.status_code == 200
        data_all = res_all.json()["data"]
        assert len(data_all) == 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_expenses_personal_date_range_filter(
    client: AsyncClient, test_session: AsyncSession
):
    """Test GET /api/v1/expenses/personal filtering by start_date and end_date (including camelCase)."""
    user_id = uuid.uuid4()

    exp_jan = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("10.00"),
        category="misc",
        expense_date=date(2026, 1, 10),
        group_id=None,
    )
    exp_feb = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("20.00"),
        category="misc",
        expense_date=date(2026, 2, 15),
        group_id=None,
    )
    exp_mar = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("30.00"),
        category="misc",
        expense_date=date(2026, 3, 20),
        group_id=None,
    )
    test_session.add_all([exp_jan, exp_feb, exp_mar])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=user_id, email="u@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}

        # Snake case start_date & end_date
        res = await client.get(
            "/api/v1/expenses/personal?start_date=2026-02-01&end_date=2026-02-28",
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == str(exp_feb.id)

        # CamelCase startDate & endDate
        res_camel = await client.get(
            "/api/v1/expenses/personal?startDate=2026-02-01&endDate=2026-02-28",
            headers=headers,
        )
        assert res_camel.status_code == 200
        data_camel = res_camel.json()["data"]
        assert len(data_camel) == 1
        assert data_camel[0]["id"] == str(exp_feb.id)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_expenses_personal_search_filter(
    client: AsyncClient, test_session: AsyncSession
):
    """Test GET /api/v1/expenses/personal substring search on note field."""
    user_id = uuid.uuid4()

    exp1 = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("15.00"),
        category="food",
        note="Starbucks Caramel Macchiato",
        expense_date=date(2026, 3, 1),
        group_id=None,
    )
    exp2 = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("80.00"),
        category="shopping",
        note="New shoes from Nike store",
        expense_date=date(2026, 3, 2),
        group_id=None,
    )
    test_session.add_all([exp1, exp2])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=user_id, email="u@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get("/api/v1/expenses/personal?search=caramel", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == str(exp1.id)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_expenses_personal_excludes_external_splits_and_group_expenses(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test personal expenses exclude:
    1. Group expenses (group_id IS NOT NULL).
    2. Expenses with external debtor splits (split.user_id != current_user.id).
    Only returns pure personal expenses (no splits or all splits for self).
    """
    user_id = uuid.uuid4()
    friend_id = uuid.uuid4()

    # 1. Pure personal expense (no splits) -> INCLUDED
    pure_personal = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("25.00"),
        category="food",
        note="Solo lunch",
        expense_date=date(2026, 3, 1),
        group_id=None,
    )

    # 2. Personal expense with split for user self -> INCLUDED
    self_split_exp = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("50.00"),
        category="groceries",
        note="Personal split",
        expense_date=date(2026, 3, 2),
        group_id=None,
    )
    split_self = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=self_split_exp.id,
        user_id=user_id,
        amount=Decimal("50.00"),
    )

    # 3. Friend expense with external split for friend -> EXCLUDED
    shared_friend_exp = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("100.00"),
        category="entertainment",
        note="Concert with friend",
        expense_date=date(2026, 3, 3),
        group_id=None,
    )
    split_friend = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=shared_friend_exp.id,
        user_id=friend_id,
        amount=Decimal("50.00"),
    )

    # 4. Group expense -> EXCLUDED
    group_exp = Expense(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=Decimal("75.00"),
        category="utilities",
        note="Apartment internet",
        expense_date=date(2026, 3, 4),
        group_id=uuid.uuid4(),
    )

    test_session.add_all([
        pure_personal,
        self_split_exp,
        split_self,
        shared_friend_exp,
        split_friend,
        group_exp,
    ])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=user_id, email="u@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get("/api/v1/expenses/personal", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]
        returned_ids = {e["id"] for e in data}

        assert returned_ids == {str(pure_personal.id), str(self_split_exp.id)}
        assert str(shared_friend_exp.id) not in returned_ids
        assert str(group_exp.id) not in returned_ids
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_expenses_personal_strict_user_authorization(
    client: AsyncClient, test_session: AsyncSession
):
    """Verify User A cannot read User B's personal expenses (strict isolation per Guardrail #5)."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    exp_a = Expense(
        id=uuid.uuid4(),
        user_id=user_a,
        amount=Decimal("30.00"),
        category="food",
        note="User A meal",
        expense_date=date(2026, 3, 1),
        group_id=None,
    )
    exp_b = Expense(
        id=uuid.uuid4(),
        user_id=user_b,
        amount=Decimal("90.00"),
        category="shopping",
        note="User B secret purchase",
        expense_date=date(2026, 3, 1),
        group_id=None,
    )
    test_session.add_all([exp_a, exp_b])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=user_a, email="usera@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get("/api/v1/expenses/personal", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]

        assert len(data) == 1
        assert data[0]["id"] == str(exp_a.id)
        assert all(e["user_id"] == str(user_a) for e in data)
    finally:
        app.dependency_overrides.clear()


# ============================================================================
# 6. GET /api/v1/groups and /api/v1/friends Tests
# ============================================================================


@pytest.mark.asyncio
async def test_groups_list_unauthenticated(client: AsyncClient):
    """Test GET /api/v1/groups/ rejects unauthenticated requests with 401."""
    response = await client.get("/api/v1/groups/")
    assert response.status_code == 401
    data = response.json()
    assert data["data"] is None
    assert "error" in data


@pytest.mark.asyncio
async def test_groups_list_only_shows_current_user_groups(
    client: AsyncClient, test_session: AsyncSession
):
    """Test GET /api/v1/groups/ only lists groups where current user is in group_members."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    group_a = Group(
        id=uuid.uuid4(),
        name="Flatmates",
        description="Shared apartment",
        created_by=user_a,
    )
    group_b = Group(
        id=uuid.uuid4(),
        name="Secret Club",
        description="User B only",
        created_by=user_b,
    )
    member_a = GroupMember(
        id=uuid.uuid4(),
        group_id=group_a.id,
        user_id=user_a,
        role="admin",
    )
    member_b = GroupMember(
        id=uuid.uuid4(),
        group_id=group_b.id,
        user_id=user_b,
        role="admin",
    )

    test_session.add_all([group_a, group_b, member_a, member_b])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=user_a, email="usera@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get("/api/v1/groups/", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]

        assert len(data) == 1
        assert data[0]["id"] == str(group_a.id)
        assert data[0]["name"] == "Flatmates"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_group_detail_endpoints(client: AsyncClient, test_session: AsyncSession):
    """
    Test GET /api/v1/groups/{id}:
    - Member can access group detail.
    - Non-member is rejected with 403.
    - Unknown group returns 404.
    """
    user_member = uuid.uuid4()
    user_outsider = uuid.uuid4()

    group = Group(
        id=uuid.uuid4(),
        name="Trip to Goa",
        description="Vacation 2026",
        created_by=user_member,
    )
    member = GroupMember(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=user_member,
        role="admin",
    )
    test_session.add_all([group, member])
    await test_session.commit()

    # 1. Member access -> 200
    async def override_member():
        return AuthenticatedUser(id=user_member, email="member@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_member
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get(f"/api/v1/groups/{group.id}", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["id"] == str(group.id)
        assert data["name"] == "Trip to Goa"
    finally:
        app.dependency_overrides.clear()

    # 2. Outsider access -> 403
    async def override_outsider():
        return AuthenticatedUser(id=user_outsider, email="outsider@example.com", role="authenticated")

    app.dependency_overrides[get_current_user] = override_outsider
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get(f"/api/v1/groups/{group.id}", headers=headers)
        assert res.status_code == 403
        assert "not a member" in res.json()["error"].lower()

        # 3. Not found group -> 404
        res_nf = await client.get(f"/api/v1/groups/{uuid.uuid4()}", headers=headers)
        assert res_nf.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_group_members_rejects_non_members_and_returns_joined_profiles(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test GET /api/v1/groups/{id}/members:
    - Rejects non-members with 403.
    - Joins group_members with profiles in a single query and returns profile metadata.
    """
    user_a = uuid.uuid4()
    user_c = uuid.uuid4()
    user_outsider = uuid.uuid4()

    group = Group(
        id=uuid.uuid4(),
        name="Study Group",
        created_by=user_a,
    )
    m_a = GroupMember(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=user_a,
        role="admin",
    )
    m_c = GroupMember(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=user_c,
        role="member",
    )
    prof_a = Profile(
        id=uuid.uuid4(),
        user_id=user_a,
        display_name="Alice Admin",
        email="alice@college.edu",
        avatar_url="https://example.com/alice.png",
        is_shadow=False,
    )
    prof_c = Profile(
        id=uuid.uuid4(),
        user_id=user_c,
        display_name="Charlie Member",
        email="charlie@college.edu",
        avatar_url="https://example.com/charlie.png",
        is_shadow=False,
    )

    test_session.add_all([group, m_a, m_c, prof_a, prof_c])
    await test_session.commit()

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    # 1. Non-member -> 403
    async def override_outsider():
        return AuthenticatedUser(id=user_outsider, email="out@example.com", role="authenticated")

    app.dependency_overrides[get_current_user] = override_outsider

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get(f"/api/v1/groups/{group.id}/members", headers=headers)
        assert res.status_code == 403
    finally:
        app.dependency_overrides.clear()

    # 2. Member -> 200 with joined profile metadata
    async def override_member():
        return AuthenticatedUser(id=user_a, email="alice@college.edu", role="authenticated")

    app.dependency_overrides[get_current_user] = override_member
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get(f"/api/v1/groups/{group.id}/members", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]

        assert len(data) == 2
        user_map = {m["user_id"]: m for m in data}

        assert str(user_a) in user_map
        assert user_map[str(user_a)]["role"] == "admin"
        assert user_map[str(user_a)]["profile"]["display_name"] == "Alice Admin"
        assert user_map[str(user_a)]["profile"]["email"] == "alice@college.edu"

        assert str(user_c) in user_map
        assert user_map[str(user_c)]["role"] == "member"
        assert user_map[str(user_c)]["profile"]["display_name"] == "Charlie Member"
        assert user_map[str(user_c)]["profile"]["email"] == "charlie@college.edu"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_friends_list_endpoint(client: AsyncClient, test_session: AsyncSession):
    """Test GET /api/v1/friends/ returns user's friends with joined counterpart profile."""
    user_me = uuid.uuid4()
    user_friend = uuid.uuid4()

    friendship = Friend(
        id=uuid.uuid4(),
        user_id=user_me,
        friend_id=user_friend,
        status="accepted",
    )
    friend_profile = Profile(
        id=uuid.uuid4(),
        user_id=user_friend,
        display_name="Buddy Friend",
        email="buddy@example.com",
        avatar_url="https://example.com/buddy.png",
        is_shadow=False,
    )
    test_session.add_all([friendship, friend_profile])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=user_me, email="me@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get("/api/v1/friends/?status=accepted", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]

        assert len(data) == 1
        assert data[0]["friend_id"] == str(user_friend)
        assert data[0]["profile"]["display_name"] == "Buddy Friend"
        assert data[0]["profile"]["email"] == "buddy@example.com"
    finally:
        app.dependency_overrides.clear()


# ============================================================================
# 7. Phase 2.4 — Friends & 1-on-1 Expenses Tests
# ============================================================================


@pytest.mark.asyncio
async def test_friends_status_filters_and_counterpart_profiles(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test GET /api/v1/friends/?status=accepted|pending|sent|all
    - accepted: (user_id == current_user.id OR friend_id == current_user.id) AND status == 'accepted'
    - pending (incoming): friend_id == current_user.id AND status == 'pending'
    - sent (outgoing): user_id == current_user.id AND status == 'pending'
    - all: all friendship records involving current_user.id
    - Join with profiles to include counterpart metadata.
    """
    user_me = uuid.uuid4()
    friend_accepted = uuid.uuid4()
    friend_incoming = uuid.uuid4()
    friend_outgoing = uuid.uuid4()

    # 1. Accepted friend
    f_acc = Friend(
        id=uuid.uuid4(),
        user_id=user_me,
        friend_id=friend_accepted,
        status="accepted",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    p_acc = Profile(
        id=uuid.uuid4(),
        user_id=friend_accepted,
        display_name="Accepted Friend",
        email="accepted@example.com",
        avatar_url="https://example.com/accepted.png",
        is_shadow=False,
    )

    # 2. Incoming friend request (friend_incoming sent to user_me)
    f_inc = Friend(
        id=uuid.uuid4(),
        user_id=friend_incoming,
        friend_id=user_me,
        status="pending",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    p_inc = Profile(
        id=uuid.uuid4(),
        user_id=friend_incoming,
        display_name="Incoming Requester",
        email="incoming@example.com",
        is_shadow=False,
    )

    # 3. Outgoing friend request (user_me sent to friend_outgoing)
    f_out = Friend(
        id=uuid.uuid4(),
        user_id=user_me,
        friend_id=friend_outgoing,
        status="pending",
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    p_out = Profile(
        id=uuid.uuid4(),
        user_id=friend_outgoing,
        display_name="Outgoing Target",
        email="outgoing@example.com",
        is_shadow=True,
    )

    test_session.add_all([f_acc, p_acc, f_inc, p_inc, f_out, p_out])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=user_me, email="me@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}

        # 1. status=accepted (default)
        res_acc = await client.get("/api/v1/friends/?status=accepted", headers=headers)
        assert res_acc.status_code == 200
        data_acc = res_acc.json()["data"]
        assert len(data_acc) == 1
        assert data_acc[0]["id"] == str(f_acc.id)
        assert data_acc[0]["profile"]["display_name"] == "Accepted Friend"
        assert data_acc[0]["profile"]["user_id"] == str(friend_accepted)

        # 2. status=pending (incoming only)
        res_inc = await client.get("/api/v1/friends/?status=pending", headers=headers)
        assert res_inc.status_code == 200
        data_inc = res_inc.json()["data"]
        assert len(data_inc) == 1
        assert data_inc[0]["id"] == str(f_inc.id)
        assert data_inc[0]["user_id"] == str(friend_incoming)
        assert data_inc[0]["friend_id"] == str(user_me)
        assert data_inc[0]["profile"]["display_name"] == "Incoming Requester"

        # 3. status=sent (outgoing only)
        res_out = await client.get("/api/v1/friends/?status=sent", headers=headers)
        assert res_out.status_code == 200
        data_out = res_out.json()["data"]
        assert len(data_out) == 1
        assert data_out[0]["id"] == str(f_out.id)
        assert data_out[0]["user_id"] == str(user_me)
        assert data_out[0]["friend_id"] == str(friend_outgoing)
        assert data_out[0]["profile"]["display_name"] == "Outgoing Target"
        assert data_out[0]["profile"]["is_shadow"] is True

        # 4. status=all (all 3)
        res_all = await client.get("/api/v1/friends/?status=all", headers=headers)
        assert res_all.status_code == 200
        data_all = res_all.json()["data"]
        assert len(data_all) == 3
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_friend_balances_calculation_with_partial_settlement_and_exclusions(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test GET /api/v1/friends/balances calculation matches client-side math:
    - Non-group expenses involving user
    - Unsettled splits only
    - Multi-expense fixture with partial settlements
    - Group expenses excluded
    """
    user_me = uuid.uuid4()
    friend_b = uuid.uuid4()
    friend_c = uuid.uuid4()

    # Expense 1: User Me pays 100.00, split 50/50 unsettled with Friend B
    # Friend B owes User Me +50.00
    exp1 = Expense(
        id=uuid.uuid4(),
        user_id=user_me,
        paid_by=user_me,
        amount=Decimal("100.00"),
        category="food",
        expense_date=date(2026, 3, 1),
        group_id=None,
    )
    s1_me = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=exp1.id,
        user_id=user_me,
        amount=Decimal("50.00"),
        is_settled=False,
    )
    s1_b = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=exp1.id,
        user_id=friend_b,
        amount=Decimal("50.00"),
        is_settled=False,
    )

    # Expense 2: Friend B pays 40.00, split 20/20 unsettled with User Me
    # User Me owes Friend B 20.00 -> Net with Friend B: 50.00 - 20.00 = +30.00
    exp2 = Expense(
        id=uuid.uuid4(),
        user_id=friend_b,
        paid_by=friend_b,
        amount=Decimal("40.00"),
        category="transport",
        expense_date=date(2026, 3, 2),
        group_id=None,
    )
    s2_b = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=exp2.id,
        user_id=friend_b,
        amount=Decimal("20.00"),
        is_settled=False,
    )
    s2_me = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=exp2.id,
        user_id=user_me,
        amount=Decimal("20.00"),
        is_settled=False,
    )

    # Expense 3: Friend B pays 30.00 for User Me, but is_settled == True!
    # Must be ignored in net balance calculation!
    exp3 = Expense(
        id=uuid.uuid4(),
        user_id=friend_b,
        paid_by=friend_b,
        amount=Decimal("30.00"),
        category="payment",
        expense_date=date(2026, 3, 3),
        group_id=None,
    )
    s3_me = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=exp3.id,
        user_id=user_me,
        amount=Decimal("30.00"),
        is_settled=True,  # Settled!
    )

    # Expense 4: Group expense involving User Me and Friend B
    # Must be ignored (group_id is not null)!
    exp4 = Expense(
        id=uuid.uuid4(),
        user_id=user_me,
        paid_by=user_me,
        amount=Decimal("60.00"),
        category="groceries",
        expense_date=date(2026, 3, 4),
        group_id=uuid.uuid4(),
    )
    s4_b = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=exp4.id,
        user_id=friend_b,
        amount=Decimal("30.00"),
        is_settled=False,
    )

    # Expense 5: Friend C pays 75.25 for User Me (unsettled)
    # User Me owes Friend C -75.25
    exp5 = Expense(
        id=uuid.uuid4(),
        user_id=friend_c,
        paid_by=friend_c,
        amount=Decimal("75.25"),
        category="utilities",
        expense_date=date(2026, 3, 5),
        group_id=None,
    )
    s5_me = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=exp5.id,
        user_id=user_me,
        amount=Decimal("75.25"),
        is_settled=False,
    )

    test_session.add_all([
        exp1, s1_me, s1_b,
        exp2, s2_b, s2_me,
        exp3, s3_me,
        exp4, s4_b,
        exp5, s5_me,
    ])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=user_me, email="me@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get("/api/v1/friends/balances", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]

        balances_by_id = {b["friendId"]: b["netBalance"] for b in data}

        # Friend B: +30.00
        assert str(friend_b) in balances_by_id
        assert balances_by_id[str(friend_b)] == 30.00

        # Friend C: -75.25
        assert str(friend_c) in balances_by_id
        assert balances_by_id[str(friend_c)] == -75.25
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_friend_expenses_and_profile_authorization_rejection(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test GET /api/v1/friends/{friend_id}/expenses and /profile:
    - Current user must be a friend (accepted) or have shared shadow profile.
    - Non-friends (strangers or pending-only) are rejected with 403 Forbidden.
    - Active friends can access both endpoints.
    - Shared shadow profile can access both endpoints.
    - Unknown friend profile returns 404.
    """
    user_me = uuid.uuid4()
    friend_accepted = uuid.uuid4()
    stranger = uuid.uuid4()
    pending_only_user = uuid.uuid4()
    shadow_friend = uuid.uuid4()

    # Active friendship with friend_accepted
    f_acc = Friend(
        id=uuid.uuid4(),
        user_id=user_me,
        friend_id=friend_accepted,
        status="accepted",
    )
    p_acc = Profile(
        id=uuid.uuid4(),
        user_id=friend_accepted,
        display_name="Accepted Friend",
        email="accepted@example.com",
        avatar_url="https://example.com/accepted.png",
        is_shadow=False,
    )

    # Pending-only friendship
    f_pend = Friend(
        id=uuid.uuid4(),
        user_id=pending_only_user,
        friend_id=user_me,
        status="pending",
    )
    p_pend = Profile(
        id=uuid.uuid4(),
        user_id=pending_only_user,
        display_name="Pending Friend",
        email="pending@example.com",
        is_shadow=False,
    )

    # Shared shadow profile (created by user_me)
    p_shadow = Profile(
        id=uuid.uuid4(),
        user_id=shadow_friend,
        display_name="Shadow Contact",
        email="shadow@example.com",
        is_shadow=True,
        shadow_created_by=user_me,
    )

    # 1-on-1 expense between user_me and friend_accepted
    exp_shared = Expense(
        id=uuid.uuid4(),
        user_id=user_me,
        paid_by=user_me,
        amount=Decimal("50.00"),
        category="food",
        expense_date=date(2026, 3, 1),
        group_id=None,
    )
    exp_split = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=exp_shared.id,
        user_id=friend_accepted,
        amount=Decimal("25.00"),
    )

    test_session.add_all([f_acc, p_acc, f_pend, p_pend, p_shadow, exp_shared, exp_split])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=user_me, email="me@example.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}

        # 1. Stranger access -> 403 Forbidden
        res_stranger_exp = await client.get(f"/api/v1/friends/{stranger}/expenses", headers=headers)
        assert res_stranger_exp.status_code == 403
        assert res_stranger_exp.json()["data"] is None

        res_stranger_prof = await client.get(f"/api/v1/friends/{stranger}/profile", headers=headers)
        assert res_stranger_prof.status_code == 403
        assert res_stranger_prof.json()["data"] is None

        # 2. Pending-only friend -> 403 Forbidden
        res_pend_exp = await client.get(f"/api/v1/friends/{pending_only_user}/expenses", headers=headers)
        assert res_pend_exp.status_code == 403

        res_pend_prof = await client.get(f"/api/v1/friends/{pending_only_user}/profile", headers=headers)
        assert res_pend_prof.status_code == 403

        # 3. Accepted friend access -> 200 OK
        res_acc_exp = await client.get(f"/api/v1/friends/{friend_accepted}/expenses", headers=headers)
        assert res_acc_exp.status_code == 200
        data_exp = res_acc_exp.json()["data"]
        assert len(data_exp) == 1
        assert data_exp[0]["id"] == str(exp_shared.id)
        assert data_exp[0]["amount"] == 50.0

        res_acc_prof = await client.get(f"/api/v1/friends/{friend_accepted}/profile", headers=headers)
        assert res_acc_prof.status_code == 200
        data_prof = res_acc_prof.json()["data"]
        assert data_prof["user_id"] == str(friend_accepted)
        assert data_prof["display_name"] == "Accepted Friend"

        # 4. Shadow friend access -> 200 OK
        res_shadow_prof = await client.get(f"/api/v1/friends/{shadow_friend}/profile", headers=headers)
        assert res_shadow_prof.status_code == 200
        assert res_shadow_prof.json()["data"]["is_shadow"] is True

        res_shadow_exp = await client.get(f"/api/v1/friends/{shadow_friend}/expenses", headers=headers)
        assert res_shadow_exp.status_code == 200
        assert res_shadow_exp.json()["data"] == []
    finally:
        app.dependency_overrides.clear()


# ============================================================================
# 8. Phase 2.5 — Group Expenses, Balances & Settlements Tests
# ============================================================================


@pytest.mark.asyncio
async def test_group_expenses_member_authorization_and_data(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test GET /api/v1/groups/{id}/expenses:
    - Member can view expenses with eager-loaded splits and profile names.
    - Excludes 'Automated Monthly Rent' expenses.
    - Non-member access is rejected with 403 Forbidden.
    - Nonexistent group returns 404.
    """
    user_member = uuid.uuid4()
    user_outsider = uuid.uuid4()
    user_split = uuid.uuid4()

    group = Group(
        id=uuid.uuid4(),
        name="Flat 402",
        created_by=user_member,
    )
    gm = GroupMember(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=user_member,
        role="admin",
    )
    gm_split = GroupMember(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=user_split,
        role="member",
    )
    prof_member = Profile(
        id=uuid.uuid4(),
        user_id=user_member,
        display_name="Alice Payer",
        email="alice@flat.com",
    )
    prof_split = Profile(
        id=uuid.uuid4(),
        user_id=user_split,
        display_name="Bob Splitter",
        email="bob@flat.com",
    )

    # 1. Normal group expense
    exp_normal = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=user_member,
        paid_by=user_member,
        amount=Decimal("80.00"),
        category="groceries",
        note="Weekly food run",
        expense_date=date(2026, 3, 2),
    )
    split_normal = ExpenseSplit(
        id=uuid.uuid4(),
        expense_id=exp_normal.id,
        user_id=user_split,
        amount=Decimal("40.00"),
        is_settled=False,
    )

    # 2. Automated rent expense (must be excluded from group expense feed)
    exp_rent = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=user_member,
        paid_by=user_member,
        amount=Decimal("1200.00"),
        category="rent",
        note="Automated Monthly Rent",
        expense_date=date(2026, 3, 1),
    )

    test_session.add_all([
        group, gm, gm_split, prof_member, prof_split,
        exp_normal, split_normal, exp_rent,
    ])
    await test_session.commit()

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    # 1. Member access -> 200 OK
    async def override_member():
        return AuthenticatedUser(id=user_member, email="alice@flat.com", role="authenticated")

    app.dependency_overrides[get_current_user] = override_member

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get(f"/api/v1/groups/{group.id}/expenses", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]

        assert len(data) == 1
        assert data[0]["id"] == str(exp_normal.id)
        assert data[0]["payer_name"] == "Alice Payer"
        assert len(data[0]["splits"]) == 1
        assert data[0]["splits"][0]["member_name"] == "Bob Splitter"
        assert data[0]["splits"][0]["amount"] == 40.0
    finally:
        app.dependency_overrides.clear()

    # 2. Outsider access -> 403 Forbidden
    async def override_outsider():
        return AuthenticatedUser(id=user_outsider, email="outsider@flat.com", role="authenticated")

    app.dependency_overrides[get_current_user] = override_outsider
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get(f"/api/v1/groups/{group.id}/expenses", headers=headers)
        assert res.status_code == 403

        # Nonexistent group -> 404
        res_nf = await client.get(f"/api/v1/groups/{uuid.uuid4()}/expenses", headers=headers)
        assert res_nf.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_group_balances_standard_peer_to_peer_fixture_match(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test GET /api/v1/groups/{id}/balances:
    Replicates exact greedy debt simplification algorithm on standard group fixture:
    - 4 members: Alice, Bob, Charlie, Dave
    - Multiple expenses with partial settlements and exclusions
    - Exactly asserts server output matches client algorithm:
      1. Charlie owes Alice 50.00
      2. Dave owes Alice 30.00
      3. Dave owes Bob 10.00
    """
    u_alice = uuid.uuid4()
    u_bob = uuid.uuid4()
    u_charlie = uuid.uuid4()
    u_dave = uuid.uuid4()

    group = Group(
        id=uuid.uuid4(),
        name="Ski Trip 2026",
        type="day_to_day",
        created_by=u_alice,
    )

    members = [
        GroupMember(id=uuid.uuid4(), group_id=group.id, user_id=u_alice, role="admin"),
        GroupMember(id=uuid.uuid4(), group_id=group.id, user_id=u_bob, role="member"),
        GroupMember(id=uuid.uuid4(), group_id=group.id, user_id=u_charlie, role="member"),
        GroupMember(id=uuid.uuid4(), group_id=group.id, user_id=u_dave, role="member"),
    ]

    profiles = [
        Profile(id=uuid.uuid4(), user_id=u_alice, display_name="Alice"),
        Profile(id=uuid.uuid4(), user_id=u_bob, display_name="Bob"),
        Profile(id=uuid.uuid4(), user_id=u_charlie, display_name="Charlie"),
        Profile(id=uuid.uuid4(), user_id=u_dave, display_name="Dave"),
    ]

    # Expense 1: Alice pays 120.00, splits 30 each (all unsettled)
    # Alice: +90, Bob: -30, Charlie: -30, Dave: -30
    exp1 = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_alice,
        paid_by=u_alice,
        amount=Decimal("120.00"),
        category="food",
        expense_date=date(2026, 3, 1),
    )
    s1 = [
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp1.id, user_id=u_alice, amount=Decimal("30.00"), is_settled=False),
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp1.id, user_id=u_bob, amount=Decimal("30.00"), is_settled=False),
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp1.id, user_id=u_charlie, amount=Decimal("30.00"), is_settled=False),
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp1.id, user_id=u_dave, amount=Decimal("30.00"), is_settled=False),
    ]

    # Expense 2: Bob pays 60.00, splits 20 each for Bob, Charlie, Dave (Alice 0)
    # Bob: +40, Charlie: -20, Dave: -20
    # Running: Alice +90, Bob +10, Charlie -50, Dave -50
    exp2 = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_bob,
        paid_by=u_bob,
        amount=Decimal("60.00"),
        category="transport",
        expense_date=date(2026, 3, 2),
    )
    s2 = [
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp2.id, user_id=u_bob, amount=Decimal("20.00"), is_settled=False),
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp2.id, user_id=u_charlie, amount=Decimal("20.00"), is_settled=False),
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp2.id, user_id=u_dave, amount=Decimal("20.00"), is_settled=False),
    ]

    # Expense 3: Charlie pays 30.00, split Alice 15, Charlie 15, BUT Alice's split is settled!
    # Settled split is ignored!
    exp3 = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_charlie,
        paid_by=u_charlie,
        amount=Decimal("30.00"),
        category="drinks",
        expense_date=date(2026, 3, 3),
    )
    s3 = [
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp3.id, user_id=u_alice, amount=Decimal("15.00"), is_settled=True),
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp3.id, user_id=u_charlie, amount=Decimal("15.00"), is_settled=False),
    ]

    # Expense 4: Automated Monthly Rent 1000.00 (MUST BE IGNORED)
    exp4 = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_alice,
        paid_by=u_alice,
        amount=Decimal("1000.00"),
        category="rent",
        note="Automated Monthly Rent",
        expense_date=date(2026, 3, 4),
    )
    s4 = [
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp4.id, user_id=u_bob, amount=Decimal("500.00"), is_settled=False),
    ]

    # Expense 5: category == 'system' (MUST BE IGNORED)
    exp5 = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_alice,
        paid_by=u_alice,
        amount=Decimal("50.00"),
        category="system",
        note="System adjustment",
        expense_date=date(2026, 3, 5),
    )
    s5 = [
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp5.id, user_id=u_dave, amount=Decimal("50.00"), is_settled=False),
    ]

    # Expense 6: Dave pays 10.00 for Alice (Alice split 10, unsettled)
    # Dave: +10, Alice: -10
    # Final Net: Alice +80, Bob +10, Charlie -50, Dave -40
    exp6 = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_dave,
        paid_by=u_dave,
        amount=Decimal("10.00"),
        category="snacks",
        expense_date=date(2026, 3, 6),
    )
    s6 = [
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp6.id, user_id=u_alice, amount=Decimal("10.00"), is_settled=False),
    ]

    test_session.add_all([
        group, *members, *profiles,
        exp1, *s1,
        exp2, *s2,
        exp3, *s3,
        exp4, *s4,
        exp5, *s5,
        exp6, *s6,
    ])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=u_alice, email="alice@test.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get(f"/api/v1/groups/{group.id}/balances", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]

        # Expected debt simplification:
        # Creditors: Alice (80), Bob (10)
        # Debtors: Charlie (50), Dave (40)
        # 1. Charlie pays Alice 50
        # 2. Dave pays Alice 30
        # 3. Dave pays Bob 10
        assert len(data) == 3

        assert data[0]["from_user_id"] == str(u_charlie)
        assert data[0]["from_name"] == "Charlie"
        assert data[0]["to_user_id"] == str(u_alice)
        assert data[0]["to_name"] == "Alice"
        assert data[0]["amount"] == 50.00

        assert data[1]["from_user_id"] == str(u_dave)
        assert data[1]["from_name"] == "Dave"
        assert data[1]["to_user_id"] == str(u_alice)
        assert data[1]["to_name"] == "Alice"
        assert data[1]["amount"] == 30.00

        assert data[2]["from_user_id"] == str(u_dave)
        assert data[2]["from_name"] == "Dave"
        assert data[2]["to_user_id"] == str(u_bob)
        assert data[2]["to_name"] == "Bob"
        assert data[2]["amount"] == 10.00

        # Assert outsider authorization rejection on /balances
        async def override_outsider():
            return AuthenticatedUser(id=uuid.uuid4(), email="outsider@test.com", role="authenticated")

        app.dependency_overrides[get_current_user] = override_outsider
        res_outsider = await client.get(f"/api/v1/groups/{group.id}/balances", headers=headers)
        assert res_outsider.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_group_balances_reimbursable_sponsor_fixture_match(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test GET /api/v1/groups/{id}/balances for Reimbursable group:
    - Sponsor owes the payer for all unsettled splits.
    - E1 pays 150.00 (splits: E1 75, E2 75)
    - E2 pays 50.00 (split: E2 50)
    - Result: Sponsor owes E1 150.00, Sponsor owes E2 50.00
    """
    u_sponsor = uuid.uuid4()
    u_e1 = uuid.uuid4()
    u_e2 = uuid.uuid4()

    group = Group(
        id=uuid.uuid4(),
        name="Company Offsite",
        type="reimbursable",
        sponsor_id=str(u_sponsor),
        created_by=u_sponsor,
    )

    members = [
        GroupMember(id=uuid.uuid4(), group_id=group.id, user_id=u_sponsor, role="admin"),
        GroupMember(id=uuid.uuid4(), group_id=group.id, user_id=u_e1, role="member"),
        GroupMember(id=uuid.uuid4(), group_id=group.id, user_id=u_e2, role="member"),
    ]

    profiles = [
        Profile(id=uuid.uuid4(), user_id=u_sponsor, display_name="Company Sponsor"),
        Profile(id=uuid.uuid4(), user_id=u_e1, display_name="Employee 1"),
        Profile(id=uuid.uuid4(), user_id=u_e2, display_name="Employee 2"),
    ]

    exp1 = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_e1,
        paid_by=u_e1,
        amount=Decimal("150.00"),
        category="travel",
        expense_date=date(2026, 3, 1),
    )
    s1 = [
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp1.id, user_id=u_e1, amount=Decimal("75.00"), is_settled=False),
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp1.id, user_id=u_e2, amount=Decimal("75.00"), is_settled=False),
    ]

    exp2 = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_e2,
        paid_by=u_e2,
        amount=Decimal("50.00"),
        category="supplies",
        expense_date=date(2026, 3, 2),
    )
    s2 = [
        ExpenseSplit(id=uuid.uuid4(), expense_id=exp2.id, user_id=u_e2, amount=Decimal("50.00"), is_settled=False),
    ]

    test_session.add_all([
        group, *members, *profiles,
        exp1, *s1,
        exp2, *s2,
    ])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=u_e1, email="e1@company.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get(f"/api/v1/groups/{group.id}/balances", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]

        # Sponsor owes E1 150, Sponsor owes E2 50
        assert len(data) == 2
        assert data[0]["from_user_id"] == str(u_sponsor)
        assert data[0]["to_user_id"] == str(u_e1)
        assert data[0]["amount"] == 150.00

        assert data[1]["from_user_id"] == str(u_sponsor)
        assert data[1]["to_user_id"] == str(u_e2)
        assert data[1]["amount"] == 50.00
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_group_settlements_and_exclusions_endpoints(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Test GET /api/v1/groups/{id}/settlements/... and exclusions:
    - Monthly settlement lookup (found and not found)
    - Multi-month settlements with limit
    - Member status lookup
    - Month-bounded group expenses lookup
    - Member exclusions lookup
    - Member authorization enforced (403 for outsider)
    """
    u_user = uuid.uuid4()
    u_outsider = uuid.uuid4()

    group = Group(
        id=uuid.uuid4(),
        name="Roommates",
        created_by=u_user,
    )
    gm = GroupMember(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_user,
        role="admin",
    )

    settlement = MonthlySettlement(
        id=uuid.uuid4(),
        group_id=group.id,
        month=3,
        year=2026,
        status="open",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    status_row = MemberMonthlyStatus(
        id=uuid.uuid4(),
        settlement_id=settlement.id,
        user_id=u_user,
        status="complete",
        created_at=datetime.now(timezone.utc),
    )
    exclusion = MemberMonthlyExclusion(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_user,
        month=3,
        year=2026,
        exclusion_type="partial",
        created_at=datetime.now(timezone.utc),
    )

    exp_march = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_user,
        paid_by=u_user,
        amount=Decimal("45.00"),
        category="groceries",
        expense_date=date(2026, 3, 15),
    )
    exp_april = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_user,
        paid_by=u_user,
        amount=Decimal("55.00"),
        category="groceries",
        expense_date=date(2026, 4, 1),
    )

    test_session.add_all([
        group, gm, settlement, status_row, exclusion, exp_march, exp_april
    ])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=u_user, email="user@room.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}

        # 1. GET /{id}/settlements/{month}/{year}
        res_found = await client.get(f"/api/v1/groups/{group.id}/settlements/3/2026", headers=headers)
        assert res_found.status_code == 200
        assert res_found.json()["data"]["id"] == str(settlement.id)
        assert res_found.json()["data"]["status"] == "open"

        res_not_found = await client.get(f"/api/v1/groups/{group.id}/settlements/2/2026", headers=headers)
        assert res_not_found.status_code == 200
        assert res_not_found.json()["data"] is None

        # 2. GET /{id}/settlements/multi?limit=5
        res_multi = await client.get(f"/api/v1/groups/{group.id}/settlements/multi?limit=5", headers=headers)
        assert res_multi.status_code == 200
        assert len(res_multi.json()["data"]) == 1

        # 3. GET /{id}/settlements/{settlement_id}/member-status
        res_status = await client.get(f"/api/v1/groups/{group.id}/settlements/{settlement.id}/member-status", headers=headers)
        assert res_status.status_code == 200
        assert len(res_status.json()["data"]) == 1
        assert res_status.json()["data"][0]["user_id"] == str(u_user)

        # 4. GET /{id}/settlements/{month}/{year}/expenses
        res_expenses = await client.get(f"/api/v1/groups/{group.id}/settlements/3/2026/expenses", headers=headers)
        assert res_expenses.status_code == 200
        assert len(res_expenses.json()["data"]) == 1
        assert res_expenses.json()["data"][0]["id"] == str(exp_march.id)

        # 5. GET /{id}/exclusions/{month}/{year}
        res_exclusions = await client.get(f"/api/v1/groups/{group.id}/exclusions/3/2026", headers=headers)
        assert res_exclusions.status_code == 200
        assert len(res_exclusions.json()["data"]) == 1
        assert res_exclusions.json()["data"][0]["exclusion_type"] == "partial"
    finally:
        app.dependency_overrides.clear()

    # 6. Non-member authorization rejection
    async def override_outsider():
        return AuthenticatedUser(id=u_outsider, email="out@test.com", role="authenticated")

    app.dependency_overrides[get_current_user] = override_outsider
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.verified.token"}
        res = await client.get(f"/api/v1/groups/{group.id}/settlements/3/2026", headers=headers)
        assert res.status_code == 403

        res = await client.get(f"/api/v1/groups/{group.id}/settlements/multi", headers=headers)
        assert res.status_code == 403

        res = await client.get(f"/api/v1/groups/{group.id}/exclusions/3/2026", headers=headers)
        assert res.status_code == 403
    finally:
        app.dependency_overrides.clear()


# ============================================================================
# OpenAPI Schema & Guardrail #3 Validation
# ============================================================================


@pytest.mark.asyncio
async def test_openapi_docs(client: AsyncClient):
    """Test OpenAPI schema availability and compliance with all required Phase 2 endpoints."""
    response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    assert "paths" in schema
    assert "/api/v1/health" in schema["paths"]
    assert "/api/v1/users/me" in schema["paths"]
    assert "/api/v1/users/lookup" in schema["paths"]
    assert "/api/v1/categories/" in schema["paths"]
    assert "/api/v1/friends/shadow/pending" in schema["paths"]
    assert "/api/v1/expenses/personal" in schema["paths"]
    assert "/api/v1/groups/" in schema["paths"]
    assert "/api/v1/groups/{id}" in schema["paths"]
    assert "/api/v1/groups/{id}/members" in schema["paths"]
    assert "/api/v1/groups/{id}/expenses" in schema["paths"]
    assert "/api/v1/groups/{id}/balances" in schema["paths"]
    assert "/api/v1/groups/{id}/settlements/multi" in schema["paths"]
    assert "/api/v1/groups/{id}/settlements/{settlement_id}/member-status" in schema["paths"]
    assert "/api/v1/groups/{id}/settlements/{month}/{year}/expenses" in schema["paths"]
    assert "/api/v1/groups/{id}/settlements/{month}/{year}" in schema["paths"]
    assert "/api/v1/groups/{id}/exclusions/{month}/{year}" in schema["paths"]
    assert "/api/v1/friends/" in schema["paths"]
    assert "/api/v1/friends/balances" in schema["paths"]
    assert "/api/v1/friends/{friend_id}/expenses" in schema["paths"]
    assert "/api/v1/friends/{friend_id}/profile" in schema["paths"]
    assert "/api/v1/dashboard/summary" in schema["paths"]


@pytest.mark.asyncio
async def test_dashboard_summary_unauthenticated(client: AsyncClient):
    res = await client.get("/api/v1/dashboard/summary")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_summary_empty_state(
    client: AsyncClient, test_session: AsyncSession
):
    u_new = uuid.uuid4()
    profile = Profile(id=uuid.uuid4(), user_id=u_new, display_name="New User")
    test_session.add(profile)
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=u_new, email="new@user.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.token"}
        res = await client.get("/api/v1/dashboard/summary", headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["personalTotal"] == 0.0
        assert data["personalPrevMonthTotal"] == 0.0
        assert data["personalExpenseCount"] == 0
        assert data["personalExpenses"] == []
        assert data["groups"] == []
        assert data["groupShareTotal"] == 0.0
        assert data["groupBalances"] == []
        assert data["friendBalances"] == []
        assert data["unifiedTotal"] == 0.0
        assert data["netOwed"] == 0.0
        assert data["netOwes"] == 0.0
        assert data["netBalance"] == 0.0
        assert data["unsettledCount"] == 0
        assert data["monthlyTransactionCount"] == 0
        assert data["recentActivity"] == []
        assert data["lastExpenseDate"] is None
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_dashboard_summary_cross_check_with_domain_endpoints(
    client: AsyncClient, test_session: AsyncSession
):
    """
    Asserts the dashboard summary's numbers match the sum of what the individual domain
    endpoints return for the same test user/fixture:
    - Personal expenses (/api/v1/expenses/personal)
    - Friend balances (/api/v1/friends/balances)
    - Group balances (/api/v1/groups/{id}/balances)
    """
    today = date.today()
    d_this_month = date(today.year, today.month, 15)
    first_this = date(today.year, today.month, 1)
    last_prev = first_this - timedelta(days=1)
    d_prev_month = date(last_prev.year, last_prev.month, 15)

    u_main = uuid.uuid4()
    u_friend = uuid.uuid4()
    u_member = uuid.uuid4()

    prof_main = Profile(id=uuid.uuid4(), user_id=u_main, display_name="Main User")
    prof_friend = Profile(id=uuid.uuid4(), user_id=u_friend, display_name="Best Friend")
    prof_member = Profile(id=uuid.uuid4(), user_id=u_member, display_name="Roommate")

    # 1. Personal expenses:
    p_exp1 = Expense(
        id=uuid.uuid4(),
        user_id=u_main,
        paid_by=u_main,
        amount=Decimal("50.00"),
        category="food",
        note="Lunch",
        expense_date=d_this_month,
    )
    p_exp2 = Expense(
        id=uuid.uuid4(),
        user_id=u_main,
        paid_by=u_main,
        amount=Decimal("30.00"),
        category="shopping",
        note="Books",
        expense_date=d_prev_month,
    )

    # 2. Friend setup (1-on-1 expense):
    # Main pays 40.00, splits: Main 20.00, Friend 20.00 (unsettled)
    f_exp = Expense(
        id=uuid.uuid4(),
        user_id=u_main,
        paid_by=u_main,
        amount=Decimal("40.00"),
        category="entertainment",
        note="Movie tickets",
        expense_date=d_this_month,
    )
    f_split_main = ExpenseSplit(
        id=uuid.uuid4(), expense_id=f_exp.id, user_id=u_main, amount=Decimal("20.00"), is_settled=False
    )
    f_split_friend = ExpenseSplit(
        id=uuid.uuid4(), expense_id=f_exp.id, user_id=u_friend, amount=Decimal("20.00"), is_settled=False
    )

    # 3. Group setup:
    group = Group(
        id=uuid.uuid4(),
        name="Apartment 4B",
        type="flat",
        created_by=u_main,
    )
    gm1 = GroupMember(id=uuid.uuid4(), group_id=group.id, user_id=u_main, role="admin")
    gm2 = GroupMember(id=uuid.uuid4(), group_id=group.id, user_id=u_member, role="member")

    g_exp = Expense(
        id=uuid.uuid4(),
        group_id=group.id,
        user_id=u_main,
        paid_by=u_main,
        amount=Decimal("60.00"),
        category="utilities",
        note="Internet bill",
        expense_date=d_this_month,
    )
    g_split_main = ExpenseSplit(
        id=uuid.uuid4(), expense_id=g_exp.id, user_id=u_main, amount=Decimal("30.00"), is_settled=False
    )
    g_split_member = ExpenseSplit(
        id=uuid.uuid4(), expense_id=g_exp.id, user_id=u_member, amount=Decimal("30.00"), is_settled=False
    )

    test_session.add_all([
        prof_main, prof_friend, prof_member,
        p_exp1, p_exp2,
        f_exp, f_split_main, f_split_friend,
        group, gm1, gm2,
        g_exp, g_split_main, g_split_member,
    ])
    await test_session.commit()

    async def override_get_current_user():
        return AuthenticatedUser(id=u_main, email="main@test.com", role="authenticated")

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        headers = {"Authorization": "Bearer fake.token"}

        # Query individual domain endpoints
        res_personal = await client.get("/api/v1/expenses/personal", headers=headers)
        assert res_personal.status_code == 200
        personal_items = res_personal.json()["data"]

        res_friends = await client.get("/api/v1/friends/balances", headers=headers)
        assert res_friends.status_code == 200
        friends_balances = res_friends.json()["data"]

        res_group_balances = await client.get(f"/api/v1/groups/{group.id}/balances", headers=headers)
        assert res_group_balances.status_code == 200
        group_balances = res_group_balances.json()["data"]

        # Query consolidated dashboard endpoint
        res_dashboard = await client.get("/api/v1/dashboard/summary", headers=headers)
        assert res_dashboard.status_code == 200
        dash = res_dashboard.json()["data"]

        # ── Cross-Checks ──
        # 1. Personal Expenses Check:
        cur_month_str = today.strftime("%Y-%m")
        prev_month_str = last_prev.strftime("%Y-%m")

        expected_personal_this_month = sum(
            p["amount"] for p in personal_items if p["expense_date"].startswith(cur_month_str)
        )
        expected_personal_prev_month = sum(
            p["amount"] for p in personal_items if p["expense_date"].startswith(prev_month_str)
        )
        assert dash["personalTotal"] == expected_personal_this_month
        assert dash["personalPrevMonthTotal"] == expected_personal_prev_month
        assert len(dash["personalExpenses"]) == len(personal_items)

        # 2. Friend Balances Check:
        assert len(dash["friendBalances"]) == len(friends_balances)
        assert dash["friendBalances"][0]["friendId"] == str(u_friend)
        assert dash["friendBalances"][0]["netBalance"] == friends_balances[0]["netBalance"]
        assert dash["friendBalances"][0]["friendName"] == "Best Friend"

        # 3. Group Balances Check:
        assert len(group_balances) == 1
        assert group_balances[0]["from_user_id"] == str(u_member)
        assert group_balances[0]["to_user_id"] == str(u_main)
        assert group_balances[0]["amount"] == 30.0

        dash_gb = dash["groupBalances"][0]
        assert dash_gb["groupId"] == str(group.id)
        assert dash_gb["userNetBalance"] == 30.0
        assert dash_gb["memberCount"] == 2

        # 4. Group Share Check:
        assert dash["groupShareTotal"] == 30.0
        assert len(dash["groupUserSplits"]) == 1
        assert dash["groupUserSplits"][0]["amount"] == 30.0

        # 5. Unified Spending: personal (50.0) + groupShare (30.0) = 80.0
        assert dash["unifiedTotal"] == 80.0
        assert dash["unifiedPrevMonthTotal"] == 30.0
        assert dash["monthOverMonthPct"] == 167

        # 6. Debt Aggregates:
        # Main is owed: 20 (friend) + 30 (group) = 50.0
        assert dash["netOwed"] == 50.0
        assert dash["netOwes"] == 0.0
        assert dash["netBalance"] == 50.0
        assert dash["unsettledCount"] == 2

        # 7. Activity Feed:
        assert len(dash["recentActivity"]) >= 2
        activity_types = {a["type"] for a in dash["recentActivity"]}
        assert "personal_expense" in activity_types
        assert "group_expense" in activity_types

    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)





