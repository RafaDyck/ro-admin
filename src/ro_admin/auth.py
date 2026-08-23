"""Authentication against rAthena's `login` table, plus token issue/verify.

rAthena stores `user_pass` as plaintext or MD5 depending on
`use_MD5_passwords` in conf/inter_athena.conf. Both are supported because
both exist in the wild -- not because either is a good choice. Panel-only
accounts with a modern KDF are a separate concern and deliberately out of
scope for this slice.
"""
import hashlib
import time
from typing import Any

import jwt

from ro_admin.permissions import Level


def verify_password(supplied: str, stored: str, md5: bool) -> bool:
    if md5:
        return hashlib.md5(supplied.encode()).hexdigest().lower() == stored.strip().lower()
    return supplied == stored


def issue_token(secret: str, subject: str, level: Level, ttl_seconds: int) -> str:
    now = int(time.time())
    payload = {"sub": subject, "lvl": int(level), "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=["HS256"])


def issue_service_token(secret: str, name: str, scopes: list, ttl_seconds: int) -> str:
    """A non-interactive token carrying explicit permissions.

    Scopes are a ceiling. A service token is never widened by the level of
    whoever minted it -- that is the whole point of handing one to an agent.
    """
    now = int(time.time())
    payload = {
        "sub": name,
        "typ": "service",
        "scopes": [str(s) for s in scopes],
        "lvl": int(Level.PLAYER),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")
