"""Write integration tests use real JWT verification and real SQL transactions.

The database is isolated SQLite, never the configured Supabase database.
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, text, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.category import UserCategory
from app.models.expense import Expense, ExpenseSplit
from app.models.friend import Friend
from app.models.group import Group
from app.models.profile import Profile

USER_ID = uuid.UUID("aaaaaaaa-1111-4111-8111-111111111111")
OTHER_ID = uuid.UUID("bbbbbbbb-2222-4222-8222-222222222222")
TEST_SECRET = "isolated-write-tests-secret-not-for-production-123456789"


def bearer(user_id=USER_ID, **claims):
    payload = {
        "sub": str(user_id), "aud": "authenticated", "role": "authenticated",
        "email": "writer@example.test",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        **claims,
    }
    return {"Authorization": "Bearer " + jwt.encode(payload, TEST_SECRET, algorithm="HS256")}


@pytest_asyncio.fixture
async def write_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", TEST_SECRET)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'writes.sqlite'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            Profile(user_id=USER_ID, display_name="Writer", email="writer@example.test"),
            Profile(user_id=OTHER_ID, display_name="Other", email="other@example.test"),
            Friend(user_id=USER_ID, friend_id=OTHER_ID, status="accepted"),
        ])
        await db.commit()

    async def isolated_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    try:
        yield factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest_asyncio.fixture
async def write_client(write_db):
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        yield client


def success(response, status=200):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"data", "error", "meta"}
    assert body["error"] is None
    return body["data"]


async def category(client, name="Hobbies", headers=None):
    return success(await client.post("/api/v1/categories/", json={"name": name}, headers=headers or bearer()), 201)


@pytest.mark.asyncio
async def test_profile_update(write_client, write_db):
    data = success(await write_client.patch("/api/v1/users/me", headers=bearer(), json={
        "display_name": " Renamed ", "avatar_url": "https://example.test/avatar.png",
    }))
    assert data["display_name"] == "Renamed"
    assert data["avatar_url"] == "https://example.test/avatar.png"
    assert data["email"] == "writer@example.test"
    success(await write_client.patch("/api/v1/users/me", headers=bearer(), json={"avatar_url": None}))
    async with write_db() as db:
        own = await db.scalar(select(Profile).where(Profile.user_id == USER_ID))
        other = await db.scalar(select(Profile).where(Profile.user_id == OTHER_ID))
        assert own.display_name == "Renamed" and own.avatar_url is None
        assert other.display_name == "Other"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("email", "stolen@example.test"), ("is_shadow", True), ("id", str(OTHER_ID)),
    ("user_id", str(OTHER_ID)), ("shadow_created_by", str(OTHER_ID)),
    ("created_at", "2020-01-01T00:00:00Z"),
])
async def test_profile_rejects_noneditable_fields(write_client, field, value):
    response = await write_client.patch("/api/v1/users/me", headers=bearer(), json={"display_name": "Bad", field: value})
    assert response.status_code == 422
    assert response.json()["error"] and response.json()["data"] is None
    assert success(await write_client.get("/api/v1/users/me", headers=bearer()))["display_name"] == "Writer"


@pytest.mark.asyncio
async def test_category_create_rename_delete_preserves_referenced_expenses(write_client, write_db):
    row = await category(write_client, "Weekend Fun")
    assert row["slug"] == "weekend_fun" and row["user_id"] == str(USER_ID)
    expense_id = uuid.uuid4()
    async with write_db() as db:
        db.add(Expense(id=expense_id, user_id=USER_ID, amount=10, category=row["slug"]))
        await db.commit()
    renamed = success(await write_client.patch(f"/api/v1/categories/{row['id']}", headers=bearer(), json={"name": "Leisure", "color_index": 4}))
    assert renamed["name"] == "Leisure" and renamed["slug"] == "weekend_fun"
    response = await write_client.delete(f"/api/v1/categories/{row['id']}", headers=bearer())
    success(response)
    assert response.json()["meta"] == {"referencing_expenses": 1, "expense_categories_preserved": True}
    async with write_db() as db:
        assert await db.get(UserCategory, uuid.UUID(row["id"])) is None
        assert (await db.get(Expense, expense_id)).category == "weekend_fun"


@pytest.mark.asyncio
async def test_category_delete_unreferenced(write_client):
    row = await category(write_client)
    response = await write_client.delete(f"/api/v1/categories/{row['id']}", headers=bearer())
    success(response)
    assert response.json()["meta"]["referencing_expenses"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method,payload", [("patch", {"name": "Stolen"}), ("delete", None)])
async def test_cannot_change_another_users_category(write_client, method, payload):
    row = await category(write_client, headers=bearer(OTHER_ID))
    response = await write_client.request(method, f"/api/v1/categories/{row['id']}", headers=bearer(), **({"json": payload} if payload else {}))
    assert response.status_code == 404
    assert response.json()["error"] == "Category not found"
    rows = success(await write_client.get("/api/v1/categories/", headers=bearer(OTHER_ID)))
    assert rows[0]["name"] == "Hobbies"


@pytest.mark.asyncio
async def test_category_validation_and_duplicates(write_client):
    await category(write_client)
    assert (await write_client.post("/api/v1/categories/", headers=bearer(), json={"name": "Hobbies"})).status_code == 409
    for payload in [{"name": "food"}, {"name": "!!"}, {"name": "x"}, {"name": "Hobby", "user_id": str(OTHER_ID)}]:
        response = await write_client.post("/api/v1/categories/", headers=bearer(), json=payload)
        assert response.status_code == 422, response.text
    row = (success(await write_client.get("/api/v1/categories/", headers=bearer())))[0]
    for payload in [{"slug": "changed"}, {"user_id": str(OTHER_ID)}, {"name": None}, {"color_index": -1}]:
        assert (await write_client.patch(f"/api/v1/categories/{row['id']}", headers=bearer(), json=payload)).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", [
    ("patch", "/api/v1/users/me", {"display_name": "Hello"}),
    ("post", "/api/v1/categories/", {"name": "Hello"}),
    ("patch", f"/api/v1/categories/{OTHER_ID}", {"name": "Hello"}),
    ("delete", f"/api/v1/categories/{OTHER_ID}", None),
])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid.token.signature"}])
async def test_user_category_writes_require_verified_jwt(write_client, method, path, payload, headers):
    response = await write_client.request(method, path, headers=headers, **({"json": payload} if payload else {}))
    assert response.status_code == 401
    assert response.json()["data"] is None and response.json()["error"]


@pytest.mark.asyncio
async def test_rejects_expired_wrong_audience_and_forged_jwt(write_client):
    for headers in [
        bearer(exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
        bearer(aud="wrong"),
        {"Authorization": "Bearer " + jwt.encode({"sub": str(USER_ID), "aud": "authenticated"}, "different-secret-that-is-long-enough-12345", algorithm="HS256")},
    ]:
        assert (await write_client.patch("/api/v1/users/me", headers=headers, json={"display_name": "Bad"})).status_code == 401


def expense_payload(**changes):
    return {"amount": "100.00", "category": "food", "note": "Lunch", "expense_date": "2026-09-10", **changes}


def allocation(own="50.00", other="50.00"):
    return [{"user_id": str(USER_ID), "amount": own}, {"user_id": str(OTHER_ID), "amount": other}]


@pytest.mark.asyncio
async def test_personal_expense_create_update_delete(write_client, write_db):
    created = success(await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload(group_id=str(uuid.uuid4()))), 201)
    assert created["group_id"] is None and created["user_id"] == str(USER_ID)
    assert created["amount"] == 100 and created["expense_splits"] == []
    updated = success(await write_client.patch(f"/api/v1/expenses/{created['id']}", headers=bearer(), json={"amount": "12.34", "note": None, "expense_date": "2026-09-09"}))
    assert updated["amount"] == 12.34 and updated["note"] is None and updated["edited_at"]
    success(await write_client.delete(f"/api/v1/expenses/{created['id']}", headers=bearer()))
    async with write_db() as db:
        assert await db.get(Expense, uuid.UUID(created["id"])) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,grouped", [(OTHER_ID, False), (USER_ID, True)])
@pytest.mark.parametrize("method", ["patch", "delete"])
async def test_personal_expense_rejects_other_owner_and_group(write_client, write_db, owner, grouped, method):
    expense_id = uuid.uuid4()
    async with write_db() as db:
        group = Group(name="Untouchable", created_by=USER_ID) if grouped else None
        if group:
            db.add(group)
            await db.flush()
        db.add(Expense(id=expense_id, user_id=owner, group_id=group.id if group else None, amount=25, category="food"))
        await db.commit()
    response = await write_client.request(method, f"/api/v1/expenses/{expense_id}", headers=bearer(), **({"json": {"amount": 1}} if method == "patch" else {}))
    assert response.status_code == 404 and response.json()["error"]
    async with write_db() as db:
        assert (await db.get(Expense, expense_id)).amount == 25


@pytest.mark.asyncio
async def test_split_expense_friend_balances_create_edit_delete(write_client, write_db):
    created = success(await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload(splits=allocation())), 201)
    assert len(created["expense_splits"]) == 2
    async def balance(user, friend):
        rows = success(await write_client.get("/api/v1/friends/balances", headers=bearer(user)))
        return next((r["netBalance"] for r in rows if r["friendId"] == str(friend)), 0)
    assert await balance(USER_ID, OTHER_ID) == 50
    assert await balance(OTHER_ID, USER_ID) == -50
    # Shared non-group expenses continue to live in the existing friend feed.
    assert success(await write_client.get("/api/v1/expenses/personal", headers=bearer())) == []
    feed = success(await write_client.get(f"/api/v1/friends/{OTHER_ID}/expenses", headers=bearer()))
    assert feed[0]["id"] == created["id"]
    patched = success(await write_client.patch(f"/api/v1/expenses/{created['id']}", headers=bearer(), json={"amount": 120, "splits": allocation("60", "60")}))
    assert {s["id"] for s in patched["expense_splits"]} == {s["id"] for s in created["expense_splits"]}
    assert await balance(USER_ID, OTHER_ID) == 60
    assert await balance(OTHER_ID, USER_ID) == -60
    success(await write_client.delete(f"/api/v1/expenses/{created['id']}", headers=bearer()))
    assert await balance(USER_ID, OTHER_ID) == await balance(OTHER_ID, USER_ID) == 0
    async with write_db() as db:
        assert await db.scalar(select(func.count()).select_from(ExpenseSplit)) == 0


@pytest.mark.asyncio
async def test_self_split_and_other_payer(write_client):
    for splits, payer in [([{"user_id": str(USER_ID), "amount": 100}], USER_ID), (allocation(), OTHER_ID)]:
        created = success(await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload(splits=splits, paid_by=str(payer))), 201)
        patched = success(await write_client.patch(f"/api/v1/expenses/{created['id']}", headers=bearer(), json={"note": "Preserve splits"}))
        assert patched["expense_splits"] == created["expense_splits"]
        success(await write_client.delete(f"/api/v1/expenses/{created['id']}", headers=bearer()))


@pytest.mark.asyncio
async def test_personal_category_ownership_and_deleted_reference(write_client):
    other = await category(write_client, "Private", bearer(OTHER_ID))
    response = await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload(category=other["slug"]))
    assert response.status_code == 422
    own = await category(write_client, "My Private")
    created = success(await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload(category=own["slug"])), 201)
    assert (await write_client.patch(f"/api/v1/expenses/{created['id']}", headers=bearer(), json={"category": other["slug"]})).status_code == 422
    success(await write_client.delete(f"/api/v1/categories/{own['id']}", headers=bearer()))
    # Historical category must not prevent editing an existing expense after deletion.
    patched = success(await write_client.patch(f"/api/v1/expenses/{created['id']}", headers=bearer(), json={"category": own["slug"], "note": "Historical"}))
    assert patched["category"] == own["slug"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"amount": 0}, {"amount": -1}, {"amount": "1.001"}, {"amount": "10000000000.00"},
    {"amount": "NaN"}, {"expense_date": "2026-02-30"}, {"expense_date": "2026-01-01T00:00:00Z"},
    {"expense_date": 0}, {"note": "x" * 2001}, {"category": "unknown"},
    {"user_id": str(OTHER_ID)}, {"status": "reimbursed"},
    {"splits": allocation("50", "49")},
    {"splits": [{"user_id": str(USER_ID), "amount": 50}] * 2},
    {"splits": [{"user_id": str(USER_ID), "amount": -1}]},
    {"splits": [{"user_id": str(USER_ID), "amount": 100, "is_settled": True}]},
])
async def test_personal_expense_validation(write_client, change):
    response = await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload(**change))
    assert response.status_code == 422, response.text
    assert response.json()["error"] and response.json()["data"] is None


@pytest.mark.asyncio
async def test_personal_patch_rejects_identity_and_null_fields(write_client):
    created = success(await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload()), 201)
    for patch in [{"user_id": str(OTHER_ID)}, {"group_id": str(uuid.uuid4())}, {"id": str(uuid.uuid4())}, {"status": "reimbursed"}, {"amount": None}, {"category": None}, {"expense_date": None}, {"splits": None}]:
        response = await write_client.patch(f"/api/v1/expenses/{created['id']}", headers=bearer(), json=patch)
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_split_rejects_unrelated_user_without_partial_insert(write_client, write_db):
    stranger = uuid.uuid4()
    response = await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload(splits=[
        {"user_id": str(USER_ID), "amount": 50}, {"user_id": str(stranger), "amount": 50},
    ]))
    assert response.status_code == 403
    async with write_db() as db:
        assert await db.scalar(select(func.count()).select_from(Expense)) == 0


@pytest.mark.asyncio
async def test_settled_splits_preserved_and_inconsistent_edits_rejected(write_client, write_db):
    created = success(await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload(splits=allocation())), 201)
    async with write_db() as db:
        split = await db.scalar(select(ExpenseSplit).where(ExpenseSplit.expense_id == uuid.UUID(created["id"]), ExpenseSplit.user_id == OTHER_ID))
        split.is_settled = True
        await db.commit()
    patched = success(await write_client.patch(f"/api/v1/expenses/{created['id']}", headers=bearer(), json={"note": "Only metadata", "splits": allocation()}))
    assert next(s for s in patched["expense_splits"] if s["user_id"] == str(OTHER_ID))["is_settled"] is True
    assert (await write_client.patch(f"/api/v1/expenses/{created['id']}", headers=bearer(), json={"amount": 120})).status_code == 422
    assert (await write_client.patch(f"/api/v1/expenses/{created['id']}", headers=bearer(), json={"amount": 120, "splits": allocation("60", "60")})).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
async def test_expense_split_transaction_rolls_back_database_failure(write_client, write_db, operation):
    existing = None
    if operation != "create":
        existing = success(await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload(splits=allocation())), 201)
    async with write_db() as db:
        # Fail after earlier statements have run, proving the entire transaction rolls back.
        trigger = {
            "create": "CREATE TRIGGER fail_split BEFORE INSERT ON expense_splits BEGIN SELECT RAISE(ABORT, 'injected split insert failure'); END",
            "update": "CREATE TRIGGER fail_split BEFORE UPDATE ON expense_splits BEGIN SELECT RAISE(ABORT, 'injected split update failure'); END",
            "delete": "CREATE TRIGGER fail_expense BEFORE DELETE ON expenses BEGIN SELECT RAISE(ABORT, 'injected expense delete failure'); END",
        }[operation]
        await db.execute(text(trigger))
        await db.commit()
    if operation == "create":
        response = await write_client.post("/api/v1/expenses/personal", headers=bearer(), json=expense_payload(splits=allocation()))
    elif operation == "update":
        response = await write_client.patch(f"/api/v1/expenses/{existing['id']}", headers=bearer(), json={"amount": 120, "splits": allocation("60", "60")})
    else:
        response = await write_client.delete(f"/api/v1/expenses/{existing['id']}", headers=bearer())
    assert response.status_code == 500 and response.json()["data"] is None
    async with write_db() as db:
        expenses = (await db.execute(select(Expense))).scalars().all()
        splits = (await db.execute(select(ExpenseSplit))).scalars().all()
        if operation == "create":
            assert expenses == splits == []
        else:
            assert len(expenses) == 1 and expenses[0].amount == 100
            assert len(splits) == 2 and all(s.amount == 50 for s in splits)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", [
    ("post", "/api/v1/expenses/personal", expense_payload()),
    ("patch", f"/api/v1/expenses/{OTHER_ID}", {"note": "Hello"}),
    ("delete", f"/api/v1/expenses/{OTHER_ID}", None),
])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid.token.signature"}])
async def test_personal_writes_require_verified_jwt(write_client, method, path, payload, headers):
    response = await write_client.request(method, path, headers=headers, **({"json": payload} if payload else {}))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_write_openapi_contracts(write_client):
    schema = (await write_client.get("/api/v1/openapi.json")).json()
    for method, path in [
        ("patch", "/api/v1/users/me"), ("post", "/api/v1/categories/"),
        ("patch", "/api/v1/categories/{category_id}"), ("delete", "/api/v1/categories/{category_id}"),
        ("post", "/api/v1/expenses/personal"), ("patch", "/api/v1/expenses/{expense_id}"),
        ("delete", "/api/v1/expenses/{expense_id}"),
    ]:
        endpoint = schema["paths"][path][method]
        assert endpoint["security"] == [{"HTTPBearer": []}]
        assert endpoint["description"]
        response = endpoint["responses"].get("200") or endpoint["responses"]["201"]
        assert "ResponseEnvelope" in response["content"]["application/json"]["schema"]["$ref"]
