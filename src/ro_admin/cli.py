"""A thin CLI over the ro-admin API, for agents and scripts.

Deliberately dependency-free (stdlib only) so an operator can run it without
installing anything beyond this package.

Design note: this tool does NOT know what endpoints exist. `discover` reads
them from the server's generated OpenAPI document at runtime. That is the
whole point of generating the document rather than hand-maintaining one -- a
hardcoded endpoint list in here would drift the moment the API changed, and
take every agent using it along.

Configuration comes from the environment, never from arguments, so tokens do
not end up in shell history or process listings:

    RO_ADMIN_URL     default http://localhost:8000
    RO_ADMIN_TOKEN   a service token from scripts/mint_token.py

Exit codes, because a caller has to be able to tell these apart:

    0   the server answered 200 and its JSON was printed
    1   the server answered something other than 200; the body was printed
    2   this tool could not do what was asked -- no token, a malformed
        key=value, or a body it will not write to stdout (see describe_body).
        Never a silent 0: an empty-looking success is the one outcome this
        tool must never produce.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_PREFIX = "/api/v1"

# JWTs are three dot-separated base64url segments whose header always begins
# `eyJ` (base64 for `{"`). That prefix is the reliable signal, so the segment
# lengths are left unconstrained on purpose: a redaction filter should fail
# toward hiding too much, and an earlier version that required 8+ characters
# per segment silently passed short tokens straight through.
_TOKEN_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def redact(text: str) -> str:
    """Blank out anything shaped like a JWT.

    Applied to everything this tool prints. An agent's output is frequently a
    transcript, and a service token pasted into one is a credential leak that
    survives long after the session.
    """
    return _TOKEN_RE.sub("<REDACTED>", text)


def build_url(base: str, path: str, params: dict | None = None) -> str:
    """Join base and path, defaulting the /api/v1 prefix, and encode params."""
    path = "/" + path.lstrip("/")
    if not path.startswith(API_PREFIX) and not path.startswith("/openapi"):
        path = API_PREFIX + path
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def parse_params(pairs: list[str]) -> dict:
    """Turn ['char_id=1', 'limit=5'] into a dict. Splits on the FIRST '=' only."""
    out = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"expected key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key] = value
    return out


def is_json(content_type: str) -> bool:
    """Whether a Content-Type header names a JSON body.

    Decided from the header the server sent, not from whether json.loads
    happens to succeed on the bytes. Guessing at a body's meaning from its
    contents is precisely how this tool used to print 122,304 invisible gat
    bytes and call it a response.
    """
    kind = content_type.split(";", 1)[0].strip().lower()
    return kind == "application/json" or kind.endswith("+json")


def describe_body(url: str, status: int, body: bytes, content_type: str) -> str:
    """What to say instead of writing a non-JSON body to a terminal.

    `roadmin get maps/prontera/cells` used to print 122,304 bytes of gat cell
    types and exit 0. Types 0-6 are unprintable control characters, so what an
    operator -- or an agent reading a transcript -- saw was a blank line and a
    success, which reads as "this map has no cells". A false negative wearing
    the shape of a successful call is the exact failure this project exists to
    remove, so it must be impossible to reach from here.

    The byte count and a first-bytes preview are the whole point: they make it
    concrete that something arrived. "Cannot display" alone would still leave
    the reader unable to tell full from empty.
    """
    head = f"<{len(body)} bytes of {content_type or 'an unnamed content type'}> (HTTP {status})"
    seen = (
        "first bytes: " + " ".join(f"{b:02x}" for b in body[:16]) + ("..." if len(body) > 16 else "")
        if body else
        "the body really is empty -- zero bytes, not merely unprintable"
    )
    return "\n".join([
        head,
        seen,
        "",
        "Not printed: this is not JSON, and a binary body written to a terminal",
        "looks exactly like an empty response.",
        "",
        "Save it with curl, which can write it to a file:",
        "",
        '  curl -sS -o out.bin -H "Authorization: Bearer $RO_ADMIN_TOKEN" \\',
        f'    "{url}"',
    ])


def _request(url: str, token: str | None) -> tuple[int, bytes, str]:
    """Status, raw body, and the declared content type.

    Bytes rather than str: the caller decides whether this is text at all, and
    .decode() on a binary body either raises or -- worse, for gat data, which
    is valid UTF-8 -- succeeds and yields something unprintable.
    """
    req = urllib.request.Request(url)
    if token:
        # Strip whitespace before it reaches the header. On Windows, print()
        # emits \r\n and shell command substitution removes only the \n, so a
        # token captured with $(mint_token.py ...) arrives with a trailing \r
        # -- which makes urllib raise on an invalid header rather than saying
        # anything about the token.
        req.add_header("Authorization", f"Bearer {token.strip()}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def _discover(base: str, token: str | None) -> int:
    status, raw, _ = _request(build_url(base, "/openapi.json"), token)
    body = raw.decode(errors="replace")
    if status != 200:
        print(redact(f"could not read the API description ({status}): {body[:300]}"))
        return 1
    spec = json.loads(body)
    print(f"{spec['info']['title']} v{spec['info']['version']} at {base}\n")
    for path in sorted(spec["paths"]):
        for method, op in sorted(spec["paths"][path].items()):
            print(f"  {method.upper():6} {path}")
            if op.get("summary"):
                print(f"         {op['summary']}")
            for param in op.get("parameters", []):
                required = " (required)" if param.get("required") else ""
                print(f"           - {param['name']}{required}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="roadmin", description="Query a ro-admin server."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="list the endpoints this server actually offers")
    get = sub.add_parser("get", help="GET an endpoint")
    get.add_argument("path", help="e.g. /logs/timeline (the /api/v1 prefix is optional)")
    get.add_argument("params", nargs="*", help="key=value query parameters")

    args = parser.parse_args(argv)
    base = os.environ.get("RO_ADMIN_URL", "http://localhost:8000")
    token = os.environ.get("RO_ADMIN_TOKEN")

    if args.command == "discover":
        return _discover(base, token)

    if not token:
        print("RO_ADMIN_TOKEN is not set. Mint one with scripts/mint_token.py.")
        return 2

    try:
        params = parse_params(args.params)
    except ValueError as exc:
        print(str(exc))
        return 2

    url = build_url(base, args.path, params)
    status, raw, content_type = _request(url, token)

    if not is_json(content_type):
        print(redact(describe_body(url, status, raw, content_type)))
        # 2, not 0. The request succeeded; this tool did not hand over the
        # bytes, and a script must not carry on as though it had. Exiting 0
        # would put a human-readable notice where a caller redirecting to a
        # file expects a payload, and 2 is already what this CLI returns for
        # "you asked for something it cannot do" -- no token, bad key=value.
        return 2

    body = raw.decode(errors="replace")
    try:
        body = json.dumps(json.loads(body), indent=2, default=str)
    except json.JSONDecodeError:
        pass
    print(redact(body))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
