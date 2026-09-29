/**
 * The UI's only road to the server: the same HTTP API the CLI and the agent
 * skill use, with the same bearer token. There is no other, so the UI cannot
 * do anything the API would refuse.
 */
export class ApiError extends Error {
  constructor(status, message, maybeApplied = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    // True only for a non-GET request whose outcome on the server is
    // unknown: no answer reached us at all, or the server answered with a
    // 5xx after the request had already arrived. A write in that state may
    // have already run, so a naive retry can grant an item or move zeny
    // twice. GET is idempotent and a clean 4xx never leaves this in doubt.
    this.maybeApplied = maybeApplied;
  }
}

function isMaybeApplied(method, status) {
  return method !== "GET" && (status === 0 || status >= 500);
}

const NO_ANSWER =
  "No answer from the server, so this may or may not have been applied. Check History before retrying.";

// FastAPI's own loc[0] names, for the part of the request each validation
// problem was found in. Stripped only there -- a field three levels down
// that happens to be named "path" must survive.
const STRIPPED_LOC_PREFIXES = new Set(["body", "query", "path", "header", "cookie"]);

function fallbackMessage(status) {
  return status >= 500
    ? `the server failed (${status}) without saying why; its log has the detail`
    : `request failed with status ${status}`;
}

/**
 * One displayable sentence from either refusal shape. Most refusals are
 * {"detail": "a sentence"}; validation failures (422) are
 * {"detail": [{"loc": [...], "msg": "..."}]} -- a LIST of dicts. Treating the
 * second like the first prints "[object Object]", and that difference has
 * already caught one implementer.
 *
 * Each problem's `input` is never read here, deliberately: on /auth/login a
 * validation failure's `input` echoes the password field straight back.
 */
export function errorMessage(body, status) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .filter((problem) => problem?.msg !== undefined)
      .map((problem) => {
        const loc = problem.loc ?? [];
        const [head, ...rest] = loc;
        const where = (STRIPPED_LOC_PREFIXES.has(head) ? rest : loc).join(".");
        return where ? `${where}: ${problem.msg}` : String(problem.msg);
      });
    if (parts.length > 0) return parts.join("; ");
  }
  return fallbackMessage(status);
}

function queryString(params) {
  const pairs = Object.entries(params ?? {})
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([key, value]) => [key, String(value)]);
  return pairs.length ? `?${new URLSearchParams(pairs)}` : "";
}

const PATH_RE = /^[A-Za-z0-9_@.-]+(\/[A-Za-z0-9_@.-]+)*$/;

// A programming error, not a refusal from the server: every current caller
// passes a literal or a `^\d+$` id, but this is the one place a stray value
// would reach a URL, so it is checked rather than trusted.
function assertValidPath(path) {
  const segments = String(path).split("/");
  const shaped = PATH_RE.test(path);
  const traversal = segments.some((segment) => segment === "." || segment === "..");
  if (!shaped || traversal) {
    throw new Error(`api: refusing to request path ${JSON.stringify(path)}`);
  }
}

export function createApi({
  fetchImpl = (...args) => fetch(...args),
  getToken,
  onUnauthorized = () => {},
}) {
  async function request(method, path, { params, body, auth = true } = {}) {
    assertValidPath(path);
    const token = auth ? getToken() : null;
    const headers = { Accept: "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";

    let response;
    try {
      response = await fetchImpl(`/api/v1/${path}${queryString(params)}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        // Bearer-only: cookies are scoped by host, not port, and this API
        // and whatever else runs on this machine's other ports should never
        // share them.
        credentials: "omit",
        // Every response can carry PII: characters, accounts, log lines.
        cache: "no-store",
      });
    } catch {
      const applied = isMaybeApplied(method, 0);
      throw new ApiError(0, applied ? NO_ANSWER : "could not reach the server", applied);
    }

    if (response.ok) {
      try {
        return await response.json();
      } catch {
        // A 2xx means the request arrived; for a write, arrived can mean
        // already queued (POST commands answers 202 once it is). A dropped
        // connection can still land here after that, and "not JSON" must
        // not read as "safe to retry" for a write whose outcome we can no
        // longer see. GET is idempotent, so it keeps the proxy wording --
        // the only realistic cause for a GET is a captive portal or gateway
        // page returning 2xx with an HTML body.
        if (method === "GET") {
          throw new ApiError(
            response.status,
            "the server's answer was not JSON (is a proxy in the way?)",
          );
        }
        throw new ApiError(
          response.status,
          "the server received the request, but its reply could not be read; check History before retrying.",
          true,
        );
      }
    }

    const payload = await response.json().catch(() => null);
    const applied = isMaybeApplied(method, response.status);
    let message = errorMessage(payload, response.status);
    if (applied) {
      // The flag alone is invisible in the UI unless something reads it;
      // folding the warning into the message means every caller that just
      // shows `error.message` still tells the operator to check History.
      message = `${message}. This may or may not have been applied; check History before retrying.`;
    }
    // Only a call that SENT a token, and whose token is still the current
    // one, can end a session. A slow request signed with a token the
    // operator has since replaced -- a fresh sign-in landed first -- must
    // not sign out the new session, and a 401 from the sign-in form means
    // wrong credentials, reported where it happened, never here.
    if (response.status === 401 && token && getToken() === token) onUnauthorized(message);
    throw new ApiError(response.status, message, applied);
  }

  return {
    // Only `auth` is taken from `opts`: `params`/`body` are never something
    // a caller's own object should silently overwrite through a spread.
    get: (path, params, opts) => request("GET", path, { params, auth: opts?.auth }),
    post: (path, body, opts) => request("POST", path, { body, auth: opts?.auth }),
  };
}
