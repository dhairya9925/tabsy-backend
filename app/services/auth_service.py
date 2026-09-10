import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt
from fastapi import HTTPException, status
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.expense import Expense, ExpenseSplit
from app.models.friend import Friend
from app.models.group import GroupMember
from app.models.profile import Profile
from app.models.user_credential import UserCredential
from app.schemas.auth import LoginRequest, SignupRequest

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def create_access_token(user_id: UUID, email: str | None = None) -> str:
    """Generate a signed HS256 JWT access token."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.JWT_ALGORITHM)


async def signup_user(
    db: AsyncSession, payload: SignupRequest
) -> tuple[Profile, str]:
    """
    Register a new user: creates credentials + profile, returns (profile, access_token).
    """
    normalized_email = str(payload.email).lower().strip()

    # Check for existing credentials
    existing = await db.scalar(
        select(UserCredential).where(UserCredential.email == normalized_email)
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists",
        )

    # Hash password & create credentials
    hashed = hash_password(payload.password)
    cred = UserCredential(
        email=normalized_email,
        hashed_password=hashed,
    )
    db.add(cred)
    await db.flush()

    # Create associated profile
    display_name = payload.display_name or normalized_email.split("@")[0]
    profile = Profile(
        user_id=cred.id,
        email=normalized_email,
        display_name=display_name,
        is_shadow=False,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    token = create_access_token(cred.id, cred.email)
    return profile, token


async def login_user(
    db: AsyncSession, payload: LoginRequest
) -> tuple[Profile, str]:
    """
    Authenticate user credentials, returns (profile, access_token).
    """
    normalized_email = str(payload.email).lower().strip()

    cred = await db.scalar(
        select(UserCredential).where(UserCredential.email == normalized_email)
    )
    if not cred or not cred.is_active or not verify_password(payload.password, cred.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Fetch profile
    profile = await db.scalar(
        select(Profile).where(Profile.user_id == cred.id)
    )
    if not profile:
        # Create profile if missing
        profile = Profile(
            user_id=cred.id,
            email=cred.email,
            display_name=cred.email.split("@")[0],
            is_shadow=False,
        )
        db.add(profile)
        await db.commit()
        await db.refresh(profile)

    token = create_access_token(cred.id, cred.email)
    return profile, token


async def delete_user_account(db: AsyncSession, user_id: UUID) -> None:
    """
    Cascading delete of all user data, replacing supabase.rpc('delete_user_account').
    """
    logger.info(f"Deleting account and all associated data for user {user_id}")

    # 1. Group memberships
    await db.execute(
        delete(GroupMember).where(GroupMember.user_id == user_id)
    )

    # 2. Expense splits
    await db.execute(
        delete(ExpenseSplit).where(ExpenseSplit.user_id == user_id)
    )

    # 3. Expenses created by or paid by the user
    await db.execute(
        delete(Expense).where(
            or_(Expense.user_id == user_id, Expense.paid_by == user_id)
        )
    )

    # 4. Friends
    await db.execute(
        delete(Friend).where(
            or_(Friend.user_id == user_id, Friend.friend_id == user_id)
        )
    )

    # 5. Profile
    await db.execute(
        delete(Profile).where(Profile.user_id == user_id)
    )

    # 6. Credentials
    await db.execute(
        delete(UserCredential).where(UserCredential.id == user_id)
    )

    await db.commit()
