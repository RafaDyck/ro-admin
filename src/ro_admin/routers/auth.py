"""Authentication routes."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ro_admin.auth import issue_token, verify_password
from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import Principal, current_principal, get_settings
from ro_admin.permissions import Level

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    userid: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    level: int


@router.post("/login", response_model=LoginResponse, summary="Exchange rAthena credentials for a token")
def login(body: LoginRequest, settings: Settings = Depends(get_settings)) -> LoginResponse:
    db = Database(settings)
    rows = db.query(
        "SELECT userid, user_pass, group_id FROM login WHERE userid = %s", (body.userid,)
    )
    if not rows or not verify_password(body.password, rows[0]["user_pass"], settings.md5_passwords):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    raw_group = rows[0]["group_id"]
    level = Level(raw_group) if raw_group in Level._value2member_map_ else Level.PLAYER
    return LoginResponse(
        access_token=issue_token(settings.jwt_secret, rows[0]["userid"], level, settings.token_ttl_seconds),
        level=int(level),
    )


class MeResponse(BaseModel):
    subject: str
    level: int


@router.get("/me", response_model=MeResponse, summary="The authenticated principal")
def me(principal: Principal = Depends(current_principal)) -> MeResponse:
    return MeResponse(subject=principal.subject, level=int(principal.level))
