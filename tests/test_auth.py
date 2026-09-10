import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.profile import Profile
from app.models.user_credential import UserCredential

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
async def client(test_session: AsyncSession):
    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_signup_success(client: AsyncClient):
    payload = {
        "email": "alice@example.com",
        "password": "strongpassword123",
        "display_name": "Alice Smith",
    }
    res = await client.post("/api/v1/auth/signup", json=payload)
    assert res.status_code == 201
    body = res.json()
    assert body["error"] is None
    assert "access_token" in body["data"]
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["user"]["email"] == "alice@example.com"
    assert body["data"]["user"]["display_name"] == "Alice Smith"


@pytest.mark.asyncio
async def test_signup_duplicate_email_fails(client: AsyncClient):
    payload = {
        "email": "duplicate@example.com",
        "password": "password123",
        "display_name": "First User",
    }
    res1 = await client.post("/api/v1/auth/signup", json=payload)
    assert res1.status_code == 201

    res2 = await client.post("/api/v1/auth/signup", json=payload)
    assert res2.status_code == 400
    assert "already exists" in res2.json()["error"]


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    # Register first
    signup_payload = {
        "email": "bob@example.com",
        "password": "mypassword123",
        "display_name": "Bob",
    }
    await client.post("/api/v1/auth/signup", json=signup_payload)

    # Login
    login_payload = {
        "email": "bob@example.com",
        "password": "mypassword123",
    }
    res = await client.post("/api/v1/auth/login", json=login_payload)
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    assert "access_token" in body["data"]
    assert body["data"]["user"]["email"] == "bob@example.com"


@pytest.mark.asyncio
async def test_login_wrong_password_fails(client: AsyncClient):
    signup_payload = {
        "email": "charlie@example.com",
        "password": "correctpassword",
    }
    await client.post("/api/v1/auth/signup", json=signup_payload)

    login_payload = {
        "email": "charlie@example.com",
        "password": "wrongpassword",
    }
    res = await client.post("/api/v1/auth/login", json=login_payload)
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["error"]


@pytest.mark.asyncio
async def test_login_nonexistent_user_fails(client: AsyncClient):
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "password"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_jwt_token_accesses_protected_endpoint(client: AsyncClient):
    signup_payload = {
        "email": "dave@example.com",
        "password": "davepassword",
        "display_name": "Dave",
    }
    signup_res = await client.post("/api/v1/auth/signup", json=signup_payload)
    token = signup_res.json()["data"]["access_token"]

    # Use token to fetch own profile from protected /users/me
    headers = {"Authorization": f"Bearer {token}"}
    me_res = await client.get("/api/v1/users/me", headers=headers)
    assert me_res.status_code == 200
    assert me_res.json()["data"]["email"] == "dave@example.com"
    assert me_res.json()["data"]["display_name"] == "Dave"


@pytest.mark.asyncio
async def test_delete_account(client: AsyncClient):
    signup_payload = {
        "email": "eve@example.com",
        "password": "evepassword",
        "display_name": "Eve",
    }
    signup_res = await client.post("/api/v1/auth/signup", json=signup_payload)
    token = signup_res.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Delete account
    del_res = await client.delete("/api/v1/auth/account", headers=headers)
    assert del_res.status_code == 200

    # Trying to login after deletion fails
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "eve@example.com", "password": "evepassword"},
    )
    assert login_res.status_code == 401
