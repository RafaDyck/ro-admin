import hashlib

import pytest

from ro_admin.auth import verify_password, issue_token, decode_token
from ro_admin.permissions import Level

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, PLAYER_PASSWORD, PLAYER_USER,
    apply_test_env,
)


def test_plaintext_password_matches():
    assert verify_password(ADMIN_PASSWORD, ADMIN_PASSWORD, md5=False)
    assert not verify_password("wrong", ADMIN_PASSWORD, md5=False)


def test_md5_password_matches():
    stored = hashlib.md5(ADMIN_PASSWORD.encode()).hexdigest()
    assert verify_password(ADMIN_PASSWORD, stored, md5=True)
    assert not verify_password("wrong", stored, md5=True)


def test_token_round_trip():
    token = issue_token(
        secret="x" * 32, subject=ADMIN_USER, level=Level.ADMIN, ttl_seconds=60
    )
    claims = decode_token(token, secret="x" * 32)
    assert claims["sub"] == ADMIN_USER
    assert claims["lvl"] == int(Level.ADMIN)


def test_token_rejected_under_wrong_secret():
    token = issue_token(secret="x" * 32, subject="a", level=Level.STAFF, ttl_seconds=60)
    with pytest.raises(Exception):
        decode_token(token, secret="y" * 32)


def test_expired_token_is_rejected():
    token = issue_token(secret="x" * 32, subject="a", level=Level.STAFF, ttl_seconds=-1)
    with pytest.raises(Exception):
        decode_token(token, secret="x" * 32)
