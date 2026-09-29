"""Capability detection.

Consumers must be able to ask what this install can actually do rather than
assuming. Every field here is an observation:

  * Tier 0 lists the log tables that are actually present.
  * Tier 1 is reported from the overlay's heartbeat -- a row the NPC script
    rewrites on every poll. Not from a config file, not from a path on disk:
    the API cannot see the game server's filesystem, and a file that exists
    is not a script that is running.
  * Tier 2 is reported the same way, and its heartbeat carries more weight
    than Tier 1's. Tier 2's script calls a COMPILED buildin, and a script
    naming an unknown buildin fails to parse and is discarded
    (src/map/npc.cpp:4421-4424). So a row in ro_admin_tier2 can only exist if
    the hook is in the running map server's binary -- there is no setting to
    misreport and no operator claim to take on trust.

Reporting a capability we cannot deliver is the failure mode the predecessor
had when its UI claimed changes were live, so every field here stays an
observation even when that means saying no.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import get_settings, requires
from ro_admin.overlay import (
    Action, ConsumerTier, OverlayStatus, consumer_tier, read_status, read_tier2_status,
)
from ro_admin.permissions import Permission
from ro_admin.routers.maps import MAPS_NOT_IMPORTED

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


class Maps(BaseModel):
    imported: bool
    count: int
    reason: str
    imported_at: datetime | None = None


class ActionCapability(BaseModel):
    # The tier whose script consumes this action; its `available` and `reason`
    # are that tier's, so a client can offer or explain the action without
    # knowing the mapping itself.
    tier: ConsumerTier
    available: bool
    reason: str


class Capabilities(BaseModel):
    tier0: Tier0
    tier1: Tier
    tier2: Tier
    maps: Maps
    actions: dict[str, ActionCapability]


def _maps(db: Database, present: set[str]) -> Maps:
    """Reported from the table, not from a setting that claims it.

    rAthena has no map list in SQL, so an install that has not run the importer
    genuinely cannot answer map questions -- and saying so is more useful than
    an empty list, which reads as "this server has no maps".
    """
    if "ro_admin_maps" not in present:
        # Imported from routers/maps.py so the map endpoints' 503 and this
        # reason are the same sentence, not two that agree today.
        return Maps(imported=False, count=0, reason=MAPS_NOT_IMPORTED)
    row = db.query(
        "SELECT COUNT(*) AS n, MAX(imported_at) AS at FROM ro_admin_maps"
    )[0]
    count = int(row["n"])
    if count == 0:
        return Maps(
            imported=False, count=0,
            reason="map table exists but is empty: run importers/import_maps.py",
        )
    return Maps(
        imported=True, count=count, imported_at=row["at"],
        reason=f"{count} maps imported",
    )


def _tier(status: OverlayStatus) -> Tier:
    """One mapping for both tiers, so they cannot start answering differently.

    `available` is `usable`, which is responding AND compatible -- a script
    that is alive but speaking a different contract version is not a
    capability, and reporting it as one is how an operator ends up debugging a
    contract change at 2am.
    """
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
    tiers = {
        "tier1": _tier(read_status(db)),
        # Two reads rather than one, because the two tiers have separate
        # heartbeats -- see overlay/tier2/schema.sql for why that separation is
        # deliberate. A shared row would make either tier's availability
        # inferable from the other's age, which is not a thing that is true.
        "tier2": _tier(read_tier2_status(db)),
    }
    # Computed once per action rather than inline in the comprehension below,
    # where it would otherwise be called three times for the same action.
    action_tiers: dict[Action, ConsumerTier] = {a: consumer_tier(a) for a in Action}
    return Capabilities(
        tier0=Tier0(
            available=True,
            log_tables=sorted(t for t in KNOWN_LOG_TABLES if t in present),
        ),
        tier1=tiers["tier1"],
        tier2=tiers["tier2"],
        maps=_maps(db, present),
        actions={
            action.value: ActionCapability(
                tier=tier, available=tiers[tier].available, reason=tiers[tier].reason,
            )
            for action, tier in action_tiers.items()
        },
    )
