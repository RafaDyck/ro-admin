"""The Tier 1 enqueue surface.

The single most important assertion in this file is that POST does not claim
success. The predecessor answered an identical request with "Real-time update
queued! Changes will be visible immediately without disconnection" -- while
racing an impostor consumer that made sure they were not.
"""
import pytest
from fastapi.testclient import TestClient

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, PLAYER_PASSWORD, PLAYER_USER,
    CHAR_WITH_ECONOMY, apply_test_env,
)


@pytest.fixture()
def client(monkeypatch):
    apply_test_env(monkeypatch)
    from ro_admin.main import app
    return TestClient(app)


def _token(client, userid, password):
    r = client.post("/api/v1/auth/login", json={"userid": userid, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


def _give_item(char_id=CHAR_WITH_ECONOMY, item_id=909, amount=1):
    return {"char_id": char_id, "action": "give_item",
            "item_id": item_id, "amount": amount}


@pytest.mark.integration
def test_enqueue_rejects_anonymous(client):
    assert client.post("/api/v1/commands", json=_give_item()).status_code == 401


@pytest.mark.integration
def test_enqueue_requires_admin_not_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.post("/api/v1/commands", json=_give_item(),
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


# The states a freshly enqueued row can legitimately be in by the time the
# response is built. See the note in test_enqueue_returns_202_and_the_rows_real_status.
ENQUEUE_STATUSES = {"pending", "processing", "executed", "failed"}


@pytest.mark.integration
def test_enqueue_returns_202_and_the_rows_real_status(client):
    """202, not 200. The work has been accepted, not performed.

    The status is whatever the row genuinely says, normally 'pending'. It is
    deliberately NOT asserted to be 'pending': enqueue is an INSERT and the
    response body comes from a separate SELECT, so an overlay polling every
    1000ms can claim and finish the row in between (~7% of spaced POSTs were
    measured already terminal against a live lab). Pinning 'pending' here
    would be latent flakiness, and "fixing" it in the API by synthesising
    'pending' would mean reporting an unobserved state -- the exact defect
    this product exists to remove. The 202 is not racy and is still pinned.
    """
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.post("/api/v1/commands", json=_give_item(),
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] in ENQUEUE_STATUSES, body["status"]
    assert body["id"] > 0
    assert body["requested_by"] == ADMIN_USER


# Words that, in prose addressed to a caller, assert an outcome this API has
# not observed. The predecessor answered an enqueue with "Real-time update
# queued! Changes will be visible immediately without disconnection" while an
# impostor consumer made sure nothing reached the game, so the wording is the
# defect and this list is the guard against it returning.
CLAIM_WORDS = ("success", "immediately", "visible", "executed", "applied")

# The one field whose VALUE is data rather than prose. See
# test_the_response_never_claims_the_change_is_visible.
DATA_ONLY_FIELDS = frozenset({"status"})


def _prose_strings(node, key=None):
    """Every string in a JSON body except the values of DATA_ONLY_FIELDS.

    Walks the whole structure -- nested objects, arrays, `detail` on an error
    response, and field names themselves -- so that adding a message field
    later puts it under the guard automatically rather than silently outside
    it.
    """
    if isinstance(node, dict):
        for name, value in node.items():
            yield name
            yield from _prose_strings(value, name)
    elif isinstance(node, list):
        for item in node:
            yield from _prose_strings(item, key)
    elif isinstance(node, str) and key not in DATA_ONLY_FIELDS:
        yield node


@pytest.mark.integration
def test_the_response_never_claims_the_change_is_visible(client):
    """A regression guard on wording, because the wording is the defect.

    SCANNED: every string in the response body -- every field name, every
    string value, `detail` on an error response, and anything nested inside
    them. Any of CLAIM_WORDS appearing there fails the test.

    EXEMPT: the value of the `status` field, and nothing else. A data value
    is not a claim. `status` carries the queue row's real state read straight
    back from the database, and 'executed' is one of its legitimate values --
    the overlay polls every 1000ms and can consume a row between the INSERT
    and the SELECT, measured at roughly 7% of requests. The row saying
    'executed' is the API reporting an OBSERVED outcome, which is the
    opposite of the defect this guard exists to catch.

    Why exempt the field rather than drop 'executed' from CLAIM_WORDS: the
    word must still be forbidden in prose. "Your change has been executed"
    in a `detail` is exactly the unobserved claim the predecessor made, and
    dropping the word would blind the guard to it. So the word stays banned
    everywhere a human sentence can appear, and only this one machine-read
    value is excused.

    Do not widen the exemption. Each additional exempt field is a place a
    claim can hide, and any field a caller reads as a sentence is prose no
    matter what it is named. `tests/test_commands.py::
    test_the_wording_guard_exempts_only_the_status_value` pins that this
    exemption is narrow.
    """
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    response = client.post("/api/v1/commands", json=_give_item(),
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 202, response.text
    body = response.json()

    scanned = list(_prose_strings(body))
    # The exemption removes exactly one string. If the walker ever stops
    # reaching the rest of the body, this guard would pass by scanning
    # nothing, so pin that it really did read the other fields.
    assert body["requested_by"] in scanned
    assert body["action"] in scanned
    assert body["status"] not in scanned

    text = " ".join(scanned).lower()
    for claim in CLAIM_WORDS:
        assert claim not in text, (
            f"response asserts an unobserved outcome: {claim!r} in {text!r}"
        )


def test_the_wording_guard_exempts_only_the_status_value():
    """The narrowing above must not have blunted the guard.

    No database: this exercises the scanner directly, so it states the rule
    in a form that cannot drift with the lab's timing. A real 'executed'
    status is only reachable through a ~7% race, and pointing the
    integration test at an online character to provoke it would make it
    flaky rather than stronger.
    """
    clean = {"id": 1, "status": "executed", "action": "give_item",
             "requested_by": "admin1234", "error_message": None,
             "overlay_responding": True}
    assert not [w for w in CLAIM_WORDS
                if w in " ".join(_prose_strings(clean)).lower()]

    # ...and every one of these must still be caught.
    for claim_body in (
        {"status": "pending", "detail": "queued! visible immediately"},
        {"status": "pending", "message": "Your change has been executed"},
        {"status": "pending", "error_message": "applied to the character"},
        {"status": "pending", "detail": "Success"},
        {"status": "pending", "notes": [{"text": "changes appear immediately"}]},
    ):
        text = " ".join(_prose_strings(claim_body)).lower()
        assert [w for w in CLAIM_WORDS if w in text], (
            f"a prose claim slipped through the narrowed guard: {claim_body}"
        )


@pytest.mark.integration
def test_enqueue_records_who_asked(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    new_id = client.post("/api/v1/commands", json=_give_item(), headers=headers).json()["id"]
    assert client.get(f"/api/v1/commands/{new_id}",
                      headers=headers).json()["requested_by"] == ADMIN_USER


@pytest.mark.integration
@pytest.mark.parametrize("body", [
    {"char_id": 1, "action": "give_item", "item_id": 909, "amount": 0},
    {"char_id": 1, "action": "give_item", "item_id": 909, "amount": 30001},
    {"char_id": 1, "action": "give_item", "item_id": 0, "amount": 1},
    {"char_id": 1, "action": "banish_player"},
    {"char_id": 1, "action": "give_item"},
    {"char_id": 1, "action": "adjust_zeny", "delta": 0},
])
def test_invalid_bodies_are_rejected_before_the_queue(client, body):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.post("/api/v1/commands", json=body,
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422


@pytest.mark.integration
def test_reading_a_command_requires_staff(client):
    assert client.get("/api/v1/commands/1").status_code == 401


@pytest.mark.integration
def test_unknown_command_id_is_404(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get("/api/v1/commands/999999999",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


@pytest.mark.integration
def test_status_response_reports_whether_the_overlay_is_alive(client):
    """A row stuck at 'pending' means one of two very different things. The
    caller must be able to tell 'not yet' from 'nobody is listening'."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    new_id = client.post("/api/v1/commands", json=_give_item(), headers=headers).json()["id"]
    assert "overlay_responding" in client.get(
        f"/api/v1/commands/{new_id}", headers=headers).json()
