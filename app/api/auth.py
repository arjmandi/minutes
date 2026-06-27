"""Dev-only token endpoint.

Mints a capability token for local development/testing. Gated to non-production environments;
production tokens are issued by the operator's provisioning flow (out of scope for v1 here).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.tokens import issue_capability_token
from app.config import DEV_ENVS

router = APIRouter(prefix="/auth", tags=["auth"])


class DevTokenRequest(BaseModel):
    principal: str = "dev"
    meetings: list[str] = Field(default_factory=lambda: ["*"])


class DevTokenResponse(BaseModel):
    token: str
    ttl_s: int


@router.post("/dev-token", response_model=DevTokenResponse)
async def dev_token(body: DevTokenRequest, request: Request) -> DevTokenResponse:
    settings = request.app.state.settings
    if settings.app_env not in DEV_ENVS:
        raise HTTPException(status_code=404, detail="not found")
    token = issue_capability_token(
        principal=body.principal,
        secret=settings.auth_secret,
        algorithm=settings.auth_algorithm,
        ttl_s=settings.auth_token_ttl_s,
        meetings=body.meetings,
    )
    return DevTokenResponse(token=token, ttl_s=settings.auth_token_ttl_s)
