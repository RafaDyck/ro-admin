"""FastAPI dependencies. The only place a permission is ever enforced."""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from ro_admin.auth import decode_token
from ro_admin.config import Settings
from ro_admin.permissions import Level, Permission, required_level


@dataclass(frozen=True)
class Principal:
    subject: str
    level: Level
    scopes: tuple[Permission, ...] | None = None


def check_permission(principal: Principal, permission: Permission) -> None:
    """Raise 403 unless the principal may exercise this permission.

    A scoped principal is checked ONLY against its scopes. Falling back to
    level for a scoped token would let a broadly-privileged minter widen a
    deliberately narrow token.
    """
    permission = Permission(permission)
    if principal.scopes is not None:
        allowed = permission in principal.scopes
    else:
        allowed = principal.level >= required_level(permission)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"not permitted: {permission}"
        )


def get_settings() -> Settings:
    return Settings()


def current_principal(
    request: Request, settings: Settings = Depends(get_settings)
) -> Principal:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        claims = decode_token(header.split(" ", 1)[1], settings.jwt_secret)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    scopes = claims.get("scopes")
    return Principal(
        subject=claims["sub"],
        level=Level(claims["lvl"]),
        scopes=tuple(Permission(s) for s in scopes) if scopes is not None else None,
    )


def requires(permission: Permission):
    """Route dependency. Usage: dependencies=[Depends(requires(Permission.LOGS_READ))]"""
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        check_permission(principal, permission)
        return principal
    return dependency
