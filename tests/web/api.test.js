import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, createApi, errorMessage } from "../../src/ro_admin/web/js/api.js";

function respond(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => {
      if (body === undefined) throw new SyntaxError("Unexpected end of JSON input");
      return body;
    },
  };
}

// A proxy, captive portal or gateway error page: 2xx or 5xx, but the body is
// HTML rather than JSON, so `.json()` fails differently but no less fatally.
function respondHtml(status) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => {
      throw new SyntaxError("Unexpected token '<', \"<html>...\" is not valid JSON");
    },
  };
}

function recorder(status, body) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return respond(status, body);
  };
  return { calls, fetchImpl };
}

test("a sentence detail is shown as it is", () => {
  assert.equal(errorMessage({ detail: "no character with id 5" }, 404), "no character with id 5");
});

test("a validation list becomes one message naming each field", () => {
  const body = {
    detail: [
      { loc: ["body", "adjust_zeny", "delta"], msg: "Value error, delta must not be 0" },
      { loc: ["query", "limit"], msg: "Input should be less than or equal to 500" },
    ],
  };
  assert.equal(
    errorMessage(body, 422),
    "adjust_zeny.delta: Value error, delta must not be 0; limit: Input should be less than or equal to 500",
  );
});

test("an empty validation list falls back to a plain status message", () => {
  assert.equal(errorMessage({ detail: [] }, 422), "request failed with status 422");
});

test("only loc[0] is stripped, and only when it names a request part", () => {
  const body = { detail: [{ loc: ["body", "config", "path"], msg: "required" }] };
  assert.equal(errorMessage(body, 422), "config.path: required");
});

test("a numeric loc segment joins like any other", () => {
  const body = { detail: [{ loc: ["body", "items", 0, "amount"], msg: "must be positive" }] };
  assert.equal(errorMessage(body, 422), "items.0.amount: must be positive");
});

test("a problem with no msg is skipped, never printed as undefined", () => {
  const body = {
    detail: [
      { loc: ["body", "x"], msg: "required" },
      { loc: ["body", "y"] },
    ],
  };
  assert.equal(errorMessage(body, 422), "x: required");
});

test("a 5xx with no JSON body names the log, not a bare status", () => {
  assert.equal(errorMessage(null, 502), "the server failed (502) without saying why; its log has the detail");
});

test("a 4xx with no body keeps the plain status message", () => {
  assert.equal(errorMessage(null, 404), "request failed with status 404");
});

test("GET sends the token and only the params that are set", async () => {
  const { calls, fetchImpl } = recorder(200, { items: [] });
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  await api.get("characters", { name_prefix: "Ka", limit: 10, online: undefined, account_id: null });
  assert.equal(calls[0].url, "/api/v1/characters?name_prefix=Ka&limit=10");
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[0].init.headers.Authorization, "Bearer t0k");
  assert.equal(calls[0].init.body, undefined);
});

test("a null params object is tolerated", async () => {
  const { calls, fetchImpl } = recorder(200, { items: [] });
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  await api.get("characters", null);
  assert.equal(calls[0].url, "/api/v1/characters");
});

test("POST sends JSON and returns the body", async () => {
  const { calls, fetchImpl } = recorder(202, { id: 1, status: "pending" });
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  const row = await api.post("commands", { action: "sync_character", char_id: 7 });
  assert.equal(row.status, "pending");
  assert.equal(calls[0].url, "/api/v1/commands");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].init.body), { action: "sync_character", char_id: 7 });
});

test("every request goes out with no cookies and no caching", async () => {
  const { calls, fetchImpl } = recorder(200, { items: [] });
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  await api.get("characters");
  assert.equal(calls[0].init.credentials, "omit");
  assert.equal(calls[0].init.cache, "no-store");
});

test("a refusal is an ApiError carrying the API's own words", async () => {
  const reason = "overlay not installed: run overlay/schema.sql against this database";
  const { fetchImpl } = recorder(409, { detail: reason });
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  await assert.rejects(
    api.post("commands", {}),
    (error) => error instanceof ApiError && error.status === 409 && error.message === reason,
  );
});

test("a 401 on a signed-in call ends the session, with the reason", async () => {
  const { fetchImpl } = recorder(401, { detail: "invalid token" });
  const reasons = [];
  const api = createApi({ fetchImpl, getToken: () => "expired", onUnauthorized: (r) => reasons.push(r) });
  await assert.rejects(api.get("auth/me"), (error) => error.status === 401);
  assert.deepEqual(reasons, ["invalid token"]);
});

test("a 401 without a token is a failed sign-in, not an ended session", async () => {
  const { calls, fetchImpl } = recorder(401, { detail: "invalid credentials" });
  let ended = false;
  const api = createApi({ fetchImpl, getToken: () => null, onUnauthorized: () => { ended = true; } });
  await assert.rejects(api.post("auth/login", {}), (error) => error.message === "invalid credentials");
  assert.equal(ended, false);
  assert.equal(calls[0].init.headers.Authorization, undefined);
});

test("auth: false sends no Authorization header even when a token exists", async () => {
  const { calls, fetchImpl } = recorder(200, { token: "abc" });
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  await api.post("auth/login", { userid: "x", password: "y" }, { auth: false });
  assert.equal(calls[0].init.headers.Authorization, undefined);
});

test("a 401 from an auth: false call never ends a session", async () => {
  const { fetchImpl } = recorder(401, { detail: "invalid credentials" });
  let ended = false;
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized: () => { ended = true; } });
  await assert.rejects(api.post("auth/login", {}, { auth: false }), (error) => error.status === 401);
  assert.equal(ended, false);
});

test("a 401 whose token has since changed does not end the new session", async () => {
  let current = "old";
  const fetchImpl = async () => {
    current = "new"; // a fresh sign-in lands while this request is still in flight
    return respond(401, { detail: "invalid token" });
  };
  const reasons = [];
  const api = createApi({ fetchImpl, getToken: () => current, onUnauthorized: (r) => reasons.push(r) });
  await assert.rejects(api.get("auth/me"), (error) => error.status === 401);
  assert.deepEqual(reasons, []);
});

test("an unreachable server is a status-0 ApiError, not a crash", async () => {
  const api = createApi({
    fetchImpl: async () => { throw new TypeError("Failed to fetch"); },
    getToken: () => null,
    onUnauthorized() {},
  });
  await assert.rejects(
    api.get("auth/me"),
    (error) => error instanceof ApiError && error.status === 0 && error.message === "could not reach the server",
  );
});

test("a GET network failure never claims to maybe have applied", async () => {
  const api = createApi({
    fetchImpl: async () => { throw new TypeError("Failed to fetch"); },
    getToken: () => null,
    onUnauthorized() {},
  });
  await assert.rejects(
    api.get("auth/me"),
    (error) => error instanceof ApiError && error.status === 0 && error.maybeApplied === false,
  );
});

test("a POST network failure says it may or may not have been applied", async () => {
  const api = createApi({
    fetchImpl: async () => { throw new TypeError("Failed to fetch"); },
    getToken: () => "t0k",
    onUnauthorized() {},
  });
  await assert.rejects(
    api.post("commands", {}),
    (error) => error instanceof ApiError
      && error.status === 0
      && error.maybeApplied === true
      && /may or may not have been applied/.test(error.message)
      && /Check History/.test(error.message),
  );
});

test("a POST that gets a 502 with an HTML body may have already run", async () => {
  const fetchImpl = async () => respondHtml(502);
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  await assert.rejects(
    api.post("commands", {}),
    (error) => error instanceof ApiError
      && error.status === 502
      && error.maybeApplied === true
      && error.message
        === "the server failed (502) without saying why; its log has the detail."
          + " This may or may not have been applied; check History before retrying.",
  );
});

test("a POST that gets a 202 with an unparsable body may have already queued", async () => {
  const fetchImpl = async () => respondHtml(202);
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  await assert.rejects(
    api.post("commands", {}),
    (error) => error instanceof ApiError
      && error.status === 202
      && error.maybeApplied === true
      && error.message
        === "the server received the request, but its reply could not be read; check History before retrying.",
  );
});

test("a 2xx with an empty (non-JSON) body is an error, not null", async () => {
  const { fetchImpl } = recorder(200, undefined);
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  await assert.rejects(
    api.get("system/health"),
    (error) => error instanceof ApiError
      && error.status === 200
      && error.message === "the server's answer was not JSON (is a proxy in the way?)",
  );
});

test("a 2xx with an HTML body (a captive portal) is an error too", async () => {
  const fetchImpl = async () => respondHtml(200);
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  await assert.rejects(
    api.get("system/health"),
    (error) => error instanceof ApiError
      && error.message === "the server's answer was not JSON (is a proxy in the way?)",
  );
});

test("opts cannot smuggle its own params or body over the real ones", async () => {
  const { calls, fetchImpl } = recorder(200, { items: [] });
  const api = createApi({ fetchImpl, getToken: () => "t0k", onUnauthorized() {} });
  await api.get("characters", { name_prefix: "Ka" }, { auth: false, params: { name_prefix: "evil" } });
  assert.equal(calls[0].url, "/api/v1/characters?name_prefix=Ka");
  assert.equal(calls[0].init.headers.Authorization, undefined);
});

test("a path outside the allowed shape is a programming error, not an ApiError", async () => {
  const api = createApi({ fetchImpl: async () => respond(200, {}), getToken: () => null, onUnauthorized() {} });
  for (const path of ["..", "a?b", "a#b", "a%2Fb"]) {
    await assert.rejects(api.get(path), (error) => error instanceof Error && !(error instanceof ApiError));
  }
});
