"""The web UI is served by the API process, and must not get in the API's way.

It is set as the router's fallback (app.router.default), not mounted at "/".
A mount at "/" would fully match every path itself, before Starlette's router
gets a chance at its own 405 and slash-redirect handling for /api/v1/*,
/healthz, /docs or /openapi.json -- and the OpenAPI document is what the
agent skill and check_skill_matches_api.py read.

None of these need a database.
"""
import re

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import ADMIN_PASSWORD, DB_PASSWORD, PLAYER_PASSWORD, apply_test_env


@pytest.fixture()
def client(monkeypatch):
    apply_test_env(monkeypatch)
    from ro_admin.main import app
    return TestClient(app)


def _shipped():
    from ro_admin.main import WEB_DIR
    return WEB_DIR, sorted(p for p in WEB_DIR.rglob("*") if p.is_file())


def test_root_serves_the_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="app"' in r.text


def test_every_shipped_file_is_served_as_is(client):
    web_dir, files = _shipped()
    assert files
    for path in files:
        r = client.get("/" + path.relative_to(web_dir).as_posix())
        assert r.status_code == 200, path
        assert r.content == path.read_bytes(), path


@pytest.mark.parametrize("path, expected", [
    ("/openapi.json", 200),
    ("/docs", 200),
    ("/api/v1/auth/me", 401),
    ("/api/v1/characters", 401),
    ("/api/v1/characters/", 307),
    ("/api/v1/auth/login", 405),
])
def test_the_api_is_not_shadowed(client, path, expected):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == expected
    if expected == 405:
        assert "POST" in r.headers["allow"]


def test_healthz_is_not_shadowed(client):
    """200 with a database and 503 without one. Either way the API answered;
    the static UI fallback would have said 404."""
    r = client.get("/healthz")
    assert r.status_code in (200, 503)
    assert "database" in r.json()


def test_unknown_api_path_is_a_json_404(client):
    """A mistyped /api/... path is NOT answered by the API -- no route
    matches it either, so the UI fallback (UIFiles, set as app.router.default)
    handles it and raises HTTPException(404), which FastAPI renders as this
    JSON body. This test exists to enforce the no-web/404.html rule in
    main.py: with html=True, a 404.html would be served here instead, as
    HTML, to a client that only meant to hit the API."""
    r = client.get("/api/v1/nonexistent")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_websocket_to_an_unmatched_path_closes_cleanly(client):
    """StaticFiles.__call__ asserts scope["type"] == "http". Harmless while
    mounted at a fixed prefix, but UIFiles is now the router's fallback and
    sees every unmatched WebSocket connection too -- left alone, that
    assertion would raise AssertionError on the server, letting anyone who
    can reach the port fill the logs with tracebacks on demand. A client
    should see an ordinary disconnect, not that traceback."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/whatever"):
            pass


# The UI keeps an ADMIN token in localStorage, so XSS there means token
# theft. UIFiles (ro_admin/webui.py) stamps every response it serves with
# these; a global CSP would break /docs, which loads Swagger UI from a CDN.
UI_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
        "require-trusted-types-for 'script'; trusted-types 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-cache",
}


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_ui_responses_carry_defensive_headers(client, path):
    r = client.get(path)
    for name, value in UI_HEADERS.items():
        assert r.headers[name] == value, (path, name)


def test_docs_has_no_content_security_policy(client):
    """A blanket CSP would break Swagger UI, which loads from a CDN with an
    inline bootstrap script -- so the header is UIFiles-only, not global."""
    r = client.get("/docs")
    assert "content-security-policy" not in r.headers


def test_a_conditional_request_still_carries_the_headers(client):
    """StaticFiles.file_response() returns a NotModifiedResponse for a
    matching If-None-Match, not the FileResponse UIFiles.file_response()
    built -- both come back through the same override, so a 304 carries the
    same headers as the 200 that produced its ETag."""
    first = client.get("/index.html")
    etag = first.headers["etag"]
    second = client.get("/index.html", headers={"if-none-match": etag})
    assert second.status_code == 304
    for name, value in UI_HEADERS.items():
        assert second.headers[name] == value, name


# Shapes a leaked credential takes. A shipped file is readable by anyone who
# can reach the server, before they sign in.
SECRET_SHAPES = [
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # a JWT
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"RO_ADMIN_[A-Z_]*SECRET"),
    # Allows a quote before the colon too, so a JSON-shaped "password": "x"
    # is caught, not just the bare password: "x" the original pattern found.
    re.compile(r"(?i)password[\"']?\s*[:=]\s*[\"'][^\"']+[\"']"),
]


def test_no_shipped_file_carries_a_secret():
    _, files = _shipped()
    for path in files:
        # errors="replace" so a future binary asset (an icon, a font) cannot
        # crash the scan with a UnicodeDecodeError -- it should fail the
        # assertions below if it hides a secret, not skip the file entirely.
        text = path.read_text(encoding="utf-8", errors="replace")
        for shape in SECRET_SHAPES:
            assert not shape.search(text), (path, shape.pattern)
        # The test suite's own accounts, which are the lab's real ones.
        for value in (ADMIN_PASSWORD, PLAYER_PASSWORD, DB_PASSWORD):
            assert value not in text, path


def test_scripts_are_served_as_javascript(client):
    """A browser refuses to run an ES module served as anything else, and
    Python's MIME table is partly read from the host: on some Windows machines
    the registry maps .js to text/plain. webui.py registers text/javascript
    for .js at import time, and this pins the resulting header so such a host
    fails a test instead of serving a blank page."""
    web_dir, files = _shipped()
    scripts = [p for p in files if p.suffix == ".js"]
    assert scripts
    for path in scripts:
        r = client.get("/" + path.relative_to(web_dir).as_posix())
        assert r.headers["content-type"].split(";")[0] in (
            "text/javascript", "application/javascript",
        ), path


def test_stylesheet_is_served_as_css(client):
    """Same reasoning as test_scripts_are_served_as_javascript, for
    app.css: a host whose registry maps .css to something else (or to
    nothing at all) should fail a test rather than silently serve an
    unstyled page. webui.py registers text/css for .css at import time."""
    r = client.get("/app.css")
    assert r.headers["content-type"].split(";")[0] == "text/css"


# `import ... from "./x.js"`, including imports split over several lines.
IMPORT = re.compile(r"""^\s*import\s[^"']*["'](\./[^"']+)["']""", re.MULTILINE)
REFERENCE = re.compile(r"""(?:src|href)="([^"]+)\"""")


def test_every_reference_resolves_to_a_shipped_file():
    """A mistyped import does not fail one feature, it blanks the whole page:
    an ES module graph with one missing file never runs. Pinned here because
    nothing else would notice before a browser did."""
    web_dir, files = _shipped()
    shipped = {p.resolve() for p in files}
    page = (web_dir / "index.html").read_text(encoding="utf-8")
    references = REFERENCE.findall(page)
    assert "js/main.js" in references
    for reference in references:
        assert (web_dir / reference).resolve() in shipped, reference
    for path in files:
        if path.suffix != ".js":
            continue
        for reference in IMPORT.findall(path.read_text(encoding="utf-8")):
            assert (path.parent / reference).resolve() in shipped, (path.name, reference)
