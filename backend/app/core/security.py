import logging
from functools import lru_cache

import requests
import jwt
from jwt import PyJWKClient, PyJWKClientError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

"""
Azure Entra ID (formerly Azure AD) JWT token validation.
Flow:
  1. Angular frontend logs in via MSAL and gets a JWT access token.
  2. Angular sends the token in the Authorization: Bearer <token> header.
  3. This module validates the token signature using Microsoft's public JWKS keys.
  4. If valid, it extracts the user's email and returns it.
  5. Every router uses get_current_user_email as a dependency — this is
     the single gate through which all API requests must pass.
"""
# ── JWKS client (cached) ──────────────────────────────────────────────────────
# Microsoft publishes public keys at this URL. PyJWKClient caches them and
# automatically re-fetches when a new key ID (kid) appears.
JWKS_URI = (
    f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}"
    f"/discovery/v2.0/keys"
)

@lru_cache()
def _get_jwks_client() -> PyJWKClient:
    """Return a cached JWKS client. Created once per process."""
    return PyJWKClient(JWKS_URI, cache_keys=True)


# ── Bearer scheme ─────────────────────────────────────────────────────────────
bearer_scheme = HTTPBearer(
    scheme_name="Azure Entra ID Bearer Token",
    description="Paste the Bearer token obtained from Azure Entra ID login.",
    auto_error=True,   # returns 403 automatically if header is missing
)


# ── Main dependency ────────────────────────────────────────────────────────────
def get_current_user_email(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    """
    FastAPI dependency. Validates the Bearer token and returns the user's
    email address (lowercase). Raises HTTP 401 on any validation failure.

    Usage in a router:
        @router.get("/team/summary")
        def team_summary(email: str = Depends(get_current_user_email)):
            # email is now guaranteed to be the authenticated manager's email
    """
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.AZURE_CLIENT_ID,
            issuer=f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}/v2.0",
            options={"verify_exp": True},
        )

        # Azure tokens use 'preferred_username' for UPNs (user@company.com).
        # 'upn' and 'email' are fallbacks depending on the token config.
        email: str = (
            payload.get("preferred_username")
            or payload.get("upn")
            or payload.get("email")
        )

        if not email:
            logger.warning("Token validated but no email claim found. Payload keys: %s", list(payload.keys()))
            raise credentials_exception

        return email.lower().strip()

    except PyJWKClientError as e:
        logger.error("JWKS key fetch failed: %s", e)
        raise credentials_exception
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid token: %s", e)
        raise credentials_exception


# ── Dev-only bypass ────────────────────────────────────────────────────────────
def get_current_user_email_dev_bypass(email: str = "dev.manager@yourcompany.com") -> str:
    """
    USE ONLY IN DEVELOPMENT when you don't have an Entra ID token yet.
    Swap in main.py by replacing get_current_user_email with this.
    Never deploy this to production.
    """
    return email