"""Write the generated OpenAPI document to stdout.

Generated, never hand-maintained: the shipped agent skill discovers endpoints
from this document, so a hand-edited copy would drift and take the skill with it.
"""
import json
import os

os.environ.setdefault("RO_ADMIN_JWT_SECRET", "x" * 32)
os.environ.setdefault("RO_ADMIN_DB_USER", "placeholder")
os.environ.setdefault("RO_ADMIN_DB_PASSWORD", "placeholder")

from ro_admin.main import app  # noqa: E402

print(json.dumps(app.openapi(), indent=2))
