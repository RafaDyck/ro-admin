import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { HOSTILE, fakeApi, installDom, markupIn, tick, type } from "./helpers.js";
import { ApiError } from "../../src/ro_admin/web/js/api.js";
import { renderSearch, runSearch, searchRequests } from "../../src/ro_admin/web/js/search.js";

const character = (char_id, name, extra = {}) => ({
  char_id, name, account_id: 2000000, base_level: 10, online: false, ...extra,
});
const account = (account_id, userid) => ({ account_id, userid, banned: false });
const page = (items, has_more = false) => ({ items, has_more });
const buttonWith = (text) =>
  [...document.querySelectorAll("button")].find((b) => b.textContent.includes(text));

beforeEach(() => installDom());

test("an empty query asks nothing", () => {
  assert.deepEqual(searchRequests("   "), []);
});

test("text is a prefix search of characters and accounts, done by the API", () => {
  assert.deepEqual(searchRequests(" Kam "), [
    { kind: "characters", path: "characters", params: { name_prefix: "Kam", limit: 10 } },
    { kind: "accounts", path: "accounts", params: { userid_prefix: "Kam", limit: 10 } },
  ]);
});

test("a number is also tried as an id", () => {
  assert.deepEqual(
    searchRequests("150000").map((r) => r.path),
    ["characters", "accounts", "characters/150000", "accounts/150000"],
  );
});

test("an id match leads its group, once; a number that is no id is no problem", async () => {
  const api = fakeApi({
    "GET characters": page([character(7, "150000fan"), character(150000, "150000")]),
    "GET accounts": page([]),
    "GET characters/150000": character(150000, "150000"),
    "GET accounts/150000": new ApiError(404, "no account with id 150000"),
  });
  const found = await runSearch(api, "150000");
  assert.deepEqual(found.characters.map((c) => c.char_id), [150000, 7]);
  assert.deepEqual(found.problems, { characters: [], accounts: [] });
});

test("other failures are reported per group, once each", async () => {
  // A realistic shared failure: both parallel requests hit the same network
  // problem, the way api.js actually reports a GET that never got an answer
  // (see api.js's NO_ANSWER / "could not reach the server" path). The two
  // endpoints never produce a 503 "database unreachable" -- that fixture
  // named a message these endpoints don't emit.
  //
  // Grouped rather than globally deduped: each group's own failure is
  // reported for that group, even though the message text happens to match.
  const api = fakeApi({
    "GET characters": new ApiError(0, "could not reach the server"),
    "GET accounts": new ApiError(0, "could not reach the server"),
  });
  const found = await runSearch(api, "Ka");
  assert.deepEqual(found.problems, {
    characters: ["could not reach the server"],
    accounts: ["could not reach the server"],
  });
});

test("has_more is passed on per group", async () => {
  const api = fakeApi({
    "GET characters": page([character(1, "Ka")], true),
    "GET accounts": page([account(2, "kaz")]),
  });
  const found = await runSearch(api, "Ka");
  assert.equal(found.moreCharacters, true);
  assert.equal(found.moreAccounts, false);
});

test("typing shows matches, and a match opens the dossier", async () => {
  const opened = [];
  const api = fakeApi({
    "GET characters": page([character(150000, "Kami")]),
    "GET accounts": page([]),
  });
  renderSearch(document.body, { api, onOpenCharacter: (id) => opened.push(id), debounceMs: 0 });
  type(document.querySelector("input"), "Ka");
  await tick(5);
  buttonWith("Kami").click();
  assert.deepEqual(opened, [150000]);
});

test("a slow answer to an earlier keystroke never replaces a newer one", async () => {
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const api = fakeApi({
    "GET characters": async (params) => {
      if (params.name_prefix === "A") {
        await slow;
        return page([character(1, "Alpha")]);
      }
      return page([character(2, "Albert")]);
    },
    "GET accounts": page([]),
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  const input = document.querySelector("input");
  type(input, "A");
  await tick(5);
  type(input, "Al");
  await tick(5);
  release();
  await tick(5);
  assert.match(document.body.textContent, /Albert/);
  assert.doesNotMatch(document.body.textContent, /Alpha/);
});

test("the newest keystroke always wins, even if an earlier answer lands inside the next debounce window", async () => {
  // The bug this proves fixed: with a nonzero debounce, typing "A" starts a
  // fetch once ITS OWN timer fires. If the operator then types "Zzz" before
  // "A"'s answer comes back, "Zzz" schedules its own timer -- but that timer
  // hasn't fired yet, so if the counter is only bumped at fire time, "A"'s
  // slow answer still matches "latest" and renders under "Zzz".
  let releaseAlpha;
  const alphaSlow = new Promise((resolve) => { releaseAlpha = resolve; });
  let releaseGhost;
  const ghostSlow = new Promise((resolve) => { releaseGhost = resolve; });
  const api = fakeApi({
    "GET characters": async (params) => {
      if (params.name_prefix === "A") { await alphaSlow; return page([character(1, "Alpha")]); }
      if (params.name_prefix === "Gh") { await ghostSlow; return page([character(3, "Ghost")]); }
      return page([character(2, "Zzzbert")]);
    },
    "GET accounts": page([]),
  });
  const debounceMs = 30;
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs });
  const input = document.querySelector("input");

  type(input, "A");
  await tick(debounceMs + 5); // "A"'s debounce fires; its slow fetch is now in flight
  type(input, "Zzz"); // typed while "A"'s fetch is outstanding, before "Zzz"'s own timer can fire
  releaseAlpha(); // "A"'s answer lands before "Zzz"'s debounce window has even ended
  await tick(5);
  assert.doesNotMatch(document.body.textContent, /Alpha/);
  await tick(debounceMs + 5); // "Zzz"'s own timer now fires
  assert.match(document.body.textContent, /Zzzbert/);

  // Clearing the box must invalidate an in-flight answer too, and clear
  // what's on screen immediately -- there is nothing to debounce when there
  // is nothing to search for.
  type(input, "Gh");
  await tick(debounceMs + 5); // "Gh"'s fetch is now in flight
  type(input, ""); // cleared before it resolves
  assert.equal(document.querySelector(".results").children.length, 0);
  releaseGhost();
  await tick(debounceMs + 10);
  assert.doesNotMatch(document.body.textContent, /Ghost/);
  assert.equal(document.querySelector(".results").children.length, 0);
});

test("a malformed answer replaces stale results, rather than leaving them on screen", async () => {
  const routes = {
    "GET characters": page([character(1, "Kami")]),
    "GET accounts": page([]),
  };
  const api = fakeApi(routes);
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  const input = document.querySelector("input");
  type(input, "Ka");
  await tick(5);
  assert.match(document.body.textContent, /Kami/);

  // A shape the real API never sends -- no `items` array -- must not be
  // read as "keep showing whatever was there before".
  routes["GET characters"] = {};
  type(input, "Kam");
  await tick(5);
  assert.doesNotMatch(document.body.textContent, /Kami/);
  assert.ok(document.querySelector(".problem"));
});

test("more matches are said to exist, rather than counted", async () => {
  const api = fakeApi({
    "GET characters": page([character(1, "Kami")], true),
    "GET accounts": page([]),
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "K");
  await tick(5);
  assert.match(document.body.textContent, /More characters match\. Keep typing to narrow\./);
});

test("nothing found says what was searched for, and that it is a prefix", async () => {
  const api = fakeApi({ "GET characters": page([]), "GET accounts": page([]) });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "Zz");
  await tick(5);
  assert.match(document.body.textContent, /No character or account starts with “Zz”\./);
});

test("a numeric query with no results also says no id matches", async () => {
  const api = fakeApi({
    "GET characters": page([]),
    "GET accounts": page([]),
    "GET characters/999999": new ApiError(404, "no character with id 999999"),
    "GET accounts/999999": new ApiError(404, "no account with id 999999"),
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "999999");
  await tick(5);
  assert.match(
    document.body.textContent,
    /No character or account starts with “999999”, and none has that id\./,
  );
});

test("a validation refusal from one group does not silence the other's no-match line", async () => {
  const api = fakeApi({
    "GET characters": page([]),
    "GET accounts": new ApiError(422, "userid_prefix: String should have at most 23 characters"),
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "a".repeat(24));
  await tick(5);
  assert.match(document.body.textContent, /No character starts with/);
  assert.match(
    document.body.textContent,
    /Accounts: userid_prefix: String should have at most 23 characters\./,
  );
  assert.doesNotMatch(document.body.textContent, /No account starts with/);
});

test("only a short status line is live; the results list is not", async () => {
  const api = fakeApi({
    "GET characters": page([character(1, "Kami"), character(2, "Kamiko")]),
    "GET accounts": page([account(3, "kam")]),
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "Kam");
  await tick(5);
  const live = [...document.querySelectorAll("[aria-live]")];
  assert.equal(live.length, 1);
  assert.match(live[0].textContent, /2 characters found\./);
  assert.match(live[0].textContent, /1 account found\./);
  assert.equal(document.querySelector(".results").hasAttribute("aria-live"), false);
});

test("a mixed line: one group has results, the other is a clean miss", async () => {
  const api = fakeApi({
    "GET characters": page([character(1, "Kami")]),
    "GET accounts": page([]),
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "Ka");
  await tick(5);
  const status = document.querySelector(".status");
  assert.match(status.textContent, /1 character found\./);
  assert.match(status.textContent, /No account starts with “Ka”\./);
});

test("a group's failure is its own sentence, styled as a problem", async () => {
  const api = fakeApi({
    "GET characters": page([]),
    "GET accounts": new ApiError(0, "could not reach the server"),
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "Ka");
  await tick(5);
  const problem = document.querySelector(".status .problem");
  assert.ok(problem);
  assert.match(problem.textContent, /Accounts: could not reach the server\./);
});

test("a group with items and a problem shows both sentences", async () => {
  // The id lookup for this group fails (a 500, not a 404 -- a real problem,
  // not an ordinary miss) while the prefix search for the same group still
  // found a match.
  const api = fakeApi({
    "GET characters": page([character(7, "150000fan")]),
    "GET accounts": page([]),
    "GET characters/150000": new ApiError(
      500,
      "the server failed (500) without saying why; its log has the detail",
    ),
    "GET accounts/150000": new ApiError(404, "no account with id 150000"),
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "150000");
  await tick(5);
  const status = document.querySelector(".status");
  assert.match(
    status.textContent,
    /Characters: the server failed \(500\) without saying why; its log has the detail\./,
  );
  assert.match(status.textContent, /1 character found\./);
  assert.ok(status.querySelector(".problem"));
});

test("an account lists its characters when opened", async () => {
  const api = fakeApi({
    "GET characters": page([]),
    "GET accounts": page([account(2000005, "gm_one")]),
    "GET accounts/2000005/characters": page([character(150000, "Kami")]),
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "gm");
  await tick(5);
  buttonWith("gm_one").click();
  await tick(5);
  assert.ok(buttonWith("Kami"));
});

test("clicking an account twice: the later click's answer wins, even if the earlier one is slower", async () => {
  let calls = 0;
  let releaseFirst;
  const firstSlow = new Promise((resolve) => { releaseFirst = resolve; });
  const api = fakeApi({
    "GET characters": page([]),
    "GET accounts": page([account(2000005, "gm_one")]),
    "GET accounts/2000005/characters": async () => {
      calls += 1;
      if (calls === 1) {
        await firstSlow;
        return page([character(1, "StaleKid")]);
      }
      return page([character(2, "FreshKid")]);
    },
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "gm");
  await tick(5);
  const button = buttonWith("gm_one");
  button.click(); // first click: slow
  await tick(5);
  button.click(); // second click: fast, resolves before the first
  await tick(5);
  releaseFirst(); // now let the stale first answer land
  await tick(5);
  assert.match(document.body.textContent, /FreshKid/);
  assert.doesNotMatch(document.body.textContent, /StaleKid/);
});

test("a hostile name is shown, not run", async () => {
  const api = fakeApi({
    "GET characters": page([character(1, HOSTILE)]),
    "GET accounts": page([account(2, HOSTILE)]),
  });
  renderSearch(document.body, { api, onOpenCharacter() {}, debounceMs: 0 });
  type(document.querySelector("input"), "<img");
  await tick(5);
  // Asserted first: if a future change swapped in a markup-parsing path, the
  // page could still contain the literal HOSTILE text (e.g. echoed back
  // inside an attribute or a script node) while ALSO having run it. Checking
  // structure before content means that regression fails here, not on the
  // textContent line below.
  assert.deepEqual(markupIn(document.body), []);
  assert.ok(document.body.textContent.includes(HOSTILE));
});
