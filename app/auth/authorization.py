# app/auth/authorization.py
#
# Separate from app/auth/claims_resolver.py deliberately — that
# file's own docstring is explicit that identity and authorization
# are different concerns, and that this file is where authorization
# would be added once a real feature needed it. Knowledge sync
# (especially full_reset, which wipes real data) is that feature.

import logging

from fastapi import Depends, HTTPException, Request
from pymongo.asynchronous.database import AsyncDatabase

from app.auth.claims_resolver import UserClaims, get_current_user
from app.config import settings
from app.database import get_database

logger = logging.getLogger("app.auth.authorization")


async def require_admin(
    user: UserClaims = Depends(get_current_user),
    db: AsyncDatabase = Depends(get_database),
) -> UserClaims:
    """
    Human-facing admin routes only (POST /knowledge/sync,
    GET /knowledge/sync/{job_id}). Checks a plain admin_users
    collection — deliberately NOT Entra App Roles, which need
    manifest configuration and per-user/group assignment that's
    often not in place (see claims_resolver.py's own docstring).
    """
    is_admin = await db["admin_users"].find_one({"_id": user.user_id})
    if is_admin is None:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_internal_service(request: Request) -> None:
    """
    Machine-to-machine only — the knowledge worker calling
    POST /internal/documents/process. NOT a user session at all, so
    this checks a shared secret header instead of any JWT/claims
    path. Combined with network isolation (this route is never
    exposed via Ocelot's public routes) as defense in depth, not the
    only protection.
    """
    token = request.headers.get("X-Internal-Token")
    if not token or token != settings.INTERNAL_SERVICE_TOKEN:
        raise HTTPException(status_code=403, detail="Not authorized")