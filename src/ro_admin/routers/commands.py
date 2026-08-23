"""Tier 1 command queue.

Two endpoints and one rule: this API reports what the queue row says, and
never anything more.

Enqueueing returns 202 -- the work has been accepted, not performed -- with
the row's REAL status at the moment it was read back. That status is normally
'pending', because normally nothing has happened in the game yet. It is not
guaranteed to be. Enqueueing is an INSERT and the response body comes from a
separate SELECT, and in between the overlay -- which polls every 1000ms -- can
claim, execute and finish the row. Measured: 2 of 30 spaced POSTs (~7%) came
back already terminal.

That is not a bug to paper over. Synthesising 'pending' for a row the database
says is 'executed' would mean reporting a state nobody observed, which is the
exact defect this product exists to remove. So the response stays honest and
callers must branch on the status they are handed rather than assume 'pending'
and poll blindly -- a 202 whose body already reads 'executed' or 'failed' is
the final answer, not a stale one.

The outcome, whenever it arrives, is written into the row by the only process
that can actually observe it.
"""
from datetime import datetime
from typing import Annotated, Literal, Union

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import Principal, get_settings, requires
from ro_admin.overlay import (
    Action, InvalidCommand, enqueue, read_command, read_status,
)
from ro_admin.permissions import Permission

router = APIRouter(prefix="/api/v1/commands", tags=["commands"])


class GiveItem(BaseModel):
    """Grant items through the game server, so picklog records it and the
    game's own stacking rules apply."""
    action: Literal["give_item"]
    char_id: int = Field(gt=0)
    item_id: int = Field(gt=0)
    amount: int = Field(gt=0, le=30_000, description="rAthena's MAX_AMOUNT is 30000")


class AdjustZeny(BaseModel):
    """Change zeny by a delta. Negative removes.

    There is no absolute 'set zeny' action. Setting an absolute value through
    the game server requires read-current, compute-difference, apply -- which
    races the player's own earning and spending in between. Offering only the
    operation that maps atomically onto @zeny is the honest choice.

    `delta` must be nonzero: @zeny 0 is refused outright by the game
    (src/map/atcommand.cpp:2897-2900), and the overlay's post-condition check
    cannot tell that refusal apart from a real change of zero, so a zero
    delta would be recorded as executed for work the game never did.
    """
    action: Literal["adjust_zeny"]
    char_id: int = Field(gt=0)
    delta: int = Field(ge=-1_000_000_000, le=1_000_000_000)

    @field_validator("delta")
    @classmethod
    def _delta_must_be_nonzero(cls, value: int) -> int:
        # Field() has no built-in "not equal to" constraint, so the hole in
        # an otherwise-contiguous range needs its own check. See the
        # docstring above and overlay._SPECS[Action.ADJUST_ZENY] for why zero
        # specifically is excluded.
        if value == 0:
            raise ValueError(
                "delta must not be 0: @zeny 0 is refused outright by the "
                "game (src/map/atcommand.cpp:2897-2900) and does nothing"
            )
        return value


CommandRequest = Annotated[Union[GiveItem, AdjustZeny], Field(discriminator="action")]


class CommandRow(BaseModel):
    id: int
    char_id: int
    action: str
    status: str
    requested_by: str
    created_at: datetime
    claimed_by: int | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    # Whether the consumer is alive right now. A 'pending' row with this false
    # is not "still working", it is "nobody is listening".
    overlay_responding: bool


def _row_to_model(row: dict, responding: bool) -> CommandRow:
    return CommandRow(overlay_responding=responding, **{
        k: row[k] for k in (
            "id", "char_id", "action", "status", "requested_by",
            "created_at", "claimed_by", "finished_at", "error_message",
        )
    })


@router.post(
    "",
    response_model=CommandRow,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an action to be applied through the game server",
    description=(
        "Returns 202 -- accepted, not performed -- with the queue row exactly "
        "as it reads at that instant. That is normally status 'pending', but "
        "it MAY ALREADY BE TERMINAL ('executed' or 'failed'): the overlay "
        "polls every 1000ms and can consume the row between the insert and "
        "the read-back, measured at roughly 7% of requests. Branch on the "
        "status you are returned rather than assuming 'pending'; if it is "
        "already terminal that is the observed outcome and there is nothing "
        "to wait for. Otherwise poll GET /api/v1/commands/{id}. This endpoint "
        "never reports an outcome it has not observed, which is why the "
        "status is not normalised to 'pending'. Returns 409 if the overlay "
        "script is not responding, rather than queueing work that nothing "
        "will consume."
    ),
)
def create_command(
    body: CommandRequest,
    response: Response,
    principal: Principal = Depends(requires(Permission.COMMANDS_WRITE)),
    settings: Settings = Depends(get_settings),
) -> CommandRow:
    db = Database(settings)

    overlay = read_status(db)
    if not overlay.usable:
        # Refusing beats accepting. The predecessor's queue held seventy rows
        # that could never succeed, and every one of them was accepted with a
        # cheerful message.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=overlay.reason)

    args = body.model_dump(exclude={"action", "char_id"})
    try:
        new_id = enqueue(
            db,
            char_id=body.char_id,
            action=Action(body.action),
            args=args,
            requested_by=principal.subject,
        )
    except InvalidCommand as exc:
        # Pydantic catches these first; this is the backstop for a bound that
        # exists in the registry but not in the request model.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    response.headers["Location"] = f"/api/v1/commands/{new_id}"
    row = read_command(db, new_id)
    return _row_to_model(row, overlay.responding)


@router.get(
    "/{command_id}",
    response_model=CommandRow,
    dependencies=[Depends(requires(Permission.COMMANDS_READ))],
    summary="The recorded outcome of a queued action",
)
def get_command(
    command_id: int, settings: Settings = Depends(get_settings)
) -> CommandRow:
    db = Database(settings)
    row = read_command(db, command_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such command")
    return _row_to_model(row, read_status(db).responding)
