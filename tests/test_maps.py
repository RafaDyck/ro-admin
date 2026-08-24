"""Map reads.

Everything here comes from ro_admin_maps, populated by the operator from their
own rAthena map cache. The predecessor hardcoded 35 maps with invented
descriptions against a server that loads 1,241; this reads the real list.

The grid bytes are rAthena GAT CELL TYPES, not a walkable flag -- see
importers/mapcache.py. Several tests below exist specifically because assuming
"0 means walkable" was wrong on 497 of 1,263 maps.
"""
import os

import pymysql
import pytest
from fastapi.testclient import TestClient

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, PLAYER_PASSWORD, PLAYER_USER,
    TEST_JWT_SECRET, apply_test_env,
)
from ro_admin.auth import issue_service_token
from ro_admin.permissions import Permission
from ro_admin.routers.maps import MAPS_NOT_IMPORTED
from ro_admin.routers.system import _maps

# From map_gat2cell, rathena/src/map/map.cpp:3270.
BLOCKED_GAT = {1, 5}
KNOWN_GAT = {0, 1, 2, 3, 4, 5, 6}

# The map the geometry tests read. This was payon, because db/map_cache.dat on
# its own does not contain prontera -- nor morocc, izlude or alberta. The table
# has it now: the importer reads all THREE of rAthena's layered caches instead
# of only the base one, and those four are among the eight in db/re/.
#
# prontera is 312x392, so nothing is lost by switching -- the reason payon was
# chosen was that it is NON-SQUARE, and a square map cannot tell a correct grid
# from one whose width and height were swapped, which would make every
# width*height and y*width+x assertion below pass on a transposed
# implementation. prontera is also the map an operator would reach for first,
# and it is in the table at all only because the layering works, so the whole
# geometry section below doubles as a guard against the single-file regression.
#
# Which maps a cache holds is still the operator's business, hence the override,
# exactly as conftest does for characters.
GEOMETRY_MAP = os.environ.get("RO_ADMIN_TEST_MAP", "prontera")
# Well inside 312x392, so it stays valid on a reasonably sized substitute.
PROBE_X, PROBE_Y = 150, 150


@pytest.fixture()
def client(monkeypatch):
    apply_test_env(monkeypatch)
    from ro_admin.main import app
    return TestClient(app)


def _token(client, userid, password):
    r = client.post("/api/v1/auth/login", json={"userid": userid, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


@pytest.fixture()
def headers(client):
    return {"Authorization": f"Bearer {_token(client, ADMIN_USER, ADMIN_PASSWORD)}"}


@pytest.mark.integration
def test_listing_rejects_anonymous(client):
    assert client.get("/api/v1/maps").status_code == 401


@pytest.mark.integration
def test_listing_requires_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get("/api/v1/maps", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.integration
def test_the_list_is_the_servers_real_map_list(client, headers):
    """Not the ten maps SQL happens to know about, and not a hardcoded 35."""
    body = client.get("/api/v1/maps", headers=headers).json()
    assert body["total"] > 1000, (
        f"only {body['total']} maps -- has import_maps.py been run?"
    )


@pytest.mark.integration
def test_the_maps_that_only_the_layered_caches_hold_are_present(client, headers):
    """The defect this suite grew to catch. rAthena reads three map caches --
    db/import/, db/<re|pre-re>/ and db/ -- and takes the first that has a given
    map (map_readallmaps, rathena/src/map/map.cpp:3910). The importer read only
    the last one, so the table held 1,263 maps and told an operator searching
    for prontera that it did not exist, on a server with characters standing on
    it. These four are the ones that were missing.
    """
    for name in ("prontera", "morocc", "izlude", "alberta"):
        r = client.get(f"/api/v1/maps/{name}", headers=headers)
        assert r.status_code == 200, (
            f"{name} is missing -- import_maps.py must read all three layered "
            "map caches, not just db/map_cache.dat"
        )


@pytest.mark.integration
def test_the_geometry_map_is_not_square(client, headers):
    """Guards the tests below rather than the service. A square map cannot tell
    a correct grid from a transposed one, so every width*height and y*width+x
    assertion in this file would pass on an implementation that swapped the
    axes. If GEOMETRY_MAP is ever pointed at a square map, that coverage is
    gone silently -- so it is asserted, not assumed."""
    detail = client.get(f"/api/v1/maps/{GEOMETRY_MAP}", headers=headers).json()
    assert detail["width"] != detail["height"], (
        f"{GEOMETRY_MAP} is {detail['width']}x{detail['height']}; pick a "
        "non-square map or a transposed grid goes unnoticed"
    )


@pytest.mark.integration
def test_a_list_entry_carries_dimensions(client, headers):
    entry = client.get("/api/v1/maps", headers=headers).json()["items"][0]
    assert {"name", "width", "height", "walkable_cells"} <= set(entry)


@pytest.mark.integration
def test_the_list_does_not_carry_the_grid(client, headers):
    """1,263 grids in one response is ~3MB compressed and ~100MB not."""
    entry = client.get("/api/v1/maps", headers=headers).json()["items"][0]
    assert "cells" not in entry


@pytest.mark.integration
def test_name_search_matches_a_substring(client, headers):
    body = client.get("/api/v1/maps", params={"q": "prt"}, headers=headers).json()
    assert body["items"]
    assert all("prt" in e["name"] for e in body["items"])


@pytest.mark.integration
def test_a_percent_sign_in_the_query_is_matched_literally(client, headers):
    """A percent sign is a LIKE wildcard even as a bound parameter. Same defect
    class as the item search, same escape."""
    everything = client.get("/api/v1/maps", headers=headers).json()["total"]
    wildcard = client.get("/api/v1/maps", params={"q": "%"}, headers=headers).json()
    assert wildcard["total"] < everything


@pytest.mark.integration
def test_total_is_the_match_count_not_the_page_size(client, headers):
    body = client.get("/api/v1/maps", params={"limit": 5}, headers=headers).json()
    assert len(body["items"]) == 5
    assert body["total"] > 5


@pytest.mark.integration
def test_one_map_is_returned(client, headers):
    r = client.get(f"/api/v1/maps/{GEOMETRY_MAP}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == GEOMETRY_MAP
    assert body["width"] > 0 and body["height"] > 0


@pytest.mark.integration
def test_an_unknown_map_is_404_naming_it(client, headers):
    """Asserts on the detail text: a route that does not exist also 404s, so a
    bare status check would pass whether or not this endpoint was built."""
    r = client.get("/api/v1/maps/nosuchmap", headers=headers)
    assert r.status_code == 404
    assert "nosuchmap" in r.json()["detail"]


# --- geometry ----------------------------------------------------------------


@pytest.mark.integration
def test_the_grid_is_served_raw_and_is_width_times_height(client, headers):
    """Served as bytes rather than JSON: a 200x200 map is 40,000 cells, and
    base64 in JSON would inflate it for a consumer that wants to blit it."""
    detail = client.get(f"/api/v1/maps/{GEOMETRY_MAP}", headers=headers).json()
    r = client.get(f"/api/v1/maps/{GEOMETRY_MAP}/cells", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert len(r.content) == detail["width"] * detail["height"]


@pytest.mark.integration
def test_the_grid_response_describes_its_own_shape(client, headers):
    """A bare byte array is unusable without knowing the row width."""
    r = client.get(f"/api/v1/maps/{GEOMETRY_MAP}/cells", headers=headers)
    detail = client.get(f"/api/v1/maps/{GEOMETRY_MAP}", headers=headers).json()
    assert int(r.headers["x-map-width"]) == detail["width"]
    assert int(r.headers["x-map-height"]) == detail["height"]


@pytest.mark.integration
def test_the_grid_contains_only_known_gat_types(client, headers):
    """The bytes are gat cell types 0-6, NOT a 0/1 walkable flag. An unknown
    value would mean the blob was written by something other than the importer,
    or that this rAthena version added a type nothing here knows how to read."""
    r = client.get(f"/api/v1/maps/{GEOMETRY_MAP}/cells", headers=headers)
    assert set(r.content) <= KNOWN_GAT


@pytest.mark.integration
def test_the_walkable_count_matches_the_grid(client, headers):
    """walkable_cells is precomputed at import. If it drifts from the grid, a
    listing reports a number nothing backs up.

    Counted as "not blocked", never as "== 0" -- that assumption was wrong on
    497 of 1,263 maps.
    """
    detail = client.get(f"/api/v1/maps/{GEOMETRY_MAP}", headers=headers).json()
    grid = client.get(f"/api/v1/maps/{GEOMETRY_MAP}/cells", headers=headers).content
    walkable = sum(1 for c in grid if c not in BLOCKED_GAT)
    assert walkable == detail["walkable_cells"]


@pytest.mark.integration
def test_the_walkable_count_is_right_on_a_map_that_broke_the_naive_rule(client, headers):
    """ba_2whs02 contains no gat 0 at all. Counting == 0 reported it as having
    ZERO walkable cells when 86,274 of its 129,600 are walkable -- the single
    clearest case of the defect, so it gets its own test."""
    detail = client.get("/api/v1/maps/ba_2whs02", headers=headers).json()
    assert detail["walkable_cells"] > 0
    grid = client.get("/api/v1/maps/ba_2whs02/cells", headers=headers).content
    assert 0 not in set(grid), "if this map now contains gat 0, pick another"
    assert detail["walkable_cells"] == sum(1 for c in grid if c not in BLOCKED_GAT)


@pytest.mark.integration
def test_a_cell_reports_the_gat_type_and_what_it_implies(client, headers):
    """Not just walkable: rAthena distinguishes water and snipable gaps, and
    collapsing that to a boolean throws away what the server knows."""
    r = client.get(
        f"/api/v1/maps/{GEOMETRY_MAP}/cell",
        params={"x": PROBE_X, "y": PROBE_Y}, headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["gat"] in KNOWN_GAT
    for flag in ("walkable", "shootable", "water"):
        assert isinstance(body[flag], bool)


@pytest.mark.integration
def test_a_water_cell_is_walkable_and_wet(client, headers):
    """The flags are derived from the gat type, so a gat 3 cell must come back
    walkable AND water. A rule written as "walkable = (gat == 0)" satisfies
    every test above that only ever sees ground, and fails here.

    ba_2whs02 is gat 3 wherever it is walkable at all, so it always has one.
    """
    detail = client.get("/api/v1/maps/ba_2whs02", headers=headers).json()
    grid = client.get("/api/v1/maps/ba_2whs02/cells", headers=headers).content
    index = grid.index(3)
    x, y = index % detail["width"], index // detail["width"]
    body = client.get(
        "/api/v1/maps/ba_2whs02/cell", params={"x": x, "y": y}, headers=headers
    ).json()
    assert body["gat"] == 3
    assert body["walkable"] is True
    assert body["shootable"] is True
    assert body["water"] is True


@pytest.mark.integration
def test_a_cell_outside_the_map_is_422_not_a_guess(client, headers):
    """Out of bounds is a different answer from "blocked", and reporting it as
    blocked would invent a fact about a cell that does not exist."""
    detail = client.get(f"/api/v1/maps/{GEOMETRY_MAP}", headers=headers).json()
    r = client.get(
        f"/api/v1/maps/{GEOMETRY_MAP}/cell",
        params={"x": detail["width"] + 5, "y": 0}, headers=headers,
    )
    assert r.status_code == 422


@pytest.mark.integration
def test_the_cell_answer_agrees_with_the_grid(client, headers):
    detail = client.get(f"/api/v1/maps/{GEOMETRY_MAP}", headers=headers).json()
    grid = client.get(f"/api/v1/maps/{GEOMETRY_MAP}/cells", headers=headers).content
    x, y = PROBE_X, PROBE_Y
    expected_gat = grid[y * detail["width"] + x]
    body = client.get(
        f"/api/v1/maps/{GEOMETRY_MAP}/cell", params={"x": x, "y": y}, headers=headers
    ).json()
    assert body["gat"] == expected_gat
    assert body["walkable"] is (expected_gat not in BLOCKED_GAT)


# --- capabilities ------------------------------------------------------------


@pytest.mark.integration
def test_capabilities_reports_maps_as_imported(client, headers):
    """Reported from the table, like every other capability -- not from a
    setting that claims it."""
    caps = client.get("/api/v1/system/capabilities", headers=headers).json()
    assert caps["maps"]["imported"] is True
    assert caps["maps"]["count"] > 1000


# --------------------------------------------------------------------------
# An install that has not run the importer.
#
# Unit tests, on purpose. The honest way to provoke this is to DROP
# ro_admin_maps, and that would break every integration test above, every map
# test in the other suites, and the shared lab for whoever runs pytest next.
# So the database layer is faked at exactly the point where the truth arrives:
# pymysql raises ProgrammingError(1146) from Database.query when the table is
# not there -- verified against MySQL 8.0, which answers
# "Table 'ragnarok.ro_admin_maps' doesn't exist" with that errno.
#
# Auth needs no database either: a service token is pure JWT, so these run in
# the no-database suite alongside everything else that does not need the lab.
# --------------------------------------------------------------------------

MAP_ENDPOINTS = (
    "/api/v1/maps",
    "/api/v1/maps/prontera",
    "/api/v1/maps/prontera/cells",
    "/api/v1/maps/prontera/cell?x=1&y=1",
)


def _no_such_table(self, sql, params=None):
    raise pymysql.err.ProgrammingError(
        1146, "Table 'ragnarok.ro_admin_maps' doesn't exist"
    )


@pytest.fixture()
def service_headers():
    """A SYSTEM_READ service token. Minted, not logged in for: login reads the
    database, and these tests are about a database that cannot answer."""
    token = issue_service_token(
        secret=TEST_JWT_SECRET, name="unimported-maps-test",
        scopes=[Permission.SYSTEM_READ], ttl_seconds=60,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def unimported(client, monkeypatch):
    """A client whose database has everything except ro_admin_maps."""
    monkeypatch.setattr("ro_admin.db.Database.query", _no_such_table)
    return client


@pytest.mark.parametrize("path", MAP_ENDPOINTS)
def test_a_missing_map_table_is_503_and_not_500(unimported, service_headers, path):
    """500 says the service is broken. It is not: it has not been given the
    data yet, which is a different fact and the only one an operator can act
    on. 503 rather than 404 because the endpoint is fine -- the install is
    incomplete."""
    r = unimported.get(path, headers=service_headers)
    assert r.status_code == 503, f"{path} answered {r.status_code}: {r.text[:200]}"


@pytest.mark.parametrize("path", MAP_ENDPOINTS)
def test_the_503_names_the_command_that_fixes_it(unimported, service_headers, path):
    """The reason is the whole point. A bare 503 is no better than a 500."""
    detail = unimported.get(path, headers=service_headers).json()["detail"]
    assert "importers/maps_schema.sql" in detail
    assert "importers/import_maps.py" in detail


@pytest.mark.parametrize("path", MAP_ENDPOINTS)
def test_the_503_is_the_same_sentence_capabilities_reports(
    unimported, service_headers, path
):
    """Not "a similar message" -- the same string, from one definition.
    /system/capabilities is what an agent is told to check first, and being
    told two different things about one situation is how the distinction the
    capabilities design exists to draw gets lost again.

    _maps returns before it touches the database when the table is absent, so
    None stands in for it here.
    """
    assert _maps(None, present=set()).reason == MAPS_NOT_IMPORTED
    assert unimported.get(path, headers=service_headers).json()["detail"] ==         MAPS_NOT_IMPORTED


def test_a_sql_error_that_is_not_a_missing_table_is_still_a_500(
    client, monkeypatch, service_headers
):
    """The guard is on the errno, not on the exception class. 1054 (unknown
    column) and 1064 (syntax) are ProgrammingError too, and they are bugs in
    this file -- reporting one as "run the importer" would send an operator to
    fix a database that is perfectly fine.
    """
    def unknown_column(self, sql, params=None):
        raise pymysql.err.ProgrammingError(
            1054, "Unknown column 'nope' in 'field list'"
        )

    monkeypatch.setattr("ro_admin.db.Database.query", unknown_column)
    with pytest.raises(pymysql.err.ProgrammingError):
        client.get("/api/v1/maps", headers=service_headers)


def test_the_openapi_document_declares_the_503(client):
    """So a consumer learns this can happen from the document, rather than by
    provoking it in production."""
    spec = client.get("/openapi.json").json()
    for path in ("/api/v1/maps", "/api/v1/maps/{name}",
                 "/api/v1/maps/{name}/cells", "/api/v1/maps/{name}/cell"):
        responses = spec["paths"][path]["get"]["responses"]
        assert "503" in responses, f"{path} does not document a 503"
        assert responses["503"]["description"] == MAPS_NOT_IMPORTED
