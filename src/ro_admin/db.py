"""Database access. Parameterized queries only.

Configured entirely by environment. The service never learns a filesystem
path, a container name, or the location of an rAthena source tree -- those
couplings are what made the predecessor unable to run against anyone else's
install.

Opens a connection per query. That is not what a busy service should do, and
it is deliberate for this slice: pooling is an optimisation with its own
failure modes (stale handles, fork safety, exhaustion under load) and there is
nothing yet to tune it against.
"""
from typing import Any, Sequence

import pymysql
from pymysql.cursors import DictCursor

from ro_admin.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self._settings = settings

    def _connect(self) -> pymysql.connections.Connection:
        s = self._settings
        return pymysql.connect(
            host=s.db_host,
            port=s.db_port,
            user=s.db_user,
            password=s.db_password,
            database=s.db_name,
            cursorclass=DictCursor,
            autocommit=True,
        )

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall())
