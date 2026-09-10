from typing import Annotated
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse
from app.schemas.envelope import ResponseEnvelope
from app.schemas.user import ProfileResponse
from app.services.auth_service import delete_user_account, login_user, signup_user

router = APIRouter()


@router.post("/signup", response_model=ResponseEnvelope[TokenResponse], status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[TokenResponse]:
    """
    Register a new user account with email, password, and optional display name.
    Returns the user profile and a JWT bearer access token.
    """
    profile, token = await signup_user(db, payload)
    return ResponseEnvelope(
        data=TokenResponse(
            access_token=token,
            token_type="bearer",
            user=ProfileResponse.model_validate(profile),
        )
    )


@router.post("/login", response_model=ResponseEnvelope[TokenResponse])
async def login(
    payload: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[TokenResponse]:
    """
    Authenticate with email and password.
    Returns the user profile and a JWT bearer access token.
    """
    profile, token = await login_user(db, payload)
    return ResponseEnvelope(
        data=TokenResponse(
            access_token=token,
            token_type="bearer",
            user=ProfileResponse.model_validate(profile),
        )
    )


@router.post("/logout", response_model=ResponseEnvelope[dict])
async def logout(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> ResponseEnvelope[dict]:
    """
    Invalidate session (client should discard the JWT from localStorage).
    """
    return ResponseEnvelope(data={"message": "Logged out successfully"})


@router.delete("/account", response_model=ResponseEnvelope[dict])
async def delete_account(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[dict]:
    """
    Permanently delete the authenticated user's account and all associated data.
    Replaces the legacy Supabase delete_user_account RPC.
    """
    await delete_user_account(db, current_user.id)
    return ResponseEnvelope(data={"message": "Account successfully deleted"})
