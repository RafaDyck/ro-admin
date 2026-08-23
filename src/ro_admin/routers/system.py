"""Capability detection.

Consumers must be able to ask what this install can actually do rather than
assuming. Every field here is an observation:

  * Tier 0 lists the log tables that are actually present.
  * Tier 1 is reported from the overlay's heartbeat -- a row the NPC script
    rewrites on every poll. Not from a config file, not from a path on disk:
    the API cannot see the game server's filesystem, and a file that exists
    is not a script that is running.

Tier 2 remains stubbed; its artifact does not exist yet, and reporting a
capability we cannot deliver is exactly the failure mode the predecessor had
when its UI claimed changes were live.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import get_settings, requires
from ro_admin.overlay import read_status
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
    # installed and responding are separate on purpose: "you have not run
    # schema.sql" and "your map server is down" need different fixes, and one
    # boolean cannot say which.
    installed: bool = False
    responding: bool = False
    version: str | None = None


class Capabilities(BaseModel):
    tier0: Tier0
    tier1: Tier
    tier2: Tier


def _tier1(db: Database) -> Tier:
    status = read_status(db)
    return Tier(
        available=status.usable,
        reason=status.reason,
        installed=status.installed,
        responding=status.responding,
        version=status.version,
    )


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
        tier1=_tier1(db),
        tier2=Tier(available=False, reason="compiled hooks not implemented in this release"),
    )
