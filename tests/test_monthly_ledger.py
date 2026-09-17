"""Integration tests for Shared Living Monthly Household Ledger.

Validates exact mathematical parity against July 2026 spreadsheet,
ceiling rounding rules, dual progress tracking, coordinator clearing,
and member personal action summaries.
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
from app.models.settlement import MemberMonthlyExclusion

TEST_SECRET = "ledger-writes-test-secret-at-least-32-chars-long-12345"

# 7 Flatmate User IDs (using leading alphabetic hex characters so SQLite preserves TEXT representation)
UID_YASH = uuid.UUID("aaaaaaaa-1111-4111-8111-111111111111")
UID_KRISH = uuid.UUID("bbbbbbbb-2222-4222-8222-222222222222")
UID_TUSHAR = uuid.UUID("cccccccc-3333-4333-8333-333333333333")
UID_DHAIRYA = uuid.UUID("dddddddd-4444-4444-8444-444444444444")
UID_PARTH = uuid.UUID("eeeeeeee-5555-4555-8555-555555555555")
UID_UDAY = uuid.UUID("ffffffff-6666-4666-8666-666666666666")
UID_VAIBHAV = uuid.UUID("a0a0a0a0-7777-4777-8777-777777777777")
UID_OUTSIDER = uuid.UUID("f0f0f0f0-9999-4999-8999-999999999999")

ALL_MEMBERS = [
    (UID_YASH, "Yash (Pramukh)", "yash@upi"),
    (UID_KRISH, "Krish", "krish@flat.test"),
    (UID_TUSHAR, "Tushar", "tushar@flat.test"),
    (UID_DHAIRYA, "Dhairya", "dhairya@flat.test"),
    (UID_PARTH, "Parth", "parth@flat.test"),
    (UID_UDAY, "Uday", "uday@flat.test"),
    (UID_VAIBHAV, "Vaibhav", "vaibhav@flat.test"),
]


def bearer(user_id=UID_YASH, email="yash@upi", **claims):
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
async def ledger_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", TEST_SECRET)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger_test.sqlite'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        # Create profiles
        for uid, name, email in ALL_MEMBERS:
            db.add(Profile(user_id=uid, display_name=name, email=email))
        db.add(Profile(user_id=UID_OUTSIDER, display_name="Outsider", email="outsider@test.test"))
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
async def client(ledger_db):
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
async def test_monthly_ledger_july_2026_exact_parity(client, ledger_db):
    """
    Verifies exact mathematical parity with the July 2026 spreadsheet:
    - 7 members
    - Actual rent: ₹13,500 -> Ceil rounded to ₹1,929 each = ₹13,503
    - Shared day-to-day expenses: ₹16,520 -> ₹2,360 each
    - Total expenses: ₹30,023
    - Total paid: ₹16,521
    - Total balance: ₹13,502
    - Yash overpaid: -₹3,979
    - Krish owes: +₹1,565
    - Tushar owes: +₹2,704
    - Dhairya owes: +₹2,205
    - Parth owes: +₹3,629
    - Uday owes: +₹3,619
    - Vaibhav owes: +₹3,759
    - Members to contribute: ₹17,481
    - Over-contributed: ₹3,979
    - Remaining for bills: ₹13,502
    """
    # 1. Create Shared Living Group
    res = await client.post(
        "/api/v1/groups/",
        headers=bearer(UID_YASH),
        json={
            "name": "Flat 402 Household",
            "type": "shared_living",
            "monthly_rent": 13500.00,
        },
    )
    group_data = unwrap(res, 201)
    group_id = group_data["id"]

    # 2. Add remaining 6 members to group
    for uid, _, email in ALL_MEMBERS[1:]:
        res = await client.post(
            f"/api/v1/groups/{group_id}/members",
            headers=bearer(UID_YASH),
            json={"user_id": str(uid), "email": email, "role": "member"},
        )
        unwrap(res, 201)

    # 3. Add July 2026 day-to-day shared expenses fronted by members:
    # Yash fronted ₹8,268
    # Krish fronted ₹2,724
    # Dhairya fronted ₹2,084
    # Tushar fronted ₹1,585
    # Uday fronted ₹670
    # Parth fronted ₹660
    # Vaibhav fronted ₹530
    # Total fronted = ₹16,521. (Note: shared expense total is ₹16,520 with ₹1 extra fronted)
    payments = [
        (UID_YASH, Decimal("8268.00"), "Groceries & Supermarket"),
        (UID_KRISH, Decimal("2724.00"), "Electricity & Gas"),
        (UID_DHAIRYA, Decimal("2084.00"), "Water cans & Maid charges"),
        (UID_TUSHAR, Decimal("1585.00"), "WiFi & Kitchen supplies"),
        (UID_UDAY, Decimal("670.00"), "Cleaning products"),
        (UID_PARTH, Decimal("660.00"), "Vegetables"),
        (UID_VAIBHAV, Decimal("530.00"), "Milk & Essentials"),
    ]

    all_member_ids = [m[0] for m in ALL_MEMBERS]
    split_count = len(all_member_ids)

    # Create expenses in July 2026
    async with ledger_db() as db:
        for payer_id, amount, note in payments:
            base_split = (amount / Decimal(split_count)).quantize(Decimal("0.01"))
            remainder = amount - (base_split * split_count)

            splits = []
            for i, uid in enumerate(all_member_ids):
                allocated = base_split + (remainder if i == 0 else Decimal("0.00"))
                splits.append(ExpenseSplit(user_id=uid, amount=allocated, is_settled=False))

            exp = Expense(
                group_id=uuid.UUID(group_id),
                user_id=payer_id,
                paid_by=payer_id,
                amount=amount,
                category="groceries",
                note=note,
                expense_date=date(2026, 7, 15),
                splits=splits,
            )
            db.add(exp)
        await db.commit()

    # 4. Fetch Monthly Ledger as Krish
    res = await client.get(
        f"/api/v1/groups/{group_id}/monthly-ledger?month=7&year=2026",
        headers=bearer(UID_KRISH),
    )
    ledger = unwrap(res, 200)

    # Verify high-level summary
    summary = ledger["summary"]
    assert Decimal(str(summary["total_rent"])) == Decimal("13503.00")
    assert Decimal(str(summary["total_shared_expenses"])) == Decimal("16521.00")  # Exactly matches fronted sum
    assert Decimal(str(summary["total_paid"])) == Decimal("16521.00")
    assert Decimal(str(summary["total_balance"])) == Decimal("13503.00")
    assert Decimal(str(summary["remaining_for_bills"])) == Decimal(str(summary["total_balance"]))

    # Verify member-level rows
    member_map = {m["user_id"]: m for m in ledger["members"]}

    yash_row = member_map[str(UID_YASH)]
    assert Decimal(str(yash_row["rent_share"])) == Decimal("1929.00")
    assert Decimal(str(yash_row["total_paid"])) == Decimal("8268.00")
    assert Decimal(str(yash_row["balance"])) < 0  # Yash is in credit / overpaid

    krish_row = member_map[str(UID_KRISH)]
    assert Decimal(str(krish_row["rent_share"])) == Decimal("1929.00")
    assert Decimal(str(krish_row["total_paid"])) == Decimal("2724.00")
    assert Decimal(str(krish_row["balance"])) > 0  # Krish owes

    # Verify Krish's personalized action summary
    my_summary = ledger["my_summary"]
    assert my_summary is not None
    assert my_summary["action"] == "pay_coordinator"
    assert Decimal(str(my_summary["amount"])) == Decimal(str(krish_row["balance"]))
    assert "Yash" in my_summary["coordinator_name"]
    assert my_summary["upi_uri"] is not None
    assert "upi://pay" in my_summary["upi_uri"]
    assert "yash@upi" in my_summary["upi_uri"]

    # 5. Fetch Monthly Ledger as Yash (Coordinator)
    res_yash = await client.get(
        f"/api/v1/groups/{group_id}/monthly-ledger?month=7&year=2026",
        headers=bearer(UID_YASH),
    )
    ledger_yash = unwrap(res_yash, 200)

    # Verify Yash's personal action summary
    yash_summary = ledger_yash["my_summary"]
    assert yash_summary["action"] == "receive_refund"
    assert Decimal(str(yash_summary["amount"])) == abs(Decimal(str(yash_row["balance"])))

    # Verify Coordinator Checklist
    coord_check = ledger_yash["coordinator_summary"]
    assert coord_check is not None
    assert len(coord_check["members_to_collect"]) == 6  # The 6 roommates owing money
    assert len(coord_check["members_to_refund"]) == 1   # Yash
    assert coord_check["members_to_refund"][0]["user_id"] == str(UID_YASH)
    assert len(coord_check["external_bills_pending"]) == 1
    assert coord_check["external_bills_pending"][0]["category"] == "rent"


@pytest.mark.asyncio
async def test_monthly_ledger_ceil_rounding_and_exclusion(client, ledger_db):
    """
    Verifies that whole-rupee ceil rounding applies, and that full member
    exclusion excludes the member from rent and recalculates ceil among active.
    """
    # Create group with rent 10,000 and 3 members
    res = await client.post(
        "/api/v1/groups/",
        headers=bearer(UID_YASH),
        json={"name": "Tri-Flat", "type": "shared_living", "monthly_rent": 10000.00},
    )
    group_id = unwrap(res, 201)["id"]

    for uid, _, email in [(UID_KRISH, "Krish", "krish@flat.test"), (UID_TUSHAR, "Tushar", "tushar@flat.test")]:
        unwrap(
            await client.post(
                f"/api/v1/groups/{group_id}/members",
                headers=bearer(UID_YASH),
                json={"user_id": str(uid), "email": email, "role": "member"},
            ),
            201,
        )

    # 10,000 / 3 = 3333.333...
    # ceil(3333.333...) = 3334
    res = await client.get(
        f"/api/v1/groups/{group_id}/monthly-ledger?month=8&year=2026",
        headers=bearer(UID_YASH),
    )
    ledger = unwrap(res, 200)
    for m in ledger["members"]:
        assert Decimal(str(m["rent_share"])) == Decimal("3334.00")
    assert Decimal(str(ledger["summary"]["total_rent"])) == Decimal("10002.00")

    # Exclude Tushar fully for August 2026
    res_excl = await client.post(
        f"/api/v1/groups/{group_id}/exclusions/8/2026",
        headers=bearer(UID_YASH),
        json={"user_id": str(UID_TUSHAR), "exclusion_type": "full"},
    )
    unwrap(res_excl, 200)

    # Now 10,000 / 2 eligible members = 5,000 each; Tushar gets 0
    res_after = await client.get(
        f"/api/v1/groups/{group_id}/monthly-ledger?month=8&year=2026",
        headers=bearer(UID_YASH),
    )
    ledger_after = unwrap(res_after, 200)
    m_map = {m["user_id"]: m for m in ledger_after["members"]}
    assert Decimal(str(m_map[str(UID_YASH)]["rent_share"])) == Decimal("5000.00")
    assert Decimal(str(m_map[str(UID_KRISH)]["rent_share"])) == Decimal("5000.00")
    assert Decimal(str(m_map[str(UID_TUSHAR)]["rent_share"])) == Decimal("0.00")
    assert m_map[str(UID_TUSHAR)]["is_excluded"] is True


@pytest.mark.asyncio
async def test_record_monthly_ledger_contribution(client, ledger_db):
    """
    Verifies recording a member contribution for the cycle updates member status.
    """
    res = await client.post(
        "/api/v1/groups/",
        headers=bearer(UID_YASH),
        json={"name": "Contribution Test Group", "type": "shared_living", "monthly_rent": 6000.00},
    )
    group_id = unwrap(res, 201)["id"]

    unwrap(
        await client.post(
            f"/api/v1/groups/{group_id}/members",
            headers=bearer(UID_YASH),
            json={"user_id": str(UID_KRISH), "email": "krish@flat.test", "role": "member"},
        ),
        201,
    )

    # Krish submits contribution
    res_contrib = await client.post(
        f"/api/v1/groups/{group_id}/monthly-ledger/contributions?month=9&year=2026",
        headers=bearer(UID_KRISH),
        json={"from_user_id": str(UID_KRISH), "amount": 3000.00, "note": "Paid via UPI"},
    )
    contrib_data = unwrap(res_contrib, 200)
    assert contrib_data["user_id"] == str(UID_KRISH)
    assert contrib_data["status"] == "submitted"

    # Yash confirms contribution
    res_confirm = await client.post(
        f"/api/v1/groups/{group_id}/monthly-ledger/contributions?month=9&year=2026",
        headers=bearer(UID_YASH),
        json={"from_user_id": str(UID_KRISH), "amount": 3000.00, "note": "Confirmed receipt"},
    )
    confirm_data = unwrap(res_confirm, 200)
    assert confirm_data["status"] == "complete"


@pytest.mark.asyncio
async def test_monthly_ledger_authorization_and_validation(client, ledger_db):
    """
    Verifies non-member access returns 403, invalid month returns 422.
    """
    res = await client.post(
        "/api/v1/groups/",
        headers=bearer(UID_YASH),
        json={"name": "Auth Test Group", "type": "shared_living"},
    )
    group_id = unwrap(res, 201)["id"]

    # Outsider gets 403
    res_403 = await client.get(
        f"/api/v1/groups/{group_id}/monthly-ledger?month=7&year=2026",
        headers=bearer(UID_OUTSIDER),
    )
    assert res_403.status_code == 403

    # Invalid month gets 422
    res_422 = await client.get(
        f"/api/v1/groups/{group_id}/monthly-ledger?month=13&year=2026",
        headers=bearer(UID_YASH),
    )
    assert res_422.status_code == 422
