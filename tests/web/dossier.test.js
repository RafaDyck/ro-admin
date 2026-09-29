import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { HOSTILE, fakeApi, installDom, markupIn, submit, tick } from "./helpers.js";
import { ApiError } from "../../src/ro_admin/web/js/api.js";
import { renderDossier } from "../../src/ro_admin/web/js/dossier.js";

const CHARACTER = {
  char_id: 150000, account_id: 2000005, name: "Kami", class: 4008,
  base_level: 99, job_level: 70, base_exp: 1234567, job_exp: 234567,
  zeny: 592213, status_point: 12, skill_point: 34,
  last_map: "prontera", last_x: 150, last_y: 180,
  online: true, stale: true, stale_fields: ["zeny"], synced_at: null,
};
const INVENTORY = {
  char_id: 150000, stale: true,
  items: [{ item_id: 909, item_name: "Jellopy", amount: 3, refine: 0, identified: true, equipped: false }],
};
const TIMELINE = {
  items: [{ date: "2026-09-26T10:11:12", kind: "item", char_id: 150000, summary: "Jellopy x3 via A on prontera", detail: {} }],
  limit: 50,
  sources: ["atcommandlog", "zenylog", "picklog"],
};
const ACCOUNT = { account_id: 2000005, userid: "gm_one", banned: false };
const READS = ["logs.read", "accounts.read", "characters.read", "system.read", "commands.read"];
const STAFF = { subject: "gm", level: 10, permissions: READS };
const ADMIN = { subject: "admin", level: 99, permissions: [...READS, "commands.write"] };
const ON = { available: true, reason: "overlay responding, last seen 0s ago" };
const OFF = { available: false, reason: "overlay not installed: run overlay/schema.sql against this database" };
const caps = (tier1, tier2) => ({
  actions: {
    give_item: { tier: "tier1", ...tier1 },
    adjust_zeny: { tier: "tier1", ...tier1 },
    sync_character: { tier: "tier2", ...tier2 },
  },
});

function dossierApi(overrides = {}) {
  return fakeApi({
    "GET characters/150000": CHARACTER,
    "GET characters/150000/inventory": INVENTORY,
    "GET logs/timeline": TIMELINE,
    "GET accounts/2000005": ACCOUNT,
    ...overrides,
  });
}

function open(api, me, capabilities = caps(ON, ON)) {
  return renderDossier(document.body, {
    api, charId: 150000, me, caps: capabilities, onBack() {}, sleep: async () => {}, debounceMs: 0,
  });
}

function syncForm() {
  return [...document.querySelectorAll("form")].find((f) => f.querySelector("h3").textContent === "Sync from game");
}

function zenyForm() {
  return [...document.querySelectorAll("form")].find((f) => f.querySelector("h3").textContent === "Adjust zeny");
}

// One <dt>/<dd> pair by its label, not a substring search over the whole
// section: a substring match on the section's full text can be satisfied by
// the WRONG field (a value that happens to appear inside a neighbour's, or
// two labels swapped) without the test noticing either mistake.
function factByLabel(section, label) {
  const dl = section.querySelector("dl");
  const terms = [...dl.querySelectorAll("dt")];
  const index = terms.findIndex((dt) => dt.textContent === label);
  if (index === -1) throw new Error(`no <dt> labelled ${JSON.stringify(label)} in ${section.className}`);
  return dl.querySelectorAll("dd")[index].textContent;
}

beforeEach(() => installDom());

// --- layout and identity -----------------------------------------------

test("one page, in reading order", async () => {
  await open(dossierApi(), ADMIN);
  const headings = [...document.querySelectorAll("h1, h2")].map((h) => h.textContent);
  assert.deepEqual(headings, ["Kami", "State", "Actions", "Inventory", "History"]);
  assert.match(document.body.textContent, /gm_one \(#2000005\)/);
  assert.match(document.body.textContent, /Jellopy/);
});

test("the class is the bare job id rAthena stores", async () => {
  await open(dossierApi(), STAFF);
  assert.match(document.body.textContent, /job 4008/);
});

test("the back button has an accessible name", async () => {
  await open(dossierApi(), STAFF);
  assert.equal(document.querySelector("button.back").getAttribute("aria-label"), "Back to search");
});

test("when the account fetch fails, Identity falls back to the bare account id", async () => {
  const api = dossierApi({ "GET accounts/2000005": new ApiError(0, "could not reach the server") });
  await open(api, STAFF);
  assert.equal(factByLabel(document.querySelector("section.identity"), "Account"), "#2000005");
});

// --- State: every CHARACTER_VOLATILE field, freshness at its head -------

test("freshness heads the State section, qualifying every value below it", async () => {
  await open(dossierApi(), STAFF);
  const state = document.querySelector("section.state");
  const [heading, note, dl] = [...state.children];
  assert.equal(heading.tagName, "H2");
  assert.equal(note.tagName, "P");
  assert.match(note.textContent, /may be behind the game/);
  assert.equal(note.className, "freshness stale"); // CHARACTER.stale is true
  assert.equal(dl.tagName, "DL");
});

test("State renders every field in CHARACTER_VOLATILE under its own label", async () => {
  // Keep in sync with projections.CHARACTER_VOLATILE (src/ro_admin/projections.py):
  // zeny, base_level, job_level, base_exp, job_exp, status_point, skill_point,
  // last_map, last_x, last_y (the last three rendered together as Position).
  await open(dossierApi(), STAFF);
  const state = document.querySelector("section.state");
  assert.equal(factByLabel(state, "Zeny"), "592,213");
  assert.equal(factByLabel(state, "Base level"), "99");
  assert.equal(factByLabel(state, "Job level"), "70");
  assert.equal(factByLabel(state, "Base exp"), "1,234,567");
  assert.equal(factByLabel(state, "Job exp"), "234,567");
  assert.equal(factByLabel(state, "Status points"), "12");
  assert.equal(factByLabel(state, "Skill points"), "34");
  assert.equal(factByLabel(state, "Position"), "prontera (150, 180)");
});

test("an offline character's State carries no freshness note", async () => {
  const api = dossierApi({ "GET characters/150000": { ...CHARACTER, online: false, stale: false, stale_fields: [] } });
  await open(api, STAFF);
  const state = document.querySelector("section.state");
  assert.equal(state.querySelector("p.freshness"), null);
  assert.equal([...state.children].length, 2); // h2, dl -- no note in between
});

test("a verified character's State says so, on the server clock", async () => {
  const api = dossierApi({
    "GET characters/150000": { ...CHARACTER, stale: false, stale_fields: [], synced_at: "2026-09-26T10:00:00" },
  });
  await open(api, STAFF);
  const note = document.querySelector("section.state p.freshness");
  assert.match(note.textContent, /verified against the game at 2026-09-26 10:00:00 \(server clock\)/);
  assert.equal(note.className, "freshness"); // not stale
});

// --- Inventory ------------------------------------------------------------

test("an empty inventory says so", async () => {
  const api = dossierApi({ "GET characters/150000/inventory": { char_id: 150000, stale: false, items: [] } });
  await open(api, STAFF);
  assert.match(document.querySelector("section.inventory").textContent, /No items\./);
});

test("inventory flags: equipped, unidentified, and a refine level all show, under a Notes header", async () => {
  const api = dossierApi({
    "GET characters/150000/inventory": {
      char_id: 150000, stale: false,
      items: [
        { item_id: 1, item_name: "Sword", amount: 1, refine: 7, identified: true, equipped: true },
        { item_id: 2, item_name: "Mystery Box", amount: 1, refine: 0, identified: false, equipped: false },
      ],
    },
  });
  await open(api, STAFF);
  const rows = [...document.querySelectorAll("section.inventory tbody tr")];
  assert.match(rows[0].textContent, /\+7/);
  assert.match(rows[0].textContent, /equipped/);
  assert.match(rows[1].textContent, /unidentified/);
  assert.doesNotMatch(rows[1].textContent, /equipped/);
  const headers = [...document.querySelectorAll("section.inventory th")].map((th) => th.textContent);
  assert.deepEqual(headers, ["Item", "Id", "Amount", "Refine", "Notes"]);
});

// --- History ---------------------------------------------------------------

function timelineOf(count) {
  return {
    items: Array.from({ length: count }, (_, i) => ({
      date: "2026-09-26T10:11:12", kind: "item", char_id: 150000, summary: `event ${i}`, detail: {},
    })),
    limit: 50,
    sources: ["atcommandlog", "zenylog", "picklog"],
  };
}

test("History states the cutoff only when the server actually cut something off", async () => {
  const full = dossierApi({ "GET logs/timeline": timelineOf(50) });
  await open(full, STAFF);
  assert.match(
    document.querySelector("section.history").textContent,
    /Showing the latest 50 events; older ones are not shown\./,
  );
});

test("History omits the cutoff line when fewer than the limit came back", async () => {
  const partial = dossierApi({ "GET logs/timeline": timelineOf(3) });
  await open(partial, STAFF);
  assert.doesNotMatch(document.querySelector("section.history").textContent, /Showing the latest/);
});

test("History omits the cutoff line for an empty timeline too", async () => {
  const empty = dossierApi({ "GET logs/timeline": { items: [], limit: 50, sources: ["atcommandlog", "zenylog", "picklog"] } });
  await open(empty, STAFF);
  assert.doesNotMatch(document.querySelector("section.history").textContent, /Showing the latest/);
});

test("an empty timeline is worded plainly, with the source tables named in a quieter line", async () => {
  const api = dossierApi({ "GET logs/timeline": { items: [], limit: 50, sources: ["atcommandlog", "zenylog", "picklog"] } });
  await open(api, STAFF);
  const history = document.querySelector("section.history");
  assert.match(history.textContent, /Nothing recorded for this character yet\./);
  assert.match(history.textContent, /atcommandlog, zenylog, picklog/);
});

// --- permissions and capabilities ------------------------------------------

test("STAFF sees the whole dossier, with no action bar", async () => {
  await open(dossierApi(), STAFF);
  assert.equal(document.querySelector("section.actions"), null);
  assert.equal(document.querySelectorAll("form").length, 0);
  assert.match(document.body.textContent, /History/);
});

test("an admin on a server without Tier 1 is told why, in the API's words", async () => {
  await open(dossierApi(), ADMIN, caps(OFF, OFF));
  assert.equal(document.querySelectorAll("form").length, 0);
  assert.ok(document.body.textContent.includes(`Give item unavailable: ${OFF.reason}`));
});

// --- failures: first load vs. a failed re-read say different things -------

test("a failed FIRST load keeps today's plain wording, and can be retried", async () => {
  let failing = true;
  const api = dossierApi({
    "GET characters/150000": () => (failing ? new ApiError(0, "could not reach the server") : CHARACTER),
  });
  await open(api, STAFF);
  assert.match(document.body.textContent, /could not reach the server/);
  assert.doesNotMatch(document.body.textContent, /Could not re-read/);
  assert.equal(document.querySelector("h1").textContent, "Character #150000");
  failing = false;
  [...document.querySelectorAll("button")].find((b) => b.textContent === "Retry").click();
  await tick(5);
  assert.match(document.body.textContent, /592,213/);
  assert.doesNotMatch(document.body.textContent, /could not reach the server/);
});

test("a failed re-read says the page is old, distinct from a failed first load", async () => {
  let charCalls = 0;
  const api = dossierApi({
    "GET characters/150000": () => {
      charCalls += 1;
      return charCalls === 1 ? CHARACTER : new ApiError(0, "could not reach the server");
    },
    "POST commands": () => ({ id: 1, char_id: 150000, action: "sync_character", status: "executed", overlay_responding: true, error_message: null }),
  });
  await open(api, ADMIN);
  submit(syncForm());
  await tick(5);
  assert.match(
    document.body.textContent,
    /Could not re-read this page: could not reach the server\. What is shown is from the earlier read\./,
  );
  // The earlier read is still the one on screen.
  assert.equal(factByLabel(document.querySelector("section.state"), "Zeny"), "592,213");
});

test("a malformed timeline during a refresh lands in the problem line, and the earlier good page is untouched", async () => {
  let charCalls = 0;
  let timelineCalls = 0;
  const api = dossierApi({
    // A DIFFERENT character on the second read: if the page repainted
    // Identity/State before History's build threw, this is what would leak
    // through -- an identical second read could never catch that.
    "GET characters/150000": () => {
      charCalls += 1;
      return charCalls === 1 ? CHARACTER : { ...CHARACTER, zeny: 700000 };
    },
    "GET logs/timeline": () => {
      timelineCalls += 1;
      // No `sources`: the second read is malformed, and rendering it throws.
      return timelineCalls === 1 ? TIMELINE : { items: [], limit: 50 };
    },
    "POST commands": () => ({ id: 1, char_id: 150000, action: "sync_character", status: "executed", overlay_responding: true, error_message: null }),
  });
  await open(api, ADMIN);
  const before = {
    identity: document.querySelector("section.identity").textContent,
    state: document.querySelector("section.state").textContent,
    inventory: document.querySelector("section.inventory").textContent,
    history: document.querySelector("section.history").textContent,
  };
  submit(syncForm());
  await tick(5);
  assert.match(document.body.textContent, /Could not re-read this page:/);
  assert.equal(document.querySelector("section.identity").textContent, before.identity);
  assert.equal(document.querySelector("section.state").textContent, before.state);
  assert.equal(document.querySelector("section.inventory").textContent, before.inventory);
  assert.equal(document.querySelector("section.history").textContent, before.history);
  // Specifically: State still reads the OLD zeny, not the new character's --
  // the sharpest possible proof that nothing was swapped into the DOM ahead
  // of the throw.
  assert.equal(factByLabel(document.querySelector("section.state"), "Zeny"), "592,213");
});

test("a failed first render -- not just a failed load -- also replaces the Loading… h1", async () => {
  // No `sources` on the very first read: buildHistory throws before
  // `loaded` is ever set to true.
  const api = dossierApi({ "GET logs/timeline": { items: [], limit: 50 } });
  await open(api, STAFF);
  const h1 = document.querySelector("h1");
  assert.equal(h1.textContent, "Character #150000");
  assert.notEqual(h1.textContent, "Loading…");
});

test("a malformed capabilities body lands in the problem line, not thrown out of the first load", async () => {
  // No `actions`: actionPlan's Object.hasOwn(caps.actions, ...) throws.
  // Identity/State/Inventory/History already built and rendered
  // successfully by the time actionPlan runs, so they must stay on
  // screen -- only Actions itself, and the problem line, are affected.
  await open(dossierApi(), ADMIN, {});
  assert.equal(document.querySelector("h1").textContent, "Kami");
  assert.match(factByLabel(document.querySelector("section.state"), "Zeny"), /592,213/);
  assert.match(document.querySelector(".problem").textContent, /\S/); // some message, not empty
  assert.ok([...document.querySelectorAll("button")].some((b) => b.textContent === "Retry"));
});

// --- the generation guard: a stale answer must never win --------------

test("a Retry double-click causes one load, not two", async () => {
  let charCalls = 0;
  const api = dossierApi({
    "GET characters/150000": () => {
      charCalls += 1;
      return charCalls === 1 ? new ApiError(0, "could not reach the server") : CHARACTER;
    },
  });
  await open(api, STAFF);
  const retry = [...document.querySelectorAll("button")].find((b) => b.textContent === "Retry");
  retry.click();
  retry.click();
  await tick(5);
  assert.equal(charCalls, 2); // the failing first load, plus exactly one retry
  assert.match(document.body.textContent, /592,213/);
});

test("an older refresh's answer landing after a newer one's does not overwrite it", async () => {
  let charCalls = 0;
  const charResolvers = [];
  const api = dossierApi({
    "GET characters/150000": () => {
      charCalls += 1;
      if (charCalls === 1) return CHARACTER; // the initial load
      return new Promise((resolve) => charResolvers.push(resolve));
    },
    "POST commands": (body) => ({
      id: charCalls + 100, char_id: 150000, action: body.action, status: "executed",
      overlay_responding: true, error_message: null,
    }),
  });
  await open(api, ADMIN);
  zenyForm().querySelector('input[name="delta"]').value = "10";

  submit(syncForm()); // starts the OLDER refresh, once its own POST resolves
  await tick(5);
  submit(zenyForm()); // starts the NEWER refresh, once its own POST resolves
  await tick(5);
  assert.equal(charResolvers.length, 2, "both refreshes should now be waiting on their own GET");

  charResolvers[1]({ ...CHARACTER, zeny: 700000 }); // the NEWER refresh answers first
  await tick(5);
  assert.equal(factByLabel(document.querySelector("section.state"), "Zeny"), "700,000");

  charResolvers[0]({ ...CHARACTER, zeny: 1 }); // the OLDER refresh answers late
  await tick(5);
  assert.equal(factByLabel(document.querySelector("section.state"), "Zeny"), "700,000"); // unchanged
});

test("an older refresh's failure landing after a newer success is discarded, not shown", async () => {
  let charCalls = 0;
  const charResolvers = [];
  const api = dossierApi({
    "GET characters/150000": () => {
      charCalls += 1;
      if (charCalls === 1) return CHARACTER;
      return new Promise((resolve, reject) => charResolvers.push({ resolve, reject }));
    },
    "POST commands": (body) => ({
      id: charCalls + 100, char_id: 150000, action: body.action, status: "executed",
      overlay_responding: true, error_message: null,
    }),
  });
  await open(api, ADMIN);
  zenyForm().querySelector('input[name="delta"]').value = "10";

  submit(syncForm()); // starts the OLDER refresh -- this is the one that will fail
  await tick(5);
  submit(zenyForm()); // starts the NEWER refresh -- this is the one that will succeed
  await tick(5);
  assert.equal(charResolvers.length, 2);

  charResolvers[1].resolve({ ...CHARACTER, zeny: 700000 }); // the newer refresh succeeds
  await tick(5);
  assert.doesNotMatch(document.body.textContent, /could not reach the server/);

  charResolvers[0].reject(new ApiError(0, "could not reach the server")); // the older one fails, late
  await tick(5);
  assert.doesNotMatch(document.body.textContent, /could not reach the server/);
  assert.equal(factByLabel(document.querySelector("section.state"), "Zeny"), "700,000");
});

// --- keeping the outcome, the account, and the name current ---------------

test("after an action executes, the page re-reads and keeps the outcome in view", async () => {
  // Simplified: in the real lab an online character's row may lag the game,
  // which the freshness line says. This pins the refresh itself.
  let zeny = 592213;
  const api = dossierApi({
    "GET characters/150000": () => ({ ...CHARACTER, zeny }),
    "POST commands": () => {
      zeny += 250;
      return { id: 41, char_id: 150000, action: "adjust_zeny", status: "executed", overlay_responding: true, error_message: null };
    },
  });
  await open(api, ADMIN);
  const form = zenyForm();
  form.querySelector('input[name="delta"]').value = "250";
  submit(form);
  await tick(5);
  assert.match(document.body.textContent, /592,463/);
  assert.match(form.textContent, /Added 250 zeny to Kami: executed/);
  // The SAME form node the operator was reading, not a freshly built one
  // sharing its place: refresh must not have torn down and rebuilt Actions,
  // which would silently blank the outcome an operator is still reading
  // even though the stale `form` reference above would keep reporting it.
  assert.equal(zenyForm(), form);
});

test("the account is cached after its first successful fetch; a later refresh does not re-fetch it", async () => {
  const api = dossierApi({
    "POST commands": () => ({ id: 1, char_id: 150000, action: "sync_character", status: "executed", overlay_responding: true, error_message: null }),
  });
  await open(api, ADMIN);
  const accountCalls = () => api.calls.filter((c) => c.method === "GET" && c.path === "accounts/2000005").length;
  assert.equal(accountCalls(), 1);
  submit(syncForm());
  await tick(5);
  assert.equal(accountCalls(), 1); // unchanged: no second round trip
});

test("onTitle is called on the first load, and again on every refresh that renames the character", async () => {
  let charCalls = 0;
  const api = dossierApi({
    "GET characters/150000": () => {
      charCalls += 1;
      return charCalls === 1 ? CHARACTER : { ...CHARACTER, name: "Kamiko" };
    },
    "POST commands": () => ({ id: 1, char_id: 150000, action: "sync_character", status: "executed", overlay_responding: true, error_message: null }),
  });
  const titles = [];
  await renderDossier(document.body, {
    api, charId: 150000, me: ADMIN, caps: caps(ON, ON), onBack() {}, sleep: async () => {}, debounceMs: 0,
    onTitle: (name) => titles.push(name),
  });
  assert.deepEqual(titles, ["Kami"]);
  submit(syncForm());
  await tick(5);
  assert.deepEqual(titles, ["Kami", "Kamiko"]);
});

test("an action's summary uses the CURRENT name after an earlier refresh renamed the character", async () => {
  let charCalls = 0;
  const api = dossierApi({
    "GET characters/150000": () => {
      charCalls += 1;
      return charCalls === 1 ? CHARACTER : { ...CHARACTER, name: "Kamiko" };
    },
    "POST commands": () => ({ id: 1, char_id: 150000, action: "sync_character", status: "executed", overlay_responding: true, error_message: null }),
  });
  await open(api, ADMIN);
  submit(syncForm());
  await tick(5);
  assert.equal(document.querySelector("h1").textContent, "Kamiko"); // the refresh already renamed them
  submit(syncForm());
  await tick(5);
  assert.match(syncForm().textContent, /Synced Kamiko: executed/);
});

// --- no markup, ever --------------------------------------------------

test("hostile strings in every field are shown, never run", async () => {
  const api = dossierApi({
    "GET characters/150000": { ...CHARACTER, name: HOSTILE, last_map: HOSTILE },
    "GET characters/150000/inventory": { ...INVENTORY, items: [{ ...INVENTORY.items[0], item_name: HOSTILE }] },
    "GET logs/timeline": { ...TIMELINE, items: [{ ...TIMELINE.items[0], summary: HOSTILE }] },
    "GET accounts/2000005": { ...ACCOUNT, userid: HOSTILE },
  });
  await open(api, ADMIN);
  assert.deepEqual(markupIn(document.body), []);
  assert.equal(document.querySelector("h1").textContent, HOSTILE);
  assert.ok(document.body.textContent.split(HOSTILE).length - 1 >= 5);
});
