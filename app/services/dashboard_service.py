from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.expense import Expense
from app.models.group import GroupMember
from app.models.profile import Profile
from app.schemas.dashboard import (
    DashboardActivityItemResponse,
    DashboardDataResponse,
    DashboardFriendBalanceResponse,
    GroupBalanceSummaryResponse,
    GroupUserSplitItemResponse,
)
from app.schemas.expense import ExpenseResponse
from app.schemas.group import GroupResponse
from app.services.expense_service import get_friend_balances, get_personal_expenses
from app.services.group_service import get_user_groups


async def get_dashboard_summary(
    db: AsyncSession, user_id: UUID
) -> DashboardDataResponse:
    """
    Consolidates the 6 sequential client-side queries in useDashboardData into a single
    server-side computation. Reuses existing service-layer logic from Phases 2.2, 2.4, 2.5.
    """
    two_dp = Decimal("0.01")
    today = date.today()
    current_month_str = today.strftime("%Y-%m")
    first_day_current_month = date(today.year, today.month, 1)
    last_day_prev_month = first_day_current_month - timedelta(days=1)
    prev_month_str = last_day_prev_month.strftime("%Y-%m")

    # ── 1. Personal Expenses ──────────────────────────────────────────
    personal_expenses_orm = await get_personal_expenses(db, user_id)

    current_month_personal = [
        e
        for e in personal_expenses_orm
        if e.expense_date and e.expense_date.strftime("%Y-%m") == current_month_str
    ]
    prev_month_personal = [
        e
        for e in personal_expenses_orm
        if e.expense_date and e.expense_date.strftime("%Y-%m") == prev_month_str
    ]

    personal_total_dec = sum(
        (Decimal(str(e.amount)) for e in current_month_personal), Decimal("0.00")
    )
    personal_prev_month_total_dec = sum(
        (Decimal(str(e.amount)) for e in prev_month_personal), Decimal("0.00")
    )

    personal_total = float(personal_total_dec.quantize(two_dp, rounding=ROUND_HALF_UP))
    personal_prev_month_total = float(
        personal_prev_month_total_dec.quantize(two_dp, rounding=ROUND_HALF_UP)
    )
    personal_expense_count = len(current_month_personal)
    personal_expenses_resp = [
        ExpenseResponse.model_validate(e) for e in personal_expenses_orm
    ]

    # ── 2. Groups & Group Balances ────────────────────────────────────
    user_groups_orm = await get_user_groups(db, user_id)
    groups_resp = [GroupResponse.model_validate(g) for g in user_groups_orm]

    group_share_total_dec = Decimal("0.00")
    group_share_prev_month_total_dec = Decimal("0.00")
    group_balances_resp: list[GroupBalanceSummaryResponse] = []
    group_user_splits_resp: list[GroupUserSplitItemResponse] = []
    group_monthly_tx_count = 0
    activity_items: list[DashboardActivityItemResponse] = []

    if user_groups_orm:
        group_ids = [g.id for g in user_groups_orm]
        group_map = {g.id: g.name for g in user_groups_orm}

        # Member count per group
        member_counts_stmt = (
            select(GroupMember.group_id, func.count(GroupMember.user_id))
            .where(GroupMember.group_id.in_(group_ids))
            .group_by(GroupMember.group_id)
        )
        mc_result = await db.execute(member_counts_stmt)
        group_member_count_map = dict(mc_result.all())

        # Valid group expenses with splits
        group_expenses_stmt = (
            select(Expense)
            .options(selectinload(Expense.splits))
            .where(
                Expense.group_id.in_(group_ids),
                or_(Expense.note.is_(None), Expense.note != "Automated Monthly Rent"),
                or_(Expense.category.is_(None), Expense.category != "system"),
            )
            .order_by(Expense.expense_date.desc(), Expense.created_at.desc())
        )
        ge_result = await db.execute(group_expenses_stmt)
        valid_group_expenses = ge_result.scalars().all()

        # Splits by expense
        splits_by_expense = {e.id: e.splits for e in valid_group_expenses}

        # Compute user's group share totals & splits list
        for exp in valid_group_expenses:
            exp_splits = splits_by_expense.get(exp.id, [])
            user_split = next((s for s in exp_splits if s.user_id == user_id), None)
            exp_month = exp.expense_date.strftime("%Y-%m") if exp.expense_date else ""

            if user_split:
                group_user_splits_resp.append(
                    GroupUserSplitItemResponse(
                        id=exp.id,
                        expense_date=str(exp.expense_date) if exp.expense_date else "",
                        category=exp.category or "other",
                        amount=float(user_split.amount),
                        group_id=exp.group_id,
                    )
                )

                split_amount_dec = Decimal(str(user_split.amount))
                if exp_month == current_month_str:
                    group_share_total_dec += split_amount_dec
                    group_monthly_tx_count += 1
                elif exp_month == prev_month_str:
                    group_share_prev_month_total_dec += split_amount_dec
            else:
                payer_id = exp.paid_by or exp.user_id
                if payer_id == user_id and exp_month == current_month_str:
                    group_monthly_tx_count += 1

        # Compute net balances for each group
        for g in user_groups_orm:
            g_expenses = [e for e in valid_group_expenses if e.group_id == g.id]
            is_reimbursable = g.type == "reimbursable"
            sponsor_id: UUID | None = None
            if g.sponsor_id:
                try:
                    sponsor_id = UUID(str(g.sponsor_id))
                except (ValueError, TypeError):
                    sponsor_id = None

            user_net_dec = Decimal("0.00")

            for exp in g_expenses:
                payer_id = exp.paid_by or exp.user_id
                exp_splits = [s for s in (splits_by_expense.get(exp.id) or []) if not s.is_settled]

                if is_reimbursable and sponsor_id:
                    if payer_id != sponsor_id:
                        total_amount = sum(Decimal(str(s.amount)) for s in exp_splits)
                        if total_amount > Decimal("0.01"):
                            if payer_id == user_id:
                                user_net_dec += total_amount
                            if sponsor_id == user_id:
                                user_net_dec -= total_amount
                else:
                    for s in exp_splits:
                        split_amt = Decimal(str(s.amount))
                        if s.user_id != payer_id:
                            if payer_id == user_id:
                                user_net_dec += split_amt
                            if s.user_id == user_id:
                                user_net_dec -= split_amt

            group_balances_resp.append(
                GroupBalanceSummaryResponse(
                    groupId=g.id,
                    groupName=g.name,
                    groupType=g.type,
                    memberCount=group_member_count_map.get(g.id, 1),
                    userNetBalance=float(user_net_dec.quantize(two_dp, rounding=ROUND_HALF_UP)),
                )
            )

        # Build activity items for recent group expenses
        payer_user_ids = {e.paid_by or e.user_id for e in valid_group_expenses[:15] if e.paid_by or e.user_id}
        payer_profile_map: dict[UUID, str] = {}
        if payer_user_ids:
            profiles_stmt = select(Profile).where(Profile.user_id.in_(list(payer_user_ids)))
            profiles_result = await db.execute(profiles_stmt)
            for p in profiles_result.scalars().all():
                payer_profile_map[p.user_id] = p.display_name or p.email or "Member"

        for exp in valid_group_expenses[:15]:
            payer_id = exp.paid_by or exp.user_id
            is_user_payer = payer_id == user_id
            payer_name = "You" if is_user_payer else payer_profile_map.get(payer_id, "Member")
            group_name = group_map.get(exp.group_id, "Group")
            exp_date_str = str(exp.expense_date) if exp.expense_date else (str(exp.created_at.date()) if exp.created_at else "")

            if exp.category == "payment":
                activity_items.append(
                    DashboardActivityItemResponse(
                        id=f"settlement-{exp.id}",
                        type="settlement",
                        title=f"{payer_name} recorded a settlement",
                        subText=f"in {group_name}",
                        amount=float(exp.amount),
                        date=exp_date_str,
                        category=exp.category,
                        link=f"/groups/{exp.group_id}",
                    )
                )
            else:
                activity_items.append(
                    DashboardActivityItemResponse(
                        id=f"group-{exp.id}",
                        type="group_expense",
                        title=f"{payer_name} added {exp.note or 'expense'}",
                        subText=f"in {group_name}",
                        amount=float(exp.amount),
                        date=exp_date_str,
                        category=exp.category or "other",
                        link=f"/groups/{exp.group_id}",
                    )
                )

    # Add personal expenses to activity items
    for exp in personal_expenses_orm[:10]:
        exp_date_str = str(exp.expense_date) if exp.expense_date else (str(exp.created_at.date()) if exp.created_at else "")
        activity_items.append(
            DashboardActivityItemResponse(
                id=f"personal-{exp.id}",
                type="personal_expense",
                title=f"You added {exp.note or 'expense'}",
                subText="Personal",
                amount=float(exp.amount),
                date=exp_date_str,
                category=exp.category or "other",
                link="/expenses",
            )
        )

    # Sort activity items by date descending and limit to 10
    recent_activity = sorted(activity_items, key=lambda a: a.date or "", reverse=True)[:10]
    last_expense_date = recent_activity[0].date if recent_activity else None

    # ── 3. Friend Balances ────────────────────────────────────────────
    raw_friend_balances = await get_friend_balances(db, user_id)
    friend_balances_resp: list[DashboardFriendBalanceResponse] = []
    if raw_friend_balances:
        friend_ids = [fb["friendId"] for fb in raw_friend_balances]
        f_profiles_stmt = select(Profile).where(Profile.user_id.in_(friend_ids))
        f_profiles_result = await db.execute(f_profiles_stmt)
        friend_profile_map = {
            p.user_id: p.display_name or p.email or "Friend"
            for p in f_profiles_result.scalars().all()
        }

        for fb in raw_friend_balances:
            friend_balances_resp.append(
                DashboardFriendBalanceResponse(
                    friendId=fb["friendId"],
                    netBalance=fb["netBalance"],
                    friendName=friend_profile_map.get(fb["friendId"], "Friend"),
                )
            )

    # ── 4. Unified Totals & Debt Aggregates ───────────────────────────
    group_share_total = float(group_share_total_dec.quantize(two_dp, rounding=ROUND_HALF_UP))
    group_share_prev_month_total = float(
        group_share_prev_month_total_dec.quantize(two_dp, rounding=ROUND_HALF_UP)
    )

    unified_total = round(personal_total + group_share_total, 2)
    unified_prev_month_total = round(personal_prev_month_total + group_share_prev_month_total, 2)

    month_over_month_pct: int | None = None
    if unified_prev_month_total > 0:
        month_over_month_pct = round(
            ((unified_total - unified_prev_month_total) / unified_prev_month_total) * 100
        )

    net_owed_dec = Decimal("0.00")
    net_owes_dec = Decimal("0.00")
    unsettled_count = 0

    # Group debts
    for gb in group_balances_resp:
        bal = Decimal(str(gb.userNetBalance))
        if bal > Decimal("0.01"):
            net_owed_dec += bal
            unsettled_count += 1
        elif bal < Decimal("-0.01"):
            net_owes_dec += abs(bal)
            unsettled_count += 1

    # Friend debts
    for fb in friend_balances_resp:
        bal = Decimal(str(fb.netBalance))
        if bal > Decimal("0.01"):
            net_owed_dec += bal
            unsettled_count += 1
        elif bal < Decimal("-0.01"):
            net_owes_dec += abs(bal)
            unsettled_count += 1

    net_owed = float(net_owed_dec.quantize(two_dp, rounding=ROUND_HALF_UP))
    net_owes = float(net_owes_dec.quantize(two_dp, rounding=ROUND_HALF_UP))
    net_balance = round(net_owed - net_owes, 2)

    monthly_transaction_count = len(current_month_personal) + group_monthly_tx_count

    return DashboardDataResponse(
        personalTotal=personal_total,
        personalPrevMonthTotal=personal_prev_month_total,
        personalExpenseCount=personal_expense_count,
        personalExpenses=personal_expenses_resp,
        groups=groups_resp,
        groupShareTotal=group_share_total,
        groupSharePrevMonthTotal=group_share_prev_month_total,
        groupBalances=group_balances_resp,
        groupUserSplits=group_user_splits_resp,
        friendBalances=friend_balances_resp,
        unifiedTotal=unified_total,
        unifiedPrevMonthTotal=unified_prev_month_total,
        monthOverMonthPct=month_over_month_pct,
        netOwed=net_owed,
        netOwes=net_owes,
        netBalance=net_balance,
        unsettledCount=unsettled_count,
        monthlyTransactionCount=monthly_transaction_count,
        recentActivity=recent_activity,
        lastExpenseDate=last_expense_date,
    )
