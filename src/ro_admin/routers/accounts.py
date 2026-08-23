"""Account reads.

Reads only. Every field served here comes from projections.ACCOUNT_COLUMNS,
which excludes user_pass, pincode and web_auth_token -- see that module for
why an allowlist rather than a denylist.
"""
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import get_settings, requires
from ro_admin.permissions import Permission
from ro_admin.projections import ACCOUNT_COLUMNS, CHARACTER_COLUMNS, select_clause
from ro_admin.routers.characters import CharacterPage, _to_character

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])

# rAthena's permanent-ban state. Other non-zero states exist and mean other
# things; only 5 is a ban.
STATE_BANNED = 5


class Account(BaseModel):
    account_id: int
    userid: str
    sex: str
    group_id: int
    state: int
    # Derived, because the caller should not have to know that rAthena records
    # a ban in two unrelated columns depending on whether it expires.
    banned: bool
    unban_time: int
    expiration_time: int
    logincount: int
    lastlogin: datetime | None = None
    character_slots: int
    vip_time: int


class AccountPage(BaseModel):
    items: list[Account]
    limit: int
    offset: int


def _to_account(row: dict, now: int) -> Account:
    return Account(
        banned=row["state"] == STATE_BANNED or row["unban_time"] > now,
        **{k: row[k] for k in ACCOUNT_COLUMNS},
    )


@router.get(
    "",
    response_model=AccountPage,
    dependencies=[Depends(requires(Permission.ACCOUNTS_READ))],
    summary="List accounts",
)
def list_accounts(
    userid: str | None = Query(default=None, description="Exact match"),
    min_group_id: int | None = Query(default=None, ge=0, le=99),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
) -> AccountPage:
    clauses, params = [], []
    if userid is not None:
        clauses.append("userid = %s")
        params.append(userid)
    if min_group_id is not None:
        clauses.append("group_id >= %s")
        params.append(min_group_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    rows = Database(settings).query(
        f"SELECT {select_clause(ACCOUNT_COLUMNS)} FROM login {where} "
        f"ORDER BY account_id LIMIT %s OFFSET %s",
        params,
    )
    now = int(time.time())
    return AccountPage(
        items=[_to_account(r, now) for r in rows], limit=limit, offset=offset
    )


@router.get(
    "/{account_id}",
    response_model=Account,
    dependencies=[Depends(requires(Permission.ACCOUNTS_READ))],
    summary="One account",
)
def get_account(
    account_id: int, settings: Settings = Depends(get_settings)
) -> Account:
    rows = Database(settings).query(
        f"SELECT {select_clause(ACCOUNT_COLUMNS)} FROM login WHERE account_id = %s",
        (account_id,),
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no account with id {account_id}",
        )
    return _to_account(rows[0], int(time.time()))


@router.get(
    "/{account_id}/characters",
    response_model=CharacterPage,
    dependencies=[Depends(requires(Permission.CHARACTERS_READ))],
    summary="The characters belonging to one account",
)
def account_characters(
    account_id: int, settings: Settings = Depends(get_settings)
) -> CharacterPage:
    db = Database(settings)
    if not db.query("SELECT account_id FROM login WHERE account_id = %s", (account_id,)):
        # 404, not an empty list. An empty list asserts "this account has no
        # characters", which is a different claim and a false one.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no account with id {account_id}",
        )
    rows = db.query(
        f"SELECT {select_clause(CHARACTER_COLUMNS)} FROM `char` "
        f"WHERE account_id = %s ORDER BY char_num",
        (account_id,),
    )
    return CharacterPage(
        items=[_to_character(r) for r in rows],
        limit=len(rows), offset=0,
    )
