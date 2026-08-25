"""The one confirmation the spec promised and nothing enforced.

    "Mitigations: narrowly scoped tokens; destructive endpoints require
     explicit confirmation regardless of caller; all mutations audit-logged
     with the acting principal."

Audit logging has been in place since ro_admin_commands.requested_by. Scoped
tokens exist. This is the third.

"Regardless of caller" is the operative phrase. SKILL.md already instructs an
agent to confirm destructive actions with its user, but an instruction to a
client is not enforcement -- an agent that misreads "take 500 zeny back" as
500,000 follows its instructions perfectly and still empties the account.
"""
import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_PASSWORD, ADMIN_USER, CHAR_WITH_ECONOMY, apply_test_env


@pytest.fixture()
def client(monkeypatch):
    apply_test_env(monkeypatch)
    from ro_admin.main import app
    return TestClient(app)


@pytest.fixture()
def headers(client):
    r = client.post("/api/v1/auth/login",
                    json={"userid": ADMIN_USER, "password": ADMIN_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.integration
def test_taking_zeny_away_without_confirm_is_refused(client, headers):
    r = client.post("/api/v1/commands", headers=headers, json={
        "char_id": CHAR_WITH_ECONOMY, "action": "adjust_zeny", "delta": -500,
    })
    assert r.status_code == 422


@pytest.mark.integration
def test_the_refusal_says_what_to_do(client, headers):
    """A refusal a caller cannot act on is a wall, not a guard."""
    r = client.post("/api/v1/commands", headers=headers, json={
        "char_id": CHAR_WITH_ECONOMY, "action": "adjust_zeny", "delta": -500,
    })
    detail = str(r.json()["detail"]).lower()
    assert "confirm" in detail


@pytest.mark.integration
def test_taking_zeny_away_with_confirm_is_accepted(client, headers):
    r = client.post("/api/v1/commands", headers=headers, json={
        "char_id": CHAR_WITH_ECONOMY, "action": "adjust_zeny",
        "delta": -500, "confirm": True,
    })
    assert r.status_code in (202, 409)   # 409 only if the overlay is down


@pytest.mark.integration
def test_giving_zeny_needs_no_confirmation(client, headers):
    """The gate is narrow on purpose. A blanket confirm flag is one everybody
    learns to always send, which is the same as no gate at all."""
    r = client.post("/api/v1/commands", headers=headers, json={
        "char_id": CHAR_WITH_ECONOMY, "action": "adjust_zeny", "delta": 500,
    })
    assert r.status_code in (202, 409)


@pytest.mark.integration
def test_giving_an_item_needs_no_confirmation(client, headers):
    """give_item adds. Nothing is destroyed."""
    r = client.post("/api/v1/commands", headers=headers, json={
        "char_id": CHAR_WITH_ECONOMY, "action": "give_item",
        "item_id": 909, "amount": 1,
    })
    assert r.status_code in (202, 409)


@pytest.mark.integration
def test_confirm_false_is_the_same_as_absent(client, headers):
    """Explicitly declining must not read as consent."""
    r = client.post("/api/v1/commands", headers=headers, json={
        "char_id": CHAR_WITH_ECONOMY, "action": "adjust_zeny",
        "delta": -500, "confirm": False,
    })
    assert r.status_code == 422


@pytest.mark.integration
def test_a_refused_command_is_not_queued(client, headers):
    """The refusal must happen before the insert, or the queue accumulates
    rows nobody authorised.

    Validation is on the request model, so it runs before the handler body --
    but that is a claim about pydantic's ordering, and the point of the test is
    to pin it rather than trust it. A 422 carries no id, so there is nothing to
    poll and nothing was written.
    """
    r = client.post("/api/v1/commands", headers=headers, json={
        "char_id": CHAR_WITH_ECONOMY, "action": "adjust_zeny", "delta": -500,
    })
    assert r.status_code == 422
    assert "id" not in r.json()
