"""Mint a service token for a script or agent.

Usage:
    python scripts/mint_token.py log-reader logs.read --days 30
"""
import argparse

from ro_admin.auth import issue_service_token
from ro_admin.config import Settings
from ro_admin.permissions import Permission

parser = argparse.ArgumentParser()
parser.add_argument("name")
parser.add_argument("scopes", nargs="+")
parser.add_argument("--days", type=int, default=30)
args = parser.parse_args()

settings = Settings()
print(issue_service_token(
    secret=settings.jwt_secret,
    name=args.name,
    scopes=[Permission(s) for s in args.scopes],
    ttl_seconds=args.days * 86400,
))
