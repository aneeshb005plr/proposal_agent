# app/auth/claims_resolver.py
#
# Single-issuer (Entra ID only) JWT validation, with a header-based
# dummy-user fallback for development when no Bearer token is present.
#
# This module resolves IDENTITY ONLY — who the user is (user_id,
# email, name). It deliberately does NOT resolve authorization
# (what the user is allowed to do). Entra App Roles require manifest
# configuration and explicit per-user/group assignment that is often
# not in place, and conflating identity with app-specific permissions
# is the wrong layer for that decision anyway. As of now, every
# authenticated user has identical access — there is no authorization
# layer in this agent. App-specific roles, if ever needed, will be
# resolved separately later (e.g. via a lookup against our own `users`
# collection), once a real feature requires that distinction.
#
# Resolution order:
#   1. Authorization: Bearer <token> present → validate against Entra.
#      Failure here is a hard 401 — we do NOT silently fall through
#      to the dummy-header path, because that would mask real auth
#      bugs as if no token had been sent at all.
#   2. No Authorization header at all → fall back to X-User-* headers,
#      but ONLY when ENVIRONMENT != "production".
#   3. Neither present → 401.

import logging
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient

from app.config import settings

logger = logging.getLogger("app.auth")


@dataclass
class UserClaims:
    """
    Identity only. Single consistent shape returned regardless of
    which path resolved the identity (real Entra token or dev
    fallback headers). Everything downstream depends only on this
    shape, never on how it was produced.

    Deliberately has no `roles` field — see module docstring.
    Authorization is a separate concern from identity, and as of now
    this agent has no authorization layer at all: every authenticated
    user has identical access.
    """
    user_id: str
    email: str
    name: str


# Built once at import time, reused for every request. PyJWKClient
# caches the JWKS document itself (cache_jwk_set + lifespan below),
# so we are not refetching keys on every single request.
_jwks_client = PyJWKClient(
    uri=(
        f"https://login.microsoftonline.com/"
        f"{settings.ENTRA_TENANT_ID}/discovery/v2.0/keys"
    ),
    cache_jwk_set=True,
    lifespan=3600,
    timeout=10,
)

# Entra ID issuer for v2.0 tokens. Must match the `iss` claim exactly.
_EXPECTED_ISSUER = (
    f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}/v2.0"
)

# Application ID URI. For App Registrations using the api://{client-id}
# format, the `aud` claim in the token will be this exact string — not
# the bare GUID. Getting this wrong is a documented, common failure
# mode (tokens get rejected with an audience mismatch even though the
# GUID "looks right").
_EXPECTED_AUDIENCE = f"api://{settings.ENTRA_CLIENT_ID}"


def _validate_entra_token(token: str) -> UserClaims:
    """
    Validates a real Entra ID JWT. Raises HTTPException(401) on any
    failure — signature, issuer, audience, or expiry.

    Extracts identity claims only (oid, preferred_username, name).
    Does not read or care about a `roles` claim.
    """
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
    except Exception as e:
        logger.warning("Could not resolve signing key for token: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=_EXPECTED_ISSUER,
            audience=_EXPECTED_AUDIENCE,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidAudienceError:
        logger.warning(
            "Token audience mismatch — expected %s", _EXPECTED_AUDIENCE
        )
        raise HTTPException(status_code=401, detail="Invalid token audience")
    except jwt.InvalidIssuerError:
        logger.warning(
            "Token issuer mismatch — expected %s", _EXPECTED_ISSUER
        )
        raise HTTPException(status_code=401, detail="Invalid token issuer")
    except jwt.InvalidTokenError as e:
        logger.warning("Token validation failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = payload.get("oid")
    if not user_id:
        raise HTTPException(
            status_code=401, detail="Token missing user identifier (oid)"
        )

    return UserClaims(
        user_id=str(user_id),
        email=str(payload.get("preferred_username", "")),
        name=str(payload.get("name", "")),
    )


def _resolve_dummy_user(request: Request) -> UserClaims:
    """
    Dev-only fallback. Trusts X-User-* headers directly with no
    cryptographic verification — that is the entire point, so you can
    test with different fake users without needing real Entra
    credentials.

    Only reachable when ENVIRONMENT != "production" — enforced by the
    caller (get_current_user), not here, so this function's own
    behaviour stays simple and the safety check lives in one obvious
    place.
    """
    user_id = request.headers.get("X-User-Id")
    email = request.headers.get("X-User-Email")
    name = request.headers.get("X-User-Name", "")

    if not user_id or not email:
        raise HTTPException(
            status_code=401,
            detail=(
                "No Authorization header present, and dummy-user "
                "fallback requires both X-User-Id and X-User-Email "
                "headers."
            ),
        )

    logger.info("Resolved dummy user via headers: %s", user_id)

    return UserClaims(
        user_id=user_id,
        email=email,
        name=name,
    )


async def get_current_user(request: Request) -> UserClaims:
    """
    FastAPI dependency. Use as:

        @router.get("/something")
        async def handler(user: UserClaims = Depends(get_current_user)):
            ...

    Resolution order:
      1. Authorization: Bearer <token> → validate against Entra.
         Failure here is a hard 401, never falls through.
      2. No Authorization header at all → dummy headers, but only
         outside production.
      3. Neither → 401.

    No authorization check happens here or anywhere downstream —
    every successfully resolved user has identical access.
    """
    auth_header = request.headers.get("Authorization")

    if auth_header:
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Authorization header must use Bearer scheme",
            )
        token = auth_header.removeprefix("Bearer ").strip()
        return _validate_entra_token(token)

    if settings.ENVIRONMENT == "production":
        raise HTTPException(
            status_code=401,
            detail="Authorization header required",
        )

    return _resolve_dummy_user(request)