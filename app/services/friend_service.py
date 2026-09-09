from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.models.expense import Expense, ExpenseSplit
from app.models.friend import Friend
from app.models.group import GroupMember
from app.models.profile import Profile
from app.models.settlement import MemberMonthlyExclusion, MemberMonthlyStatus
from app.schemas.friend import FriendRequestCreate
from app.services.user_service import get_profile_by_user_id, lookup_user_by_email



async def send_friend_request(
    db: AsyncSession, current_user_id: UUID, payload: FriendRequestCreate
) -> tuple[Friend, Profile | None]:
    """
    Sends a friend request to a target user identified by email or user_id.
    - Resolves target user profile (404 if not found).
    - Prevents self-friending (400).
    - Checks existing relationships in either direction:
      - If already accepted -> 409 Conflict.
      - If pending -> 409 Conflict (idempotency / duplicate protection).
      - If rejected -> resurrects to pending.
      - If none -> inserts new Friend row with status='pending'.
    """
    async with db.begin():
        if payload.email:
            clean_email = payload.email.strip().lower()
            profile = await lookup_user_by_email(db, clean_email)
            if not profile:
                raise HTTPException(404, "User not found")
            target_user_id = profile.user_id
        else:
            assert payload.user_id is not None
            target_user_id = payload.user_id
            profile = await get_profile_by_user_id(db, target_user_id)
            if not profile:
                raise HTTPException(404, "User not found")

        if target_user_id == current_user_id:
            raise HTTPException(400, "Cannot send friend request to yourself")

        stmt = select(Friend).where(
            or_(
                and_(Friend.user_id == current_user_id, Friend.friend_id == target_user_id),
                and_(Friend.user_id == target_user_id, Friend.friend_id == current_user_id),
            )
        ).with_for_update()
        res = await db.execute(stmt)
        existing = res.scalar_one_or_none()

        if existing:
            if existing.status == "accepted":
                raise HTTPException(409, "You are already friends with this user")
            elif existing.status == "pending":
                if existing.user_id == current_user_id:
                    raise HTTPException(409, "A friend request is already pending")
                else:
                    raise HTTPException(
                        409, "This user has already sent you a friend request. Please accept it."
                    )
            elif existing.status == "rejected":
                existing.user_id = current_user_id
                existing.friend_id = target_user_id
                existing.status = "pending"
                existing.updated_at = datetime.now(timezone.utc)
                await db.flush()
                return existing, profile
            else:
                existing.status = "pending"
                existing.user_id = current_user_id
                existing.friend_id = target_user_id
                existing.updated_at = datetime.now(timezone.utc)
                await db.flush()
                return existing, profile
        else:
            friend = Friend(
                user_id=current_user_id,
                friend_id=target_user_id,
                status="pending",
            )
            db.add(friend)
            await db.flush()
            return friend, profile


async def accept_friend_request(
    db: AsyncSession, current_user_id: UUID, friendship_id: UUID
) -> tuple[Friend, Profile | None]:
    """
    Accepts a pending friend request.
    - Only the recipient (friend_id == current_user_id) may accept.
    - Idempotent: if already accepted, returns existing record with 200.
    - If status != 'pending' (e.g. rejected), returns 400.
    """
    async with db.begin():
        friend = (await db.execute(
            select(Friend).where(Friend.id == friendship_id).with_for_update()
        )).scalar_one_or_none()

        if friend is None:
            raise HTTPException(404, "Friend request not found")

        if friend.friend_id != current_user_id:
            if friend.user_id == current_user_id:
                raise HTTPException(403, "Only the recipient can accept a friend request")
            raise HTTPException(403, "Not authorized to accept this friend request")

        if friend.status == "accepted":
            counterpart_id = friend.user_id
            profile = await get_profile_by_user_id(db, counterpart_id)
            return friend, profile

        if friend.status != "pending":
            raise HTTPException(400, "Only pending requests can be accepted")

        friend.status = "accepted"
        friend.updated_at = datetime.now(timezone.utc)
        await db.flush()

        counterpart_id = friend.user_id
        profile = await get_profile_by_user_id(db, counterpart_id)
        return friend, profile


async def reject_friend_request(
    db: AsyncSession, current_user_id: UUID, friendship_id: UUID
) -> tuple[Friend, Profile | None]:
    """
    Rejects a pending friend request.
    - Only the recipient (friend_id == current_user_id) may reject.
    - Idempotent: if already rejected, returns existing record with 200.
    - If status != 'pending', returns 400.
    """
    async with db.begin():
        friend = (await db.execute(
            select(Friend).where(Friend.id == friendship_id).with_for_update()
        )).scalar_one_or_none()

        if friend is None:
            raise HTTPException(404, "Friend request not found")

        if friend.friend_id != current_user_id:
            if friend.user_id == current_user_id:
                raise HTTPException(403, "Only the recipient can reject a friend request")
            raise HTTPException(403, "Not authorized to reject this friend request")

        if friend.status == "rejected":
            counterpart_id = friend.user_id
            profile = await get_profile_by_user_id(db, counterpart_id)
            return friend, profile

        if friend.status != "pending":
            raise HTTPException(400, "Only pending requests can be rejected")

        friend.status = "rejected"
        friend.updated_at = datetime.now(timezone.utc)
        await db.flush()

        counterpart_id = friend.user_id
        profile = await get_profile_by_user_id(db, counterpart_id)
        return friend, profile


async def delete_friendship(
    db: AsyncSession, current_user_id: UUID, friendship_id: UUID
) -> None:
    """
    Cancels a pending sent request, or removes an existing accepted friendship.
    - Only a party to that friendship (user_id or friend_id == current_user_id) may do this.
    - Preserves financial history (expenses and splits remain).
    """
    async with db.begin():
        friend = (await db.execute(
            select(Friend).where(Friend.id == friendship_id).with_for_update()
        )).scalar_one_or_none()

        if friend is None:
            raise HTTPException(404, "Friendship not found")

        if friend.user_id != current_user_id and friend.friend_id != current_user_id:
            raise HTTPException(403, "Not authorized to delete this friendship")

        await db.delete(friend)
        await db.flush()


async def merge_shadow_profile(
    db: AsyncSession, current_user_id: UUID, shadow_user_id: UUID
) -> dict:
    """
    Safely merges a shadow profile into the authenticated real user in an atomic transaction.
    - Validates shadow profile exists and is marked is_shadow=True.
    - Validates real user profile exists and is marked is_shadow=False.
    - Verifies emails match between shadow and authenticated user.
    - Transfers group memberships, expenses, splits, settlements, and friendships.
    - Deletes the shadow profile.
    """
    async with db.begin():
        res = await db.execute(
            select(Profile).where(Profile.user_id == shadow_user_id, Profile.is_shadow.is_(True)).with_for_update()
        )
        shadow_profile = res.scalar_one_or_none()
        if not shadow_profile:
            raise HTTPException(404, "Shadow profile not found or is not a shadow profile")

        res = await db.execute(
            select(Profile).where(Profile.user_id == current_user_id, Profile.is_shadow.is_(False)).with_for_update()
        )
        current_profile = res.scalar_one_or_none()
        if not current_profile:
            raise HTTPException(404, "Real user profile not found")

        if not shadow_profile.email or not current_profile.email or shadow_profile.email.strip().lower() != current_profile.email.strip().lower():
            raise HTTPException(400, "Email mismatch: shadow profile email does not match authenticated user email")

        # 1. group_members: remove shadow membership if current user is already member
        existing_gm = await db.execute(
            select(GroupMember.group_id).where(GroupMember.user_id == current_user_id)
        )
        existing_group_ids = set(existing_gm.scalars().all())
        if existing_group_ids:
            await db.execute(
                delete(GroupMember).where(
                    GroupMember.user_id == shadow_user_id,
                    GroupMember.group_id.in_(existing_group_ids),
                )
            )
        await db.execute(
            update(GroupMember).where(GroupMember.user_id == shadow_user_id).values(user_id=current_user_id)
        )

        # 2. expenses
        await db.execute(
            update(Expense).where(Expense.user_id == shadow_user_id).values(user_id=current_user_id)
        )
        await db.execute(
            update(Expense).where(Expense.paid_by == shadow_user_id).values(paid_by=current_user_id)
        )

        # 3. expense_splits
        await db.execute(
            update(ExpenseSplit).where(ExpenseSplit.user_id == shadow_user_id).values(user_id=current_user_id)
        )

        # 4. member_monthly_status
        await db.execute(
            update(MemberMonthlyStatus).where(MemberMonthlyStatus.user_id == shadow_user_id).values(user_id=current_user_id)
        )

        # 5. member_monthly_exclusions
        await db.execute(
            update(MemberMonthlyExclusion).where(MemberMonthlyExclusion.user_id == shadow_user_id).values(user_id=current_user_id)
        )

        # 6. friends references
        await db.execute(
            update(Friend).where(Friend.friend_id == shadow_user_id).values(friend_id=current_user_id)
        )
        await db.execute(
            update(Friend).where(Friend.user_id == shadow_user_id).values(user_id=current_user_id)
        )

        # 7. Accept pending incoming requests
        now = datetime.now(timezone.utc)
        await db.execute(
            update(Friend).where(Friend.friend_id == current_user_id, Friend.status == "pending").values(status="accepted", updated_at=now)
        )

        # 8. Delete shadow profile
        await db.delete(shadow_profile)
        await db.flush()

        return {"merged": True, "shadow_user_id": str(shadow_user_id), "real_user_id": str(current_user_id)}


async def create_shadow_profile(
    db: AsyncSession, current_user_id: UUID, display_name: str, email: str
) -> tuple[Profile, UUID]:
    """
    Atomically creates a shadow profile and an auto-accepted friendship record in one transaction.
    """
    import uuid
    shadow_user_id = uuid.uuid4()
    clean_email = email.strip().lower()

    async with db.begin():
        profile = Profile(
            user_id=shadow_user_id,
            display_name=display_name.strip(),
            email=clean_email,
            is_shadow=True,
            shadow_created_by=current_user_id,
        )
        db.add(profile)

        friend = Friend(
            user_id=current_user_id,
            friend_id=shadow_user_id,
            status="accepted",
        )
        db.add(friend)
        await db.flush()

    return profile, shadow_user_id


