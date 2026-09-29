import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { fakeApi, installDom, memoryStorage, submit, tick } from "./helpers.js";
import { ApiError, createApi } from "../../src/ro_admin/web/js/api.js";
import {
  TOKEN_STORAGE_KEY, createSession, renderLogin, sessionChangeFromStorage,
} from "../../src/ro_admin/web/js/session.js";

const refusingStorage = {
  getItem() { throw new Error("SecurityError"); },
  setItem() { throw new Error("QuotaExceededError"); },
  removeItem() { throw new Error("SecurityError"); },
};

beforeEach(() => installDom());

test("a saved token survives a restart", () => {
  const storage = memoryStorage();
  createSession(storage).save("abc");
  assert.equal(createSession(storage).token(), "abc");
});

test("signOut forgets it, for this tab and the next", () => {
  const storage = memoryStorage();
  const session = createSession(storage);
  session.save("abc");
  session.signOut();
  assert.equal(session.token(), null);
  assert.equal(createSession(storage).token(), null);
});

test("signOut removes the key even if another tab saved a different token", () => {
  // The operator asked THIS BROWSER to sign out. If signOut() deferred to a
  // newer token the way expire() does, the button would silently do
  // nothing in a tab that isn't the newest -- and worse, this tab's own
  // next token() call would re-read storage and adopt the other tab's
  // token, signing this tab back in right after "signing out".
  const storage = memoryStorage();
  const tabA = createSession(storage);
  tabA.save("abc");
  const tabB = createSession(storage);
  tabB.save("def");
  tabA.signOut();
  assert.equal(tabA.token(), null);
  assert.equal(createSession(storage).token(), null);
});

test("expire does not remove a newer token another tab already saved", () => {
  // A tab's own session can outlive its usefulness -- e.g. its 401 handler
  // fires after a fresh sign-in in another tab already replaced the stored
  // token. That newer token must survive this tab's expire().
  const storage = memoryStorage();
  const tabA = createSession(storage);
  tabA.save("abc");
  const tabB = createSession(storage);
  tabB.save("def");
  tabA.expire();
  assert.equal(createSession(storage).token(), "def");
  assert.equal(tabB.token(), "def");
});

test("expire does nothing for a tab that never held a token of its own", () => {
  // A tab that never signed in and never called token() has `current ===
  // null` in memory. expire() must not fall through to storage to find
  // something to remove -- that would let a bystander tab expire a session
  // it never had a stake in, just because it happened to read storage at
  // the wrong moment.
  const storage = memoryStorage();
  const other = createSession(storage);
  other.save("def");
  const bystander = createSession(storage);
  bystander.expire();
  assert.equal(createSession(storage).token(), "def");
  assert.equal(other.token(), "def");
});

test("storage that throws still gives this tab a session", () => {
  const session = createSession(refusingStorage);
  session.save("abc");
  assert.equal(session.token(), "abc");
  session.signOut();
  assert.equal(session.token(), null);
});

test("no storage at all behaves the same", () => {
  const session = createSession(null);
  assert.equal(session.token(), null);
  session.save("abc");
  assert.equal(session.token(), "abc");
});

// sessionChangeFromStorage: the cross-tab decision app.js's `storage`
// listener acts on. Moved here from tests/web/app.test.js (session.js is
// where the function actually lives) when app.js could not yet be
// imported for its side effects; kept here now that it can, because the
// decision itself needs no live page.
function storageEvent({ key, newValue }) {
  return { key, newValue };
}

test("a different key is never a session change", () => {
  const event = storageEvent({ key: "some-other-key", newValue: null });
  assert.equal(sessionChangeFromStorage(event, "abc", memoryStorage()), null);
});

test("the stored token being removed ends the session, for a tab that held one", () => {
  const event = storageEvent({ key: TOKEN_STORAGE_KEY, newValue: null });
  assert.equal(sessionChangeFromStorage(event, "abc", memoryStorage()), "ended");
});

test("the stored token being removed is not a change for a tab with no session of its own", () => {
  const event = storageEvent({ key: TOKEN_STORAGE_KEY, newValue: null });
  assert.equal(sessionChangeFromStorage(event, null, memoryStorage()), null);
});

test("a different token being stored is a replacement", () => {
  const event = storageEvent({ key: TOKEN_STORAGE_KEY, newValue: "def" });
  assert.equal(sessionChangeFromStorage(event, "abc", memoryStorage()), "replaced");
});

test("a different token being stored is a replacement even for a tab on the login form", () => {
  // The browser holds ONE session, shared by every tab: a tab with no
  // session of its own (showing the login form) must still pick up a
  // fresh sign-in from another tab, not ignore it as someone else's.
  const event = storageEvent({ key: TOKEN_STORAGE_KEY, newValue: "new-token" });
  assert.equal(sessionChangeFromStorage(event, null, memoryStorage()), "replaced");
});

test("the token this tab is already using being stored again is not a change", () => {
  const event = storageEvent({ key: TOKEN_STORAGE_KEY, newValue: "abc" });
  assert.equal(sessionChangeFromStorage(event, "abc", memoryStorage()), null);
});

// localStorage.clear(), seen from another tab, fires a `storage` event with
// EVERY field null -- key, oldValue and newValue alike -- regardless of
// which keys were actually cleared. sessionChangeFromStorage must consult
// storage itself to learn whether this key survived.
test("key === null (localStorage.clear()) and the token is gone: ended", () => {
  const event = storageEvent({ key: null, newValue: null });
  assert.equal(sessionChangeFromStorage(event, "abc", memoryStorage()), "ended");
});

test("key === null and the token was never this tab's: not a change", () => {
  const event = storageEvent({ key: null, newValue: null });
  assert.equal(sessionChangeFromStorage(event, null, memoryStorage()), null);
});

test("key === null but the token survived, unchanged: not a change", () => {
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "abc");
  const event = storageEvent({ key: null, newValue: null });
  assert.equal(sessionChangeFromStorage(event, "abc", storage), null);
});

test("key === null but a different token survived: a replacement", () => {
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "def");
  const event = storageEvent({ key: null, newValue: null });
  assert.equal(sessionChangeFromStorage(event, "abc", storage), "replaced");
});

test("key === null and storage refuses to answer: treated the same as gone", () => {
  const event = storageEvent({ key: null, newValue: null });
  assert.equal(sessionChangeFromStorage(event, "abc", refusingStorage), "ended");
});

function signIn(root, userid, password) {
  root.querySelector('input[name="userid"]').value = userid;
  root.querySelector('input[name="password"]').value = password;
  submit(root.querySelector("form"));
}

const loginApi = () =>
  fakeApi({
    "POST auth/login": (body) =>
      body.userid === "gm" && body.password === "pw"
        ? { access_token: "tok", token_type: "bearer", level: 99 }
        : new ApiError(401, "invalid credentials"),
  });

test("signing in saves the token and moves on", async () => {
  const session = createSession(memoryStorage());
  let signedIn = 0;
  renderLogin(document.body, { api: loginApi(), session, onLoggedIn: () => { signedIn += 1; } });
  signIn(document.body, "gm", "pw");
  await tick();
  assert.equal(session.token(), "tok");
  assert.equal(signedIn, 1);
});

test("the form stays disabled after a successful submit, so a race can't sign in twice", async () => {
  const session = createSession(memoryStorage());
  renderLogin(document.body, { api: loginApi(), session, onLoggedIn() {} });
  signIn(document.body, "gm", "pw");
  await tick();
  const button = document.querySelector('button[type="submit"]');
  assert.equal(button.disabled, true);
});

test("sign-in never sends a bearer token", async () => {
  // fakeApi drops the third argument, so this needs a real spy to see it.
  const session = createSession(memoryStorage());
  const calls = [];
  const api = {
    post: async (path, body, opts) => {
      calls.push({ path, body, opts });
      return { access_token: "tok", token_type: "bearer", level: 99 };
    },
  };
  renderLogin(document.body, { api, session, onLoggedIn() {} });
  signIn(document.body, "gm", "pw");
  await tick();
  assert.deepEqual(calls, [
    { path: "auth/login", body: { userid: "gm", password: "pw" }, opts: { auth: false } },
  ]);
});

test("sign-in, end to end: no bearer sent for the login call, and its 401 never ends the session", async () => {
  // Uses the real createApi (not a fake) so the `auth: false` opt is
  // actually exercised through api.js's own header-building and
  // onUnauthorized logic, with a stale token already in storage -- the
  // shape a browser is in after a previous session expired.
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "stale-token");
  const session = createSession(storage);
  const calls = [];
  let unauthorizedCount = 0;
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return {
      ok: false,
      status: 401,
      json: async () => ({ detail: "invalid credentials" }),
    };
  };
  const api = createApi({
    fetchImpl,
    getToken: () => session.token(),
    onUnauthorized: () => { unauthorizedCount += 1; },
  });
  renderLogin(document.body, { api, session, onLoggedIn() {} });
  signIn(document.body, "gm", "wrong");
  await tick();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/v1/auth/login");
  assert.equal(calls[0].init.headers.Authorization, undefined);
  assert.match(document.body.textContent, /invalid credentials/);
  assert.equal(unauthorizedCount, 0);
  // The failed attempt never touched the session that was already there.
  assert.equal(session.token(), "stale-token");
});

test("a refusal is shown in the API's words, and nothing is saved", async () => {
  const session = createSession(memoryStorage());
  let signedIn = 0;
  renderLogin(document.body, { api: loginApi(), session, onLoggedIn: () => { signedIn += 1; } });
  signIn(document.body, "gm", "wrong");
  await tick();
  assert.match(document.body.textContent, /invalid credentials/);
  assert.equal(session.token(), null);
  assert.equal(signedIn, 0);
});

test("after a refusal, the form is usable again and the password is cleared", async () => {
  const session = createSession(memoryStorage());
  renderLogin(document.body, { api: loginApi(), session, onLoggedIn() {} });
  signIn(document.body, "gm", "wrong");
  await tick();
  const button = document.querySelector('button[type="submit"]');
  const password = document.querySelector('input[name="password"]');
  assert.equal(button.disabled, false);
  assert.equal(password.value, "");
  assert.equal(document.activeElement, password);
});

test("why the last session ended is shown above the form", () => {
  renderLogin(document.body, {
    api: loginApi(),
    session: createSession(memoryStorage()),
    onLoggedIn() {},
    notice: "Signed out: invalid token.",
  });
  assert.match(document.body.textContent, /Signed out: invalid token\./);
});
