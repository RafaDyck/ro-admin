"""Capability detection.

Consumers must be able to ask what this install can actually do rather than
assuming. Tier 0 is available whenever the database answers. Tier 1 and 2
detection is deliberately stubbed to False in this slice -- their artifacts do
not exist yet, and reporting a capability we cannot deliver is exactly the
failure mode the predecessor had when its UI claimed changes were live.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import get_settings, requires
from ro_admin.permissions import Permission

router = APIRouter(prefix="/api/v1/system", tags=["system"])

KNOWN_LOG_TABLES = (
    "atcommandlog", "picklog", "zenylog", "chatlog",
    "loginlog", "mvplog", "branchlog", "charlog",
)


class Tier0(BaseModel):
    available: bool
    log_tables: list[str]


class Tier(BaseModel):
    available: bool
    reason: str


class Capabilities(BaseModel):
    tier0: Tier0
    tier1: Tier
    tier2: Tier


@router.get(
    "/capabilities",
    response_model=Capabilities,
    dependencies=[Depends(requires(Permission.SYSTEM_READ))],
    summary="Which install tiers and data sources are available",
)
def capabilities(settings: Settings = Depends(get_settings)) -> Capabilities:
    db = Database(settings)
    present = {
        r["t"].lower()
        for r in db.query(
            "SELECT table_name AS t FROM information_schema.tables WHERE table_schema = %s",
            (settings.db_name,),
        )
    }
    return Capabilities(
        tier0=Tier0(
            available=True,
            log_tables=sorted(t for t in KNOWN_LOG_TABLES if t in present),
        ),
        tier1=Tier(available=False, reason="script overlay not implemented in this release"),
        tier2=Tier(available=False, reason="compiled hooks not implemented in this release"),
    )
