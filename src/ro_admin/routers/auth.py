"""Authentication routes."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ro_admin.auth import issue_token, verify_password
from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import Principal, current_principal, get_settings, is_permitted
from ro_admin.permissions import ALL_PERMISSIONS, Level, Permission

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
    # Typed as the enum itself, not bare str: a permission string that is not
    # a real Permission (from a typo in some future edit here) is then a
    # validation error at response time, not a value a client silently
    # receives.
    permissions: list[Permission]


@router.get("/me", response_model=MeResponse, summary="The authenticated principal")
def me(principal: Principal = Depends(current_principal)) -> MeResponse:
    """Who you are, and exactly what you may do.

    `permissions` is computed by the function that enforces them, so a client
    can decide what to offer without keeping its own copy of the permission
    table.
    """
    return MeResponse(
        subject=principal.subject,
        level=int(principal.level),
        permissions=[p for p in ALL_PERMISSIONS if is_permitted(principal, p)],
    )
