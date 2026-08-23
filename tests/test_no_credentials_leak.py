"""No response may carry a credential, and no future column may sneak in.

rAthena's `login` table stores the password in `user_pass`. On a stock install
that is the password itself -- verified against the reference lab, where
account 2 is userid `admin`, user_pass `admin`. It also holds `pincode` and
`web_auth_token`.

The check is deliberately built from the LIVE table definition rather than a
hardcoded list of bad names. rAthena adds columns; a list written today does
not know about the column added upstream next year. Reading
information_schema means a new column fails this test instead of shipping.

These tests assert on the RESPONSE, not on the allowlist, and that distinction
was earned. Watching them fail showed there are two independent gates: the
projection decides what SQL returns, and the `Account` response model -- a
pydantic model, so `extra='ignore'` by default -- decides what is serialized.
Adding `user_pass` to ACCOUNT_COLUMNS alone leaks nothing, because the model
silently drops the extra field. Only the response can answer the question
these tests actually ask, so only the response is what they read. Proven by
injection: a `SELECT *` with `extra='allow'` fails three of them by name,
listing user_pass, pincode, web_auth_token, email, birthdate and last_ip.
"""
import json

import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_PASSWORD, ADMIN_USER, apply_test_env
from ro_admin.projections import ACCOUNT_COLUMNS


@pytest.fixture()
def client(monkeypatch):
    apply_test_env(monkeypatch)
    from ro_admin.main import app
    return TestClient(app)


@pytest.fixture()
def headers(client):
    r = client.post(
        "/api/v1/auth/login", json={"userid": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _login_columns(monkeypatch) -> set[str]:
    apply_test_env(monkeypatch)
    from ro_admin.config import Settings
    from ro_admin.db import Database
    rows = Database(Settings()).query(
        "SELECT column_name AS c FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'login'"
    )
    return {r["c"] for r in rows}


@pytest.mark.integration
def test_every_login_column_is_either_allowlisted_or_absent(client, headers, monkeypatch):
    """The load-bearing test.

    Any column in the real table that is not on the allowlist must not appear
    anywhere in an accounts response -- as a key or as a value.
    """
    withheld = _login_columns(monkeypatch) - set(ACCOUNT_COLUMNS)
    assert withheld, "if this is empty the test proves nothing"

    body = client.get("/api/v1/accounts", headers=headers).json()
    assert body["items"], "need at least one account for this to mean anything"
    served = set()
    for entry in body["items"]:
        served |= set(entry)
    leaked = served & withheld
    assert not leaked, f"withheld login columns appeared in the response: {leaked}"


def _logins(monkeypatch) -> list[dict]:
    apply_test_env(monkeypatch)
    from ro_admin.config import Settings
    from ro_admin.db import Database
    return Database(Settings()).query("SELECT account_id, userid, user_pass FROM login")


def _strings(node, key=None):
    """Every string in a JSON document, paired with the key it sits under.

    Dict keys are yielded too, under key=None, because a column name reaching
    the caller is itself the leak this file is about.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            yield None, k
            yield from _strings(v, k)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item, key)
    elif isinstance(node, str):
        yield key, node


SCANNED_PATHS = (
    "/api/v1/accounts",
    "/api/v1/accounts/2000005",
    "/api/v1/accounts/2000005/characters",
    "/api/v1/characters",
)


@pytest.mark.integration
def test_no_account_password_value_appears_in_any_response(client, headers, monkeypatch):
    """Values, not just keys. A password could leak under an innocent key name,
    and on this lab the passwords are short real strings, so this is a genuine
    check rather than a formality.

    One exemption, and it is narrow: `userid` is served deliberately, and on a
    stock install some accounts set the password to the login name -- the
    reference lab has three (`admin`/`admin`, `test`/`test`,
    `test1234`/`test1234`). A flat substring scan therefore flags the userid
    the API is supposed to return. So a match is forgiven only when it sits
    under the key `userid` AND is a real userid in the table. Everywhere else,
    under every other key, and as a dict key, a password value fails the test.

    That exemption would open exactly one hole -- a password served under the
    key `userid` -- and the test below closes it by checking every served
    userid against the row it came from.
    """
    rows = _logins(monkeypatch)
    secrets = {
        r["user_pass"] for r in rows if r["user_pass"] and len(r["user_pass"]) >= 4
    }
    userids = {r["userid"] for r in rows}
    assert secrets, "the lab has passwords; if not, this test proves nothing"
    assert secrets - userids, (
        "every password equals its userid, so the exemption would swallow the "
        "whole test -- it would prove nothing on this data"
    )

    for path in SCANNED_PATHS:
        body = client.get(path, headers=headers).json()
        for key, value in _strings(body):
            if key == "userid" and value in userids:
                continue
            assert value not in secrets, (
                f"{path} leaked a password value under key {key!r}"
            )
        # The flat scan too, minus the one known-benign collision, so a leak
        # embedded in a string rather than served as its own field is caught.
        text = json.dumps(body)
        for secret in secrets - userids:
            assert secret not in text, f"{path} leaked a password value"


@pytest.mark.integration
def test_the_served_userid_is_the_real_userid(client, headers, monkeypatch):
    """Closes the hole the `userid` exemption above opens.

    If user_pass were ever served under the key `userid`, the exemption would
    forgive it. It cannot hide here: the value is compared against the row it
    claims to describe.
    """
    by_id = {r["account_id"]: r["userid"] for r in _logins(monkeypatch)}
    body = client.get("/api/v1/accounts", headers=headers).json()
    assert body["items"], "need at least one account for this to mean anything"
    for entry in body["items"]:
        assert entry["userid"] == by_id[entry["account_id"]], (
            f"account {entry['account_id']} was served a userid that is not its "
            "own -- some other column is being rendered into that field"
        )


@pytest.mark.integration
def test_the_single_account_endpoint_is_covered_too(client, headers, monkeypatch):
    """The list endpoint and the detail endpoint are separate queries, and a
    projection applied to one is not automatically applied to the other."""
    withheld = _login_columns(monkeypatch) - set(ACCOUNT_COLUMNS)
    body = client.get("/api/v1/accounts/2000005", headers=headers).json()
    leaked = set(body) & withheld
    assert not leaked, f"withheld login columns in the detail response: {leaked}"
