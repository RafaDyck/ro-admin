"""Which columns leave this service, stated once.

An allowlist, not a denylist, and the difference is the point. rAthena's
`login` table holds `user_pass` -- a varchar(32) containing, on a stock
install, the password itself. Checked against the reference lab on
2026-08-23: account 2 is userid `admin`, user_pass `admin`. It also holds
`pincode`, `web_auth_token`, and personal data.

So `SELECT *` on that table is not an untidy query. It is an endpoint that
serves working credentials for every account on the server.

A denylist would work today and fail later: upstream adds a column, nobody
updates the list, and the new column ships. With an allowlist the same event
is invisible until someone deliberately adds the name -- and
tests/test_no_credentials_leak.py fails the build if a real column ever
appears in a response without being listed here.
"""

# login. Deliberately absent: user_pass, pincode, pincode_change,
# web_auth_token, web_auth_token_enabled (credentials and session tokens);
# email, birthdate, last_ip (personal data). None of these has a read use that
# justifies the blast radius of getting the gate wrong once.
ACCOUNT_COLUMNS: tuple[str, ...] = (
    "account_id",
    "userid",
    "sex",
    "group_id",
    "state",
    "unban_time",
    "expiration_time",
    "logincount",
    "lastlogin",
    "character_slots",
    "vip_time",
)

# char. The table has 80 columns; most are cosmetic ids and client-side UI
# state (hair_color, hotkey_rowshift, body_direction) that no administrative
# question needs. Listing what is useful is shorter than excluding what is not.
CHARACTER_COLUMNS: tuple[str, ...] = (
    "char_id",
    "account_id",
    "name",
    "class",
    "base_level",
    "job_level",
    "base_exp",
    "job_exp",
    "zeny",
    "status_point",
    "skill_point",
    "party_id",
    "guild_id",
    "last_map",
    "last_x",
    "last_y",
    "online",
    "last_login",
    "delete_date",
    "unban_time",
)

# Columns the map server holds in memory while the character is online, and
# flushes on logout or every autosave_time (300s -- see
# rathena-source/conf/map_athena.conf:91).
#
# Measured during the Tier 1 work: after an in-game +777 zeny change, char.zeny
# was unchanged at t+0s, +2s, +5s, +10s and +20s, then read exactly +777 after
# logout. These values are not wrong, they are old, and a response that does
# not say so invites a reader to treat a five-minute-old number as live.
CHARACTER_VOLATILE: frozenset[str] = frozenset({
    "zeny", "base_level", "job_level", "base_exp", "job_exp",
    "status_point", "skill_point", "last_map", "last_x", "last_y",
})


def select_clause(columns: tuple[str, ...]) -> str:
    """Render an allowlist as a backticked SELECT list.

    Backticks are not optional here: `class` and `int` are both reserved
    words and both real column names in rAthena's `char` table.

    The identifier check guards a door that is currently shut -- every caller
    passes a constant from this module. It is here so that stays true if a
    later change ever threads a column name in from a request.
    """
    for name in columns:
        if not name.isidentifier():
            raise ValueError(f"not a column identifier: {name!r}")
    return ", ".join(f"`{name}`" for name in columns)
