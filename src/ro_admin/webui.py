"""Defensive response headers for the shipped web UI, and nothing else.

Applied only to the UI's static files, never globally: /docs serves Swagger
UI from a CDN with an inline bootstrap script, and a blanket
Content-Security-Policy would break it. The UI itself needs no CDN and no
inline script -- js/dom.js is the only DOM builder, and every <script> tag in
index.html names a same-origin file -- so it can run under a policy strict
enough that stealing the ADMIN token an operator keeps in localStorage takes
more than one XSS bug.

What each header buys:
  * Content-Security-Policy -- script-src/style-src 'self' stop an injected
    <script> or stylesheet from loading; object-src 'none' closes the
    plugin/Flash vector; base-uri 'none' stops a base tag from redirecting
    every relative script/style URL elsewhere; frame-ancestors 'none' stops
    the page being framed for clickjacking. require-trusted-types-for
    'script' is the runtime backstop behind scripts/check_no_innerhtml.py's
    static one: where supported, it turns an innerHTML/insertAdjacentHTML/
    document.write assignment from a silent XSS into a thrown TypeError,
    even if the static gate were ever bypassed or a dependency added one
    later.
  * X-Content-Type-Options: nosniff -- stops a browser from re-sniffing a
    misconfigured content-type into something it will execute.
  * Referrer-Policy: no-referrer -- the UI uses hash routing, so no secret
    ever sits in a URL fragment (fragments never leave the browser anyway),
    but query strings on outbound requests should not leak into a Referer
    header either.
  * Cache-Control: no-cache -- the ES modules import each other by
    unversioned URL (js/app.js imports js/api.js, js/api.js is imported by
    js/actions.js, and so on), so a heuristically cached old js/api.js could
    keep running next to a freshly fetched js/dossier.js after a deploy.
    no-cache forces revalidation on every load; the ETag StaticFiles already
    computes makes that revalidation a cheap 304, and these headers are
    attached to the 304 too.
"""
import mimetypes

from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketClose

# Some Windows hosts map .js to text/plain in the registry
# (HKEY_CLASSES_ROOT\.js), which mimetypes.guess_type() reads. A browser
# refuses to execute an ES module served with that content-type. Registering
# the correct type here, at import time and before the app serves a single
# request, makes the served type independent of the host's registry.
mimetypes.add_type("text/javascript", ".js")
# The same registry can map .css to something else entirely, or to nothing.
# This pin is not tidiness: UIFiles stamps every response with
# X-Content-Type-Options: nosniff (below), and a browser in standards mode
# refuses to apply a stylesheet served with anything other than text/css
# when nosniff is set -- app.css would silently not apply at all, on a host
# whose registry disagrees, without this.
mimetypes.add_type("text/css", ".css")

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
    "require-trusted-types-for 'script'; trusted-types 'none'"
)


class UIFiles(StaticFiles):
    """StaticFiles that stamps every response it produces with the headers
    above -- including a 304. StaticFiles.file_response() returns either a
    fresh FileResponse or, for a matching conditional request, a
    NotModifiedResponse; both are plain Response subclasses with a
    .headers mapping, and this override adds the same four headers to
    whichever one comes back.

    Also closes non-HTTP requests cleanly, because this is set as
    app.router.default (see main.py), not mounted at a fixed prefix: it now
    receives every scope the router itself didn't match, not just HTTP ones.
    """

    async def __call__(self, scope, receive, send):
        """StaticFiles.__call__ asserts scope["type"] == "http", which was
        harmless while this class was only ever mounted under a path -- a
        mount only ever forwards HTTP traffic. As the router's fallback it
        also receives a WebSocket connection to any path nothing else
        matched, and that assertion would raise AssertionError on the
        server, letting anyone who can reach the port fill the logs with
        tracebacks on demand. Closing the socket instead is exactly what
        Starlette's own Router.not_found does for the same case (see
        starlette/routing.py).

        Never sees a "lifespan" scope: Starlette's Router.app() (routing.py)
        handles that scope type itself and returns before ever dispatching
        to `default`, so only "http" and "websocket" reach here.
        """
        if scope["type"] != "http":
            await WebSocketClose()(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-cache"
        return response
