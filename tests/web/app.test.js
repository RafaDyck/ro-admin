// app.js no longer has any side effects at import time: startApp() takes
// the page, the browser and the network as arguments, so it can be stood
// up here against a throwaway jsdom window (installDom) and a fetch stub
// (fetchStub), the same way every other module in this UI is tested.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fetchStub, installDom, memoryStorage, tick } from "./helpers.js";
import { parseRoute, startApp } from "../../src/ro_admin/web/js/app.js";
import { TOKEN_STORAGE_KEY } from "../../src/ro_admin/web/js/session.js";

test("a character route is its id", () => {
  assert.deepEqual(parseRoute("#/character/150000"), { view: "dossier", charId: 150000 });
});

test("a leading zero does not change the id", () => {
  assert.deepEqual(parseRoute("#/character/007"), { view: "dossier", charId: 7 });
});

test("a trailing slash is still the same character", () => {
  assert.deepEqual(parseRoute("#/character/150000/"), { view: "dossier", charId: 150000 });
});

test("no hash, an unrecognised path, and a non-numeric id are all the search view", () => {
  for (const hash of ["", "#", "#/", "#/foo", "#/character/", "#/character/abc"]) {
    assert.deepEqual(parseRoute(hash), { view: "search" }, hash);
  }
});

test("an id past Number.MAX_SAFE_INTEGER is the search view, not a rounded id", () => {
  // "9007199254740993" cannot be represented exactly as a float64 -- JS
  // would silently round it to 9007199254740992, a DIFFERENT id that might
  // belong to somebody else. Number.isSafeInteger rejects it regardless of
  // how the rounding lands, because the boundary check is on the result,
  // not on whether rounding happened to occur.
  assert.deepEqual(parseRoute("#/character/9007199254740993"), { view: "search" });
  assert.deepEqual(parseRoute("#/character/99999999999999999999"), { view: "search" });
});

const capsRoute = { "GET system/capabilities": { body: { actions: {} } } };

function meRoute(subject) {
  return { "GET auth/me": { body: { subject, level: 50, permissions: ["system.read"] } } };
}

test("with no stored token, the login form is shown", async () => {
  const win = installDom();
  const { fetchImpl } = fetchStub({});
  startApp({ root: win.document.body, storage: memoryStorage(), win, fetchImpl });
  await tick();
  assert.ok(win.document.querySelector('input[name="userid"]'));
});

test("with a stored token, it signs itself in and shows the search view", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  const { fetchImpl, calls } = fetchStub({ ...meRoute("alice"), ...capsRoute });
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  assert.ok(win.document.querySelector('input[name="q"]'));
  assert.ok(calls.some((call) => call.url.includes("auth/me")));
  assert.match(win.document.body.textContent, /alice/);
});

test("Sign out clears the session and returns to the login form", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  const { fetchImpl } = fetchStub({ ...meRoute("alice"), ...capsRoute });
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  win.document.querySelector("header button").click();
  await tick();
  assert.ok(win.document.querySelector('input[name="userid"]'));
  assert.equal(storage.getItem(TOKEN_STORAGE_KEY), null);
});

// --- Stage-1 review fixes -------------------------------------------------
//
// The three below need finer control over WHEN a fetch answers than
// fetchStub's plain values give, so they build a small scripted fetchImpl
// of their own: most routes answer immediately, one is deliberately held
// open (a "gate") until the test releases it, standing in for the slow
// system/capabilities or auth/me answer each bug needs in flight.

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

function routeKey(url, init) {
  const path = new URL(url, "http://localhost").pathname.replace(/^\/api\/v1\//, "");
  return `${init?.method ?? "GET"} ${path}`;
}

function openGate() {
  let release;
  const opened = new Promise((resolve) => { release = resolve; });
  return { opened, release };
}

const CHAR_ID = 150000;
const CHARACTER = {
  char_id: CHAR_ID, account_id: 900000, name: "Hero", class: 0, online: false,
  stale: false, synced_at: null, zeny: 0, base_level: 1, job_level: 1,
  base_exp: 0, job_exp: 0, status_point: 0, skill_point: 0,
  last_map: "prontera", last_x: 53, last_y: 111,
};
const DOSSIER_ROUTES = {
  [`GET characters/${CHAR_ID}`]: jsonResponse(CHARACTER),
  [`GET characters/${CHAR_ID}/inventory`]: jsonResponse({ items: [] }),
  "GET logs/timeline": jsonResponse({ items: [], sources: ["picklog"] }),
  [`GET accounts/${CHARACTER.account_id}`]: jsonResponse({
    account_id: CHARACTER.account_id, userid: "hero1", banned: false,
  }),
};

// A routeKey-based fetchImpl (NOT fetchStub, whose handlers are shaped
// {status, body} rather than the {ok, status, json()} jsonResponse()
// builds) that can load auth/me, capabilities and this fixture's dossier,
// for tests that need a dossier to actually finish loading.
function dossierFetch(overrides = {}) {
  const routes = {
    "GET auth/me": jsonResponse({ subject: "alice", level: 50, permissions: ["system.read"] }),
    "GET system/capabilities": jsonResponse({ actions: {} }),
    ...DOSSIER_ROUTES,
    ...overrides,
  };
  return async (url, init) => {
    const key = routeKey(url, init);
    const entry = routes[key];
    if (entry === undefined) throw new Error(`no stub for ${key}`);
    return entry;
  };
}

test("Important #1: signing out while a route is in flight leaves the login form on screen", async () => {
  // Reproduces the reviewer's repro: on a dossier, click Back, then Sign
  // out before the resulting navigation's system/capabilities answer has
  // come back. Without showLogin() invalidating the in-flight route, that
  // stale answer lands AFTER Sign out and draws a signed-in search view
  // with no token behind it.
  //
  // The hash is changed only AFTER startApp() has registered its
  // `hashchange` listener, and each navigation is awaited before the next
  // -- setting it any earlier queues a hashchange jsdom only delivers on a
  // later tick, and that arrives duplicated once the listener exists,
  // starting an extra, uncontrolled route() this test does not mean to be
  // racing.
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  const meAlice = jsonResponse({ subject: "alice", level: 50, permissions: ["system.read"] });
  let capsCalls = 0;
  let blockOnCapsCall = -1;
  const capsGate = openGate();
  const fetchImpl = async (url, init) => {
    const key = routeKey(url, init);
    if (key === "GET system/capabilities") {
      capsCalls += 1;
      if (capsCalls === blockOnCapsCall) await capsGate.opened;
      return jsonResponse({ actions: {} });
    }
    const entry = { "GET auth/me": meAlice, ...DOSSIER_ROUTES }[key];
    if (entry === undefined) throw new Error(`no stub for ${key}`);
    return entry;
  };

  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick(); // the first route(): lands on the search view
  assert.ok(win.document.querySelector('input[name="q"]'));

  win.location.hash = `#/character/${CHAR_ID}`; // open the dossier
  await tick();
  assert.ok(win.document.querySelector(".back"), "the dossier loaded");

  // The NEXT capabilities call is Back's own navigation -- block exactly
  // that one, so it is still in flight when Sign out is clicked.
  blockOnCapsCall = capsCalls + 1;
  win.location.hash = ""; // Back
  await tick();

  win.document.querySelector("header button").click(); // Sign out, mid-flight
  await tick();
  assert.ok(win.document.querySelector('input[name="userid"]'), "the login form is shown immediately");

  capsGate.release(); // the stale answer lands
  await tick();
  assert.ok(
    win.document.querySelector('input[name="userid"]'),
    "the stale capabilities answer must not have replaced the login form",
  );
});

test("Important #2: a stale auth/me answer never assigns the wrong account", async () => {
  // "me" belongs to whichever token it was fetched for. Alice's auth/me is
  // held open; while it is in flight, another tab signs in as bob (a
  // "replaced" cross-tab change -- the only way a SECOND sign-in can start
  // while the first is still on screen, since the login form disables
  // itself after one submit). handleReplaced() clears meToken and calls
  // route(), which asks auth/me AGAIN (meCalls #2, for bob) and assigns
  // `me` correctly -- but that route() is still mid-flight, waiting on
  // system/capabilities, when alice's STALE original answer (meCalls #1)
  // finally lands. A naive `me = me ?? await auth/me` assigns before any
  // guard runs, so that late write clobbers the shared `me` back to
  // alice's, and the render bob's route() performs once capabilities
  // answers uses whatever `me` holds AT THAT MOMENT -- silently alice's,
  // not bob's, even though the guard Important #1 added correctly stops
  // alice's OWN call from drawing anything itself. Both gates are needed
  // to land alice's write inside that exact window.
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "alice-token");
  const meCalls = [];
  const aliceMeGate = openGate();
  const capsGate = openGate();
  const fetchImpl = async (url, init) => {
    const key = routeKey(url, init);
    if (key === "GET auth/me") {
      meCalls.push(init.headers.Authorization);
      if (meCalls.length === 1) {
        await aliceMeGate.opened;
        return jsonResponse({ subject: "alice", level: 50, permissions: ["system.read"] });
      }
      return jsonResponse({ subject: "bob", level: 50, permissions: ["system.read"] });
    }
    if (key === "GET system/capabilities") {
      await capsGate.opened;
      return jsonResponse({ actions: {} });
    }
    throw new Error(`no stub for ${key}`);
  };

  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  assert.equal(meCalls.length, 1, "alice's auth/me is in flight");

  storage.setItem(TOKEN_STORAGE_KEY, "bob-token");
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: "alice-token", newValue: "bob-token",
  }));
  await tick();
  // #2: route()'s own fetch, since handleReplaced() cleared meToken.
  assert.equal(meCalls.length, 2, "auth/me was asked again, for bob");

  aliceMeGate.release(); // alice's stale answer lands while bob's own route is still in flight
  await tick();

  capsGate.release(); // bob's route can now finish and draw the view
  await tick();

  assert.match(win.document.body.textContent, /bob/, "must render bob, not alice's stale answer");
  assert.doesNotMatch(win.document.body.textContent, /alice/);
});

test("Important #3: another tab signing out ends this tab's session too", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  const { fetchImpl } = fetchStub({ ...meRoute("alice"), ...capsRoute });
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  assert.ok(win.document.querySelector('input[name="q"]'));

  storage.removeItem(TOKEN_STORAGE_KEY);
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: "tok", newValue: null,
  }));
  await tick();

  assert.ok(win.document.querySelector('input[name="userid"]'));
  assert.match(win.document.body.textContent, /Signed out in another tab/);
});

test("Important #3: a tab on the login form adopts a sign-in from another tab", async () => {
  // The browser holds ONE session, shared by every tab -- a tab with none
  // of its own (showing the login form) must pick up a fresh sign-in from
  // another tab, not ignore it as someone else's business.
  const win = installDom();
  const storage = memoryStorage();
  const { fetchImpl } = fetchStub({ ...meRoute("bob"), ...capsRoute });
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  assert.ok(win.document.querySelector('input[name="userid"]'));

  storage.setItem(TOKEN_STORAGE_KEY, "bob-token");
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: null, newValue: "bob-token",
  }));
  await tick();

  assert.equal(win.document.querySelector('input[name="userid"]'), null);
  assert.match(win.document.body.textContent, /bob/);
});

test("Important A: another tab signing out while this tab's first auth/me is still in flight is not missed", async () => {
  // The storage listener used to compare against `meToken`, which stays
  // null until auth/me actually answers -- so a sign-out from another tab
  // arriving BEFORE that first answer landed read as "no session here to
  // end" and was silently missed: this tab stayed signed in, and its next
  // request would have gone out bearing the very token that was just
  // removed. `session.held()` is set the moment session.token() is first
  // called -- route()'s own first line -- well before the fetch it was
  // used to build has answered, closing that gap.
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  const meGate = openGate();
  const fetchImpl = async (url, init) => {
    const key = routeKey(url, init);
    if (key === "GET auth/me") {
      await meGate.opened;
      return jsonResponse({ subject: "alice", level: 50, permissions: ["system.read"] });
    }
    if (key === "GET system/capabilities") return jsonResponse({ actions: {} });
    throw new Error(`no stub for ${key}`);
  };
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick(); // route() started; auth/me is in flight, held open

  storage.removeItem(TOKEN_STORAGE_KEY);
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: "tok", newValue: null,
  }));
  await tick();
  assert.ok(
    win.document.querySelector('input[name="userid"]'),
    "the sign-out was noticed even though auth/me had not answered yet",
  );

  meGate.release(); // the stale answer lands
  await tick();
  assert.ok(win.document.querySelector('input[name="userid"]'), "still signed out");
});

// A dossierFetch() whose `characters/<id>` read -- the first thing
// loadDossier() awaits -- is held open until the test releases it, so a
// route() can be caught mid-renderDossier(), with the OLD view still not
// yet drawn onto anything.
function dossierFetchGatedOnCharacter(gate, overrides = {}) {
  const routes = {
    "GET auth/me": jsonResponse({ subject: "alice", level: 50, permissions: ["system.read"] }),
    "GET system/capabilities": jsonResponse({ actions: {} }),
    ...DOSSIER_ROUTES,
    ...overrides,
  };
  return async (url, init) => {
    const key = routeKey(url, init);
    if (key === `GET characters/${CHAR_ID}`) await gate.opened;
    const entry = routes[key];
    if (entry === undefined) throw new Error(`no stub for ${key}`);
    return entry;
  };
}

test("Important B: navigating away during a dossier load does not let it set a stale title", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  const gate = openGate();
  startApp({ root: win.document.body, storage, win, fetchImpl: dossierFetchGatedOnCharacter(gate) });
  await tick();
  win.location.hash = `#/character/${CHAR_ID}`;
  await tick(); // route() is now awaiting renderDossier(), blocked mid-load

  win.location.hash = ""; // Back -- a NEWER route() lands on search quickly
  await tick();
  assert.equal(win.document.title, "ro-admin");

  gate.release(); // the stale dossier load finally finishes
  await tick();
  assert.equal(win.document.title, "ro-admin", "the stale dossier must not have set its own title");
});

test("Important B: another tab signing out during a dossier load does not let it set a stale title", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  const gate = openGate();
  startApp({ root: win.document.body, storage, win, fetchImpl: dossierFetchGatedOnCharacter(gate) });
  await tick();
  win.location.hash = `#/character/${CHAR_ID}`;
  await tick(); // blocked mid-dossier-load

  storage.removeItem(TOKEN_STORAGE_KEY);
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: "tok", newValue: null,
  }));
  await tick();
  assert.equal(win.document.title, "Sign in — ro-admin");

  gate.release(); // the stale dossier load finally finishes
  await tick();
  assert.equal(win.document.title, "Sign in — ro-admin", "the stale dossier must not have set its own title");
});

// --- Minor fixes -----------------------------------------------------------

test("an account without system.read sees a plain notice, never Retry, and system/capabilities is never asked", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  const fetchImpl = async (url, init) => {
    const key = routeKey(url, init);
    if (key === "GET auth/me") {
      return jsonResponse({ subject: "player1", level: 1, permissions: [] });
    }
    // If system/capabilities is ever requested for an account with no
    // system.read, the guard below did not run -- this throw makes that
    // failure loud instead of quietly rendering "Something went wrong".
    throw new Error(`no stub for ${key}`);
  };
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  assert.match(win.document.body.textContent, /This account has no ro-admin permissions\./);
  assert.doesNotMatch(win.document.body.textContent, /Something went wrong/);
  assert.equal(
    [...win.document.querySelectorAll("button")].some((b) => b.textContent === "Retry"),
    false,
    "a permanent state of the account, not a transient failure, must offer no Retry",
  );

  const signOutButtons = [...win.document.querySelectorAll("button")]
    .filter((b) => b.textContent === "Sign out");
  assert.ok(signOutButtons.length >= 1, "a Sign out button is offered");
  signOutButtons[signOutButtons.length - 1].click();
  await tick();
  assert.ok(win.document.querySelector('input[name="userid"]'), "Sign out returns to the login form");
});

test("Minor: signing out from a dossier does not redraw the login form a second time", async () => {
  // If it did, a SECOND renderLogin() call would replace the first form's
  // own input with a fresh one, silently discarding anything typed into
  // it in between.
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  startApp({ root: win.document.body, storage, win, fetchImpl: dossierFetch() });
  await tick();
  win.location.hash = `#/character/${CHAR_ID}`;
  await tick();
  assert.equal(win.document.querySelector("h1")?.textContent, "Hero", "the dossier loaded");

  win.document.querySelector("header button").click(); // Sign out
  await tick();
  const userid = win.document.querySelector('input[name="userid"]');
  assert.ok(userid);
  userid.value = "still-here";
  await tick(); // let the (suppressed) hashchange task run, if any
  assert.equal(win.document.querySelector('input[name="userid"]').value, "still-here");
});

test("Minor: a failed navigation shows an error view with a Retry button", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  let fail = true;
  const fetchImpl = async (url, init) => {
    const key = routeKey(url, init);
    if (key === "GET auth/me") return jsonResponse({ subject: "alice", level: 50, permissions: ["system.read"] });
    if (key === "GET system/capabilities") {
      if (fail) throw new Error("network down");
      return jsonResponse({ actions: {} });
    }
    throw new Error(`no stub for ${key}`);
  };
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  assert.match(win.document.body.textContent, /Something went wrong/);
  const retry = [...win.document.querySelectorAll("button")].find((b) => b.textContent === "Retry");
  assert.ok(retry, "a Retry button is offered");

  fail = false;
  retry.click();
  await tick();
  assert.ok(win.document.querySelector('input[name="q"]'), "Retry recovered the page");
});

test("Minor: document.title names the view", async () => {
  const win = installDom();
  const { fetchImpl } = fetchStub({});
  startApp({ root: win.document.body, storage: memoryStorage(), win, fetchImpl });
  await tick();
  assert.equal(win.document.title, "Sign in — ro-admin");
});

test("Minor: the dossier sets the tab title to the character's name", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  startApp({ root: win.document.body, storage, win, fetchImpl: dossierFetch() });
  await tick();
  win.location.hash = `#/character/${CHAR_ID}`;
  await tick();
  assert.equal(win.document.title, "Hero — ro-admin");
});

test("Minor: opening the dossier moves focus to its own heading", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  startApp({ root: win.document.body, storage, win, fetchImpl: dossierFetch() });
  await tick();
  win.location.hash = `#/character/${CHAR_ID}`;
  await tick();
  assert.equal(win.document.activeElement.tagName, "H1");
  assert.equal(win.document.activeElement.textContent, "Hero");
});

test("Minor: landing on the search view still focuses the search box, not its heading", async () => {
  // search.js's own input.focus() must win: a sighted person who can act
  // on the box right away should not have that taken back by a generic
  // route-change focus rule.
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "tok");
  const { fetchImpl } = fetchStub({ ...meRoute("alice"), ...capsRoute });
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  assert.equal(win.document.activeElement, win.document.querySelector('input[name="q"]'));
});

test("Minor 1: after \"replaced\", the old view's form leaves the document synchronously, and never sends", async () => {
  // Simplified design (a session change elsewhere always redraws, rather
  // than probing first to decide whether to keep the old view): the
  // placeholder replaces the view BEFORE anything async happens, so this
  // needs no gate at all -- the assertion right after dispatchEvent, with
  // no `await` in between, IS the proof there is no window at all.
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "alice-token");
  let meCalls = 0;
  const requests = [];
  const fetchImpl = async (url, init) => {
    requests.push({ url, auth: init?.headers?.Authorization });
    const key = routeKey(url, init);
    if (key === "GET auth/me") {
      meCalls += 1;
      return jsonResponse({ subject: meCalls === 1 ? "alice" : "bob", level: 50, permissions: ["system.read", "commands.write"] });
    }
    if (key === "GET system/capabilities") {
      return jsonResponse({ actions: { sync_character: { tier: "tier2", available: true, reason: "" } } });
    }
    const entry = DOSSIER_ROUTES[key];
    if (entry !== undefined) return entry;
    throw new Error(`no stub for ${key}`);
  };
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  win.location.hash = `#/character/${CHAR_ID}`;
  await tick();
  const syncForm = [...win.document.querySelectorAll("form")]
    .find((f) => f.querySelector("h3")?.textContent === "Sync from game");
  assert.ok(syncForm && win.document.contains(syncForm), "alice's dossier loaded with its Sync form");

  requests.length = 0; // only what happens from here on matters
  storage.setItem(TOKEN_STORAGE_KEY, "bob-token");
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: "alice-token", newValue: "bob-token",
  }));
  // No `await` yet: dispatchEvent() runs the storage listener --
  // handleReplaced() -- synchronously, to completion (route()'s own
  // reload is fire-and-forget from there, and may itself have already
  // started a fetch by the time dispatchEvent() returns -- but for the
  // NEW identity, which is correct), so the placeholder -- and the loss
  // of the old form -- is already in the document by that point.
  assert.ok(!win.document.contains(syncForm), "the old form left the document synchronously");
  assert.match(win.document.body.textContent, /Switching session/);

  await tick();
  await tick();
  await tick();
  await tick();
  assert.equal(
    requests.filter((r) => r.url.includes("commands")).length,
    0,
    "the stale form was never interacted with (it was gone before anyone could), so it never sent anything",
  );
  assert.match(win.document.body.textContent, /bob/);
});

test("Minor 1: a navigation already in flight when \"replaced\" arrives ends on the CURRENT hash's view", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "alice-token");
  let meCalls = 0;
  const charGate = openGate();
  const fetchImpl = async (url, init) => {
    const key = routeKey(url, init);
    if (key === "GET auth/me") {
      meCalls += 1;
      return jsonResponse({ subject: meCalls === 1 ? "alice" : "bob", level: 50, permissions: ["system.read"] });
    }
    if (key === "GET system/capabilities") return jsonResponse({ actions: {} });
    if (key === `GET characters/${CHAR_ID}`) {
      await charGate.opened;
      return jsonResponse(CHARACTER);
    }
    const entry = DOSSIER_ROUTES[key];
    if (entry !== undefined) return entry;
    throw new Error(`no stub for ${key}`);
  };
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick(); // signed in as alice, on the search view

  win.location.hash = `#/character/${CHAR_ID}`; // navigate to the dossier
  await tick(); // route() is awaiting renderDossier(), blocked on the gated character fetch

  storage.setItem(TOKEN_STORAGE_KEY, "bob-token");
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: "alice-token", newValue: "bob-token",
  }));
  await tick();

  charGate.release(); // both the stale (now-invalidated) load and handleReplaced()'s own route() can proceed
  await tick();
  await tick();

  // The hash was never touched -- route() re-read it fresh, so the
  // in-flight navigation's destination is exactly what loads, now under
  // bob, rather than being dropped in favour of wherever alice was before.
  assert.equal(win.document.querySelector("h1")?.textContent, "Hero", "the dossier for #5, not the search view");
  assert.equal(win.location.hash, `#/character/${CHAR_ID}`);
});

test("Minor 1: a different subject after \"replaced\" shows the cross-tab notice; the same subject shows none", async () => {
  async function switchTo(secondSubject) {
    const win = installDom();
    const storage = memoryStorage();
    storage.setItem(TOKEN_STORAGE_KEY, "alice-token-1");
    let meCalls = 0;
    const fetchImpl = async (url, init) => {
      const key = routeKey(url, init);
      if (key === "GET auth/me") {
        meCalls += 1;
        return jsonResponse({ subject: meCalls === 1 ? "alice" : secondSubject, level: 50, permissions: ["system.read"] });
      }
      if (key === "GET system/capabilities") return jsonResponse({ actions: {} });
      throw new Error(`no stub for ${key}`);
    };
    startApp({ root: win.document.body, storage, win, fetchImpl });
    await tick();

    storage.setItem(TOKEN_STORAGE_KEY, "alice-token-2");
    win.dispatchEvent(new win.StorageEvent("storage", {
      key: TOKEN_STORAGE_KEY, oldValue: "alice-token-1", newValue: "alice-token-2",
    }));
    await tick();
    await tick();
    return win.document.body.textContent.includes("Now signed in as");
  }

  assert.equal(await switchTo("bob"), true, "a different subject shows the notice");
  assert.equal(await switchTo("alice"), false, "the same subject shows no notice");
});

test("Minor 1: the dossier title follows the CURRENT view after a switch", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "alice-token");
  let meCalls = 0;
  const fetchImpl = async (url, init) => {
    const key = routeKey(url, init);
    if (key === "GET auth/me") {
      meCalls += 1;
      return jsonResponse({ subject: meCalls === 1 ? "alice" : "bob", level: 50, permissions: ["system.read"] });
    }
    if (key === "GET system/capabilities") return jsonResponse({ actions: {} });
    const entry = DOSSIER_ROUTES[key];
    if (entry !== undefined) return entry;
    throw new Error(`no stub for ${key}`);
  };
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  win.location.hash = `#/character/${CHAR_ID}`;
  await tick();
  assert.equal(win.document.title, "Hero — ro-admin");

  storage.setItem(TOKEN_STORAGE_KEY, "bob-token");
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: "alice-token", newValue: "bob-token",
  }));
  await tick();
  await tick();
  assert.equal(win.document.title, "Hero — ro-admin", "still the current dossier's title, now loaded under bob");
});

test("Minor 1: alice -> bob -> carol in quick succession ends on carol with the notice", async () => {
  // crossTabPreviousSubject used to be consumed unconditionally at the
  // top of route() -- so bob's own route(), started by the first
  // handleReplaced(), would have already thrown "alice" away before
  // carol's switch ever got a chance to compare against it. bob's own
  // auth/me is held open so the second "replaced" (carol) arrives while
  // `me` is still null from the first -- exactly the window where the old
  // bug lost track of "alice" entirely.
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "alice-token");
  let meCalls = 0;
  const bobGate = openGate();
  const fetchImpl = async (url, init) => {
    const key = routeKey(url, init);
    if (key === "GET auth/me") {
      meCalls += 1;
      if (meCalls === 1) return jsonResponse({ subject: "alice", level: 50, permissions: ["system.read"] });
      if (meCalls === 2) {
        await bobGate.opened; // bob's own route(), never allowed to finish
        return jsonResponse({ subject: "bob", level: 50, permissions: ["system.read"] });
      }
      return jsonResponse({ subject: "carol", level: 50, permissions: ["system.read"] });
    }
    if (key === "GET system/capabilities") return jsonResponse({ actions: {} });
    throw new Error(`no stub for ${key}`);
  };
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick(); // signed in as alice

  storage.setItem(TOKEN_STORAGE_KEY, "bob-token");
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: "alice-token", newValue: "bob-token",
  }));
  await tick(); // handleReplaced() for bob has run; its own route() is gated mid-flight

  storage.setItem(TOKEN_STORAGE_KEY, "carol-token");
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: "bob-token", newValue: "carol-token",
  }));
  await tick();
  await tick(); // carol's own route() is not gated, and finishes

  assert.match(win.document.body.textContent, /carol/);
  assert.doesNotMatch(win.document.body.textContent, /bob/);
  assert.match(
    win.document.body.textContent,
    /Now signed in as carol, from another tab\./,
    "the notice still compares against alice, the identity actually shown before the switch, not bob",
  );

  bobGate.release(); // the stale, superseded fetch must change nothing
  await tick();
  assert.match(win.document.body.textContent, /Now signed in as carol, from another tab\./);
});

test("Minor: the placeholder gets the plain title, not the old view's", async () => {
  const win = installDom();
  const storage = memoryStorage();
  storage.setItem(TOKEN_STORAGE_KEY, "alice-token");
  let meCalls = 0;
  const fetchImpl = async (url, init) => {
    const key = routeKey(url, init);
    if (key === "GET auth/me") {
      meCalls += 1;
      return jsonResponse({ subject: meCalls === 1 ? "alice" : "bob", level: 50, permissions: ["system.read"] });
    }
    if (key === "GET system/capabilities") return jsonResponse({ actions: {} });
    const entry = DOSSIER_ROUTES[key];
    if (entry !== undefined) return entry;
    throw new Error(`no stub for ${key}`);
  };
  startApp({ root: win.document.body, storage, win, fetchImpl });
  await tick();
  win.location.hash = `#/character/${CHAR_ID}`;
  await tick();
  assert.equal(win.document.title, "Hero — ro-admin");

  storage.setItem(TOKEN_STORAGE_KEY, "bob-token");
  win.dispatchEvent(new win.StorageEvent("storage", {
    key: TOKEN_STORAGE_KEY, oldValue: "alice-token", newValue: "bob-token",
  }));
  // No `await`: setTitle() runs synchronously inside handleReplaced(),
  // right alongside the placeholder itself.
  assert.equal(win.document.title, "ro-admin", "no longer Hero's title, the instant the switch starts");

  await tick();
  await tick();
  assert.equal(win.document.title, "Hero — ro-admin", "back to the current dossier's title, now loaded under bob");
});
