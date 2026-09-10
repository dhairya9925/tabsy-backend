import logging
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

# HTTP Bearer scheme
security_scheme = HTTPBearer(auto_error=False)

# PyJWKClient for fetching and caching Supabase asymmetric public keys (ES256 / RS256)
_jwks_client = jwt.PyJWKClient(settings.jwks_url, cache_jwk_set=True, lifespan=3600)


class AuthenticatedUser(BaseModel):
    id: UUID
    email: str | None = None
    role: str | None = None
    raw_claims: dict[str, Any] = {}


def verify_supabase_jwt(token: str) -> dict[str, Any]:
    """
    Verifies a Supabase-issued JWT token.
    Supports asymmetric JWKS (ES256/RS256) and symmetric (HS256) secrets.
    Validates signature, expiry, and audience.
    """
    try:
        # First inspect the unverified header to determine key algorithm & kid
        unverified_header = jwt.get_unverified_header(token)
        alg = unverified_header.get("alg", "ES256")

        # Asymmetric (JWKS) flow: ES256 / RS256
        if alg in ("ES256", "RS256"):
            signing_key = _jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=[alg],
                audience="authenticated",
                options={"verify_aud": True, "verify_exp": True},
            )
            return payload

        # Symmetric (Secret) flow: HS256 (Self-hosted or Supabase secret)
        elif alg == "HS256":
            secret = settings.secret_key
            unverified_claims = jwt.decode(token, options={"verify_signature": False})
            has_aud = "aud" in unverified_claims
            if has_aud:
                payload = jwt.decode(
                    token,
                    secret,
                    algorithms=["HS256"],
                    audience="authenticated",
                    options={"verify_aud": True, "verify_exp": True},
                )
                return payload
            else:
                payload = jwt.decode(
                    token,
                    secret,
                    algorithms=["HS256"],
                    options={"verify_aud": False, "verify_exp": True},
                )
                return payload

        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Unsupported token algorithm: {alg}",
            )

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidAudienceError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token audience (expected 'authenticated')",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError as e:
        logger.warning(f"JWT verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_scheme)],
) -> AuthenticatedUser:
    """
    FastAPI dependency that enforces Supabase JWT verification on protected endpoints.
    Extracts the authenticated user's ID, email, and claims.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header or Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = verify_supabase_jwt(token)

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject (user ID)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Subject in token is not a valid UUID",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthenticatedUser(
        id=user_id,
        email=payload.get("email"),
        role=payload.get("role"),
        raw_claims=payload,
    )
