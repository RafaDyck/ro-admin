"""Application assembly.

There are deliberately no debug routes. The predecessor registered an
unauthenticated debug blueprint whose create-admin endpoint could mint an
admin account; a debug surface registered unconditionally will eventually
ship.
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ro_admin.config import Settings
from ro_admin.db import Database

from ro_admin.routers import auth, items, logs, system

app = FastAPI(
    title="ro-admin",
    version="0.1.0",
    description="Administration API for rAthena servers. Tier 0: database only.",
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
