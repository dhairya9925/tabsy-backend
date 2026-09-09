"""Integration tests for Phase 3.3 Groups & Members write operations.

Tests use real JWT tokens, SQLite isolation, and verify RBAC and financial integrity.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.expense import Expense, ExpenseSplit
from app.models.group import Group, GroupMember
from app.models.profile import Profile

USER_A = uuid.UUID("aaaaaaaa-1111-4111-8111-111111111111")
USER_B = uuid.UUID("bbbbbbbb-2222-4222-8222-222222222222")
USER_C = uuid.UUID("cccccccc-3333-4333-8333-333333333333")
TEST_SECRET = "groups-writes-test-secret-at-least-32-chars-long-12345"


def bearer(user_id=USER_A, email="user@example.test", **claims):
    payload = {
        "sub": str(user_id),
        "aud": "authenticated",
        "role": "authenticated",
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        **claims,
    }
    return {"Authorization": "Bearer " + jwt.encode(payload, TEST_SECRET, algorithm="HS256")}


@pytest_asyncio.fixture
async def groups_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", TEST_SECRET)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'groups_writes.sqlite'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            Profile(user_id=USER_A, display_name="Alice Admin", email="alice@example.test"),
            Profile(user_id=USER_B, display_name="Bob Member", email="bob@example.test"),
            Profile(user_id=USER_C, display_name="Charlie Member", email="charlie@example.test"),
        ])
        await db.commit()

    async def override_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        yield factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest_asyncio.fixture
async def client(groups_db):
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as c:
        yield c


def unwrap(response, expected_status=200):
    assert response.status_code == expected_status, f"Expected {expected_status}, got {response.status_code}: {response.text}"
    body = response.json()
    assert set(body.keys()) == {"data", "error", "meta"}
    assert body["error"] is None
    return body["data"]


@pytest.mark.asyncio
async def test_create_group_adds_creator_as_admin(client, groups_db):
    payload = {
        "name": "Apartment 4B",
        "description": "Roommates",
        "type": "roommates",
        "monthly_rent": "1200.00",
    }
    data = unwrap(await client.post("/api/v1/groups/", headers=bearer(USER_A), json=payload), 201)
    assert data["name"] == "Apartment 4B"
    assert data["created_by"] == str(USER_A)
    group_id = uuid.UUID(data["id"])

    async with groups_db() as db:
        members = (await db.execute(select(GroupMember).where(GroupMember.group_id == group_id))).scalars().all()
        assert len(members) == 1
        assert members[0].user_id == USER_A
        assert members[0].role == "admin"

        # Verify initial rent expense created
        rent = (await db.execute(select(Expense).where(Expense.group_id == group_id, Expense.category == "rent"))).scalar_one_or_none()
        assert rent is not None
        assert rent.amount == 1200.00


@pytest.mark.asyncio
async def test_update_group_by_admin_and_rent_sync(client, groups_db):
    created = unwrap(await client.post("/api/v1/groups/", headers=bearer(USER_A), json={"name": "Initial"}), 201)
    group_id = created["id"]

    # Admin updates name and adds monthly rent
    updated = unwrap(await client.patch(
        f"/api/v1/groups/{group_id}",
        headers=bearer(USER_A),
        json={"name": "Renamed", "monthly_rent": "600.00"},
    ))
    assert updated["name"] == "Renamed"
    assert updated["monthly_rent"] == 600.00

    # Add second member to verify rent split calculation
    unwrap(await client.post(
        f"/api/v1/groups/{group_id}/members",
        headers=bearer(USER_A),
        json={"email": "bob@example.test"},
    ), 201)

    async with groups_db() as db:
        rent = (await db.execute(select(Expense).where(Expense.group_id == uuid.UUID(group_id), Expense.category == "rent"))).scalar_one()
        assert rent.amount == 600.00
        splits = (await db.execute(select(ExpenseSplit).where(ExpenseSplit.expense_id == rent.id))).scalars().all()
        assert len(splits) == 2
        assert all(s.amount == 300.00 for s in splits)

    # Set rent to 0 removes the automated expense
    unwrap(await client.patch(f"/api/v1/groups/{group_id}", headers=bearer(USER_A), json={"monthly_rent": "0.00"}))
    async with groups_db() as db:
        rent = (await db.execute(select(Expense).where(Expense.group_id == uuid.UUID(group_id), Expense.category == "rent"))).scalar_one_or_none()
        assert rent is None


@pytest.mark.asyncio
async def test_update_group_rejects_non_admin(client):
    created = unwrap(await client.post("/api/v1/groups/", headers=bearer(USER_A), json={"name": "Secret Group"}), 201)
    group_id = created["id"]

    # Add Bob as regular member
    unwrap(await client.post(
        f"/api/v1/groups/{group_id}/members",
        headers=bearer(USER_A),
        json={"email": "bob@example.test", "role": "member"},
    ), 201)

    # Bob (regular member) tries to update settings
    res = await client.patch(f"/api/v1/groups/{group_id}", headers=bearer(USER_B), json={"name": "Hacked"})
    assert res.status_code == 403
    assert "Only group admins" in res.json()["error"]


@pytest.mark.asyncio
async def test_delete_group_by_admin_and_rejection_for_member(client, groups_db):
    created = unwrap(await client.post("/api/v1/groups/", headers=bearer(USER_A), json={"name": "To Delete"}), 201)
    group_id = created["id"]

    # Add Bob as member
    unwrap(await client.post(f"/api/v1/groups/{group_id}/members", headers=bearer(USER_A), json={"email": "bob@example.test"}), 201)

    # Bob tries to delete group -> 403
    res = await client.delete(f"/api/v1/groups/{group_id}", headers=bearer(USER_B))
    assert res.status_code == 403

    # Alice (admin) deletes group -> 200
    unwrap(await client.delete(f"/api/v1/groups/{group_id}", headers=bearer(USER_A)))

    async with groups_db() as db:
        assert await db.get(Group, uuid.UUID(group_id)) is None
        members = (await db.execute(select(GroupMember).where(GroupMember.group_id == uuid.UUID(group_id)))).scalars().all()
        assert len(members) == 0


@pytest.mark.asyncio
async def test_add_member_by_email_and_user_id_and_rejections(client):
    created = unwrap(await client.post("/api/v1/groups/", headers=bearer(USER_A), json={"name": "Member Test"}), 201)
    group_id = created["id"]

    # Add Bob by email
    member_bob = unwrap(await client.post(
        f"/api/v1/groups/{group_id}/members",
        headers=bearer(USER_A),
        json={"email": "bob@example.test"},
    ), 201)
    assert member_bob["user_id"] == str(USER_B)
    assert member_bob["role"] == "member"
    assert member_bob["profile"]["email"] == "bob@example.test"

    # Adding Bob again returns 409 Conflict
    res_dup = await client.post(
        f"/api/v1/groups/{group_id}/members",
        headers=bearer(USER_A),
        json={"email": "bob@example.test"},
    )
    assert res_dup.status_code == 409

    # Bob (member) attempts to add Charlie -> 403
    res_bob_add = await client.post(
        f"/api/v1/groups/{group_id}/members",
        headers=bearer(USER_B),
        json={"email": "charlie@example.test"},
    )
    assert res_bob_add.status_code == 403

    # Unknown email -> 404
    res_unknown = await client.post(
        f"/api/v1/groups/{group_id}/members",
        headers=bearer(USER_A),
        json={"email": "nonexistent@example.test"},
    )
    assert res_unknown.status_code == 404

    # Add Charlie by user_id
    member_charlie = unwrap(await client.post(
        f"/api/v1/groups/{group_id}/members",
        headers=bearer(USER_A),
        json={"user_id": str(USER_C)},
    ), 201)
    assert member_charlie["user_id"] == str(USER_C)


@pytest.mark.asyncio
async def test_join_group_via_invite(client):
    created = unwrap(await client.post("/api/v1/groups/", headers=bearer(USER_A), json={"name": "Invite Group"}), 201)
    group_id = created["id"]

    # Charlie joins via invite endpoint
    joined = unwrap(await client.post(f"/api/v1/groups/{group_id}/join", headers=bearer(USER_C)), 201)
    assert joined["user_id"] == str(USER_C)
    assert joined["role"] == "member"

    # Charlie joins again -> 409
    res_dup = await client.post(f"/api/v1/groups/{group_id}/join", headers=bearer(USER_C))
    assert res_dup.status_code == 409

    # Non-existent group -> 404
    res_404 = await client.post(f"/api/v1/groups/{uuid.uuid4()}/join", headers=bearer(USER_C))
    assert res_404.status_code == 404


@pytest.mark.asyncio
async def test_remove_member_and_leave_group(client, groups_db):
    created = unwrap(await client.post("/api/v1/groups/", headers=bearer(USER_A), json={"name": "Removal Group"}), 201)
    group_id = created["id"]

    unwrap(await client.post(f"/api/v1/groups/{group_id}/members", headers=bearer(USER_A), json={"email": "bob@example.test"}), 201)
    unwrap(await client.post(f"/api/v1/groups/{group_id}/members", headers=bearer(USER_A), json={"email": "charlie@example.test"}), 201)

    # Bob (regular member) cannot remove Charlie -> 403
    res_forbid = await client.delete(f"/api/v1/groups/{group_id}/members/{USER_C}", headers=bearer(USER_B))
    assert res_forbid.status_code == 403

    # Alice (sole admin) cannot leave while Bob & Charlie remain -> 400
    res_leave_admin = await client.delete(f"/api/v1/groups/{group_id}/members/{USER_A}", headers=bearer(USER_A))
    assert res_leave_admin.status_code == 400
    assert "sole admin" in res_leave_admin.json()["error"]

    # Bob removes himself (leaves group) -> 200
    unwrap(await client.delete(f"/api/v1/groups/{group_id}/members/{USER_B}", headers=bearer(USER_B)))

    # Alice (admin) removes Charlie -> 200
    unwrap(await client.delete(f"/api/v1/groups/{group_id}/members/{USER_C}", headers=bearer(USER_A)))

    async with groups_db() as db:
        members = (await db.execute(select(GroupMember).where(GroupMember.group_id == uuid.UUID(group_id)))).scalars().all()
        assert len(members) == 1
        assert members[0].user_id == USER_A


@pytest.mark.asyncio
async def test_update_member_role_rbac_and_sole_admin_protection(client):
    created = unwrap(await client.post("/api/v1/groups/", headers=bearer(USER_A), json={"name": "Role Group"}), 201)
    group_id = created["id"]

    unwrap(await client.post(f"/api/v1/groups/{group_id}/members", headers=bearer(USER_A), json={"email": "bob@example.test"}), 201)

    # Bob attempts to promote himself -> 403 (cannot modify own role)
    res_self = await client.patch(
        f"/api/v1/groups/{group_id}/members/{USER_B}",
        headers=bearer(USER_B),
        json={"role": "admin"},
    )
    assert res_self.status_code == 403

    # Alice demoting herself -> 403 (cannot modify own role)
    res_alice_self = await client.patch(
        f"/api/v1/groups/{group_id}/members/{USER_A}",
        headers=bearer(USER_A),
        json={"role": "member"},
    )
    assert res_alice_self.status_code == 403

    # Alice promotes Bob to admin -> 200
    promoted = unwrap(await client.patch(
        f"/api/v1/groups/{group_id}/members/{USER_B}",
        headers=bearer(USER_A),
        json={"role": "admin"},
    ))
    assert promoted["role"] == "admin"

    # Bob demotes Alice to member -> 200 (allowed because Bob is also admin)
    demoted = unwrap(await client.patch(
        f"/api/v1/groups/{group_id}/members/{USER_A}",
        headers=bearer(USER_B),
        json={"role": "member"},
    ))
    assert demoted["role"] == "member"

    # Now Alice (member) tries to demote Bob -> 403
    res_unauth = await client.patch(
        f"/api/v1/groups/{group_id}/members/{USER_B}",
        headers=bearer(USER_A),
        json={"role": "member"},
    )
    assert res_unauth.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", [
    ("post", "/api/v1/groups/", {"name": "G"}),
    ("patch", f"/api/v1/groups/{uuid.uuid4()}", {"name": "G"}),
    ("delete", f"/api/v1/groups/{uuid.uuid4()}", None),
    ("post", f"/api/v1/groups/{uuid.uuid4()}/members", {"email": "x@x.com"}),
    ("post", f"/api/v1/groups/{uuid.uuid4()}/join", None),
    ("delete", f"/api/v1/groups/{uuid.uuid4()}/members/{USER_A}", None),
    ("patch", f"/api/v1/groups/{uuid.uuid4()}/members/{USER_A}", {"role": "admin"}),
])
async def test_group_writes_require_verified_jwt(client, method, path, payload):
    res_no_auth = await client.request(method, path, **({"json": payload} if payload else {}))
    assert res_no_auth.status_code == 401

    res_bad_jwt = await client.request(method, path, headers={"Authorization": "Bearer bad.token.sig"}, **({"json": payload} if payload else {}))
    assert res_bad_jwt.status_code == 401


@pytest.mark.asyncio
async def test_group_writes_openapi_contracts(client):
    schema = (await client.get("/api/v1/openapi.json")).json()
    paths = schema["paths"]
    for method, path in [
        ("post", "/api/v1/groups/"),
        ("patch", "/api/v1/groups/{id}"),
        ("delete", "/api/v1/groups/{id}"),
        ("post", "/api/v1/groups/{id}/members"),
        ("post", "/api/v1/groups/{id}/join"),
        ("delete", "/api/v1/groups/{id}/members/{user_id}"),
        ("patch", "/api/v1/groups/{id}/members/{user_id}"),
    ]:
        endpoint = paths[path][method]
        assert endpoint["security"] == [{"HTTPBearer": []}]
        assert endpoint["description"]
        resp_code = "201" if method == "post" else "200"
        content = endpoint["responses"][resp_code]["content"]["application/json"]["schema"]
        assert "ResponseEnvelope" in content.get("$ref", "") or "ResponseEnvelope" in str(content)
