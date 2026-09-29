"""Application assembly.

There are deliberately no debug routes. The predecessor registered an
unauthenticated debug blueprint whose create-admin endpoint could mint an
admin account; a debug surface registered unconditionally will eventually
ship.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.webui import UIFiles

from ro_admin.routers import accounts, auth, characters, commands, items, logs, maps, system

# The web UI's files. Shipped inside the package (see pyproject.toml), so a
# wheel install and the container both carry them, with no build step and no
# Node at install or run time.
WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(
    title="ro-admin",
    version="0.1.0",
    # Ships in the generated OpenAPI document, which is what a consuming agent
    # reads to find out what this install is. "Tier 0: database only" was true
    # until the overlay landed; leaving it would have been a false claim in the
    # one document the skill treats as authoritative.
    description=(
        "Administration API for rAthena servers. Tier 0 reads the database. "
        "Tier 1, where the overlay script is installed, applies item grants "
        "and zeny adjustments inside the running game -- see overlay/README.md."
    ),
)

@app.on_event("startup")
def verify_configuration() -> None:
    """Fail at boot if the service is misconfigured, not on the first request.

    Settings are otherwise only constructed inside a per-request dependency, so
    a container with no RO_ADMIN_JWT_SECRET started happily, reported healthy,
    and served /openapi.json -- then failed on the first real call. An operator
    would have seen a green container and a broken deployment.

    Found by containerising the service and actually running it without a
    secret. Constructing Settings() here turns that into a refusal to start.
    """
    Settings()


app.include_router(auth.router)
app.include_router(logs.router)
app.include_router(system.router)
app.include_router(items.router)
app.include_router(commands.router)
app.include_router(accounts.router)
app.include_router(characters.router)
app.include_router(maps.router)


@app.get("/healthz", tags=["system"], summary="Liveness and database reachability")
def healthz() -> JSONResponse:
    """Unauthenticated probe that actually exercises the database.

    Deliberately NOT a bare 200. A check that cannot fail certifies nothing:
    the first container healthcheck here probed /openapi.json, which needs no
    configuration, so a container with no database credentials reported healthy
    while being unable to serve a single real request.

    Returns 503 when the database is unreachable, so orchestrators see the
    difference between "process running" and "service working".
    """
    try:
        Database(Settings()).query("SELECT 1 AS ok")
    except Exception as exc:  # noqa: BLE001 - any failure to reach the DB is unhealthy
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": f"{type(exc).__name__}"},
        )
    return JSONResponse(status_code=200, content={"status": "ok", "database": "ok"})


# Set as the router's fallback, not mounted at "/". A mount fully matches
# every path itself, so Starlette's router never reaches its OWN 405
# (wrong method on a real route) or 307 (missing trailing slash) handling
# for anything under /api/v1 or /healthz -- both silently regressed to a
# bare 404 under a mount, measured before fixing this (see
# tests/test_web_ui.py::test_the_api_is_not_shadowed):
#   GET /api/v1/auth/login   405 Allow: POST  -> 404 under a mount
#   GET /api/v1/characters/  307              -> 404 under a mount
#   GET /healthz/            307              -> 404 under a mount
# Router.default is only invoked after a full match, a method-mismatch
# partial match, and a slash-redirect have all failed -- exactly the
# fallback semantics a catch-all UI route needs, and it needs no particular
# position in this file to get them.
#
# The UI is one more client of this API: it reaches the server over HTTP like
# the CLI and the skill do, which is what makes a privileged UI path
# impossible rather than merely avoided.
#
# Never add a web/404.html. With html=True a request for a file that does
# not exist falls through to it, so it would be served -- as HTML -- to an
# API client that mistyped an /api/... path. The UI uses hash routing, so
# the browser never asks the server for a route that doesn't exist as a
# real file, and needs no fallback page of its own.
app.router.default = UIFiles(directory=WEB_DIR, html=True)
