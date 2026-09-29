import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { fakeApi, HOSTILE, installDom, markupIn, submit, tick, type } from "./helpers.js";
import { ApiError } from "../../src/ro_admin/web/js/api.js";
import {
  describeOutcome, FORMS, LABELS, pollCommand, renderActions, runCommand,
} from "../../src/ro_admin/web/js/actions.js";

const noWait = async () => {};
const row = (status, extra = {}) => ({
  id: 41, char_id: 150000, action: "give_item", status,
  overlay_responding: true, error_message: null, ...extra,
});
const inTurn = (...rows) => {
  let next = 0;
  return () => rows[Math.min(next++, rows.length - 1)];
};

beforeEach(() => installDom());

// --- the public surface -----------------------------------------------------

test("LABELS and FORMS cover exactly the same actions, so they cannot drift", () => {
  assert.deepEqual(Object.keys(LABELS), Object.keys(FORMS));
});

// --- the poll -------------------------------------------------------------

test("a 202 that is already terminal is the answer; nothing is polled", async () => {
  const api = fakeApi({ "POST commands": row("executed") });
  const result = await runCommand(api, {}, { sleep: noWait });
  assert.equal(result.row.status, "executed");
  assert.equal(result.stopped, null);
  assert.equal(api.calls.length, 1);
});

test("a pending row is polled until the row itself is terminal", async () => {
  const api = fakeApi({
    "POST commands": row("pending"),
    "GET commands/41": inTurn(row("processing"), row("executed")),
  });
  const seen = [];
  const result = await runCommand(api, {}, { sleep: noWait, onUpdate: (r) => seen.push(r.status) });
  assert.deepEqual(seen, ["pending", "processing", "executed"]);
  assert.equal(result.stopped, null);
});

test("a row nobody is consuming stops the wait instead of spinning", async () => {
  const api = fakeApi({ "POST commands": row("pending", { overlay_responding: false }) });
  const result = await runCommand(api, {}, { sleep: noWait });
  assert.equal(result.stopped, "no-consumer");
  assert.equal(api.calls.length, 1);
});

test("the wait is bounded", async () => {
  const api = fakeApi({ "POST commands": row("pending"), "GET commands/41": row("pending") });
  const result = await runCommand(api, {}, { sleep: noWait, maxPolls: 3, intervalMs: 500 });
  assert.equal(result.stopped, "timeout");
  assert.equal(api.calls.length, 4);
  assert.equal(result.waitMs, 1500); // maxPolls * intervalMs, the wait actually made
});

// --- a GET failure mid-poll must never be read as "the write failed" ------

test("a GET that rejects twice then succeeds still reaches executed", async () => {
  let getCalls = 0;
  const api = fakeApi({
    "GET commands/41": () => {
      getCalls += 1;
      if (getCalls <= 2) throw new ApiError(0, "could not reach the server");
      return row("executed");
    },
  });
  const result = await pollCommand(api, row("pending"), { sleep: noWait });
  assert.equal(result.row.status, "executed");
  assert.equal(result.stopped, null);
  assert.equal(getCalls, 3);
});

test("three consecutive GET failures give lost-contact, with the last row read, not a thrown error", async () => {
  const api = fakeApi({
    "GET commands/41": () => { throw new ApiError(0, "could not reach the server"); },
  });
  const result = await pollCommand(api, row("pending"), { sleep: noWait });
  assert.equal(result.stopped, "lost-contact");
  assert.equal(result.row.status, "pending");
  assert.ok(result.error instanceof Error);
});

test("a 401 mid-poll gives lost-contact immediately, without tolerating retries", async () => {
  // api.js has already ended the session by the time this throws; nothing
  // is gained by spending the two tolerated failures on a request that
  // cannot succeed.
  let getCalls = 0;
  const api = fakeApi({
    "GET commands/41": () => { getCalls += 1; throw new ApiError(401, "session expired"); },
  });
  const result = await pollCommand(api, row("pending"), { sleep: noWait });
  assert.equal(result.stopped, "lost-contact");
  assert.equal(getCalls, 1);
});

test("a 403 mid-poll stops immediately as unwatchable, never spending the lost-contact retry budget", async () => {
  // A scoped token with commands.write but not commands.read gets this on
  // EVERY poll -- unlike a network blip, retrying it buys nothing, so it
  // must not tolerate the two transient failures a real lost-contact does.
  let getCalls = 0;
  const api = fakeApi({
    "GET commands/41": () => { getCalls += 1; throw new ApiError(403, "not permitted: commands.read"); },
  });
  const result = await pollCommand(api, row("pending"), { sleep: noWait });
  assert.equal(result.stopped, "unwatchable");
  assert.equal(getCalls, 1, "a 403 must not be retried the way a network blip is");
  assert.ok(result.error instanceof Error);
});

test("a 404 mid-poll is equally final, the same as a 403", async () => {
  const api = fakeApi({ "GET commands/41": () => { throw new ApiError(404, "not found"); } });
  const result = await pollCommand(api, row("pending"), { sleep: noWait });
  assert.equal(result.stopped, "unwatchable");
});

test("a 5xx mid-poll is still a transient lost-contact blip, not unwatchable", async () => {
  // Only the 4xx-but-not-401 range is treated as final; a 5xx keeps the
  // existing tolerate-two-then-give-up behaviour.
  let getCalls = 0;
  const api = fakeApi({
    "GET commands/41": () => { getCalls += 1; throw new ApiError(500, "internal error"); },
  });
  const result = await pollCommand(api, row("pending"), { sleep: noWait });
  assert.equal(result.stopped, "lost-contact");
  assert.equal(getCalls, 3, "a 5xx still gets the two tolerated retries before giving up");
});

test("checkFirst:false does not judge a stale row after its first GET fails -- only a row it actually just read", async () => {
  // The bug this proves fixed: `judge` used to become true after the FIRST
  // lap regardless of whether that lap's GET succeeded. So a Check again
  // whose first fresh GET failed would fall through to judging the STALE
  // row it was resumed with -- on the very next lap, without ever having
  // read anything new -- which is exactly the stale judgement
  // `checkFirst: false` exists to avoid.
  let getCalls = 0;
  const stale = row("pending", { overlay_responding: false });
  const api = fakeApi({
    "GET commands/41": () => {
      getCalls += 1;
      if (getCalls === 1) throw new ApiError(0, "could not reach the server");
      // The FRESH row, not the stale one, is what must decide the stop.
      return row("pending", { overlay_responding: false, claimed_by: 999 });
    },
  });
  const result = await pollCommand(api, stale, { sleep: noWait, checkFirst: false });
  assert.equal(result.stopped, "no-consumer");
  assert.equal(getCalls, 2, "a second GET must be attempted rather than judging the stale row after the first failed");
  assert.equal(result.row.claimed_by, 999, "the reported row must be the fresh one that was actually read");
});

test("lost-contact names the command id and is not styled as a failure", () => {
  const outcome = describeOutcome({ row: row("pending"), stopped: "lost-contact" }, "x");
  assert.equal(outcome.kind, "lost-contact");
  assert.match(outcome.message, /command #41/);
  assert.notEqual(outcome.kind, "failed");
});

test("unwatchable names the command id and the API's own message, styled the same as lost-contact", () => {
  const error = new ApiError(403, "not permitted: commands.read");
  const outcome = describeOutcome({ row: row("pending"), stopped: "unwatchable", error }, "x");
  assert.equal(outcome.kind, "lost-contact");
  assert.equal(
    outcome.message,
    "Command #41 was queued, but its outcome can't be read: not permitted: commands.read. "
    + "Check History before sending this again.",
  );
  assert.notEqual(outcome.kind, "failed");
});

// --- what the row means ---------------------------------------------------

test("executed is reported as read back, with the command's id", () => {
  const outcome = describeOutcome({ row: row("executed"), stopped: null }, "Gave Jellopy ×3 to Kami");
  assert.equal(outcome.kind, "done");
  assert.equal(outcome.message, "Gave Jellopy ×3 to Kami: executed (command #41, read back from the queue).");
});

test("an offline character is a refusal, worded as one", () => {
  const failed = row("failed", { error_message: "character is not online" });
  const outcome = describeOutcome({ row: failed, stopped: null }, "x");
  assert.equal(outcome.kind, "refused");
  assert.match(outcome.message, /^Not applied: character is not online/);
});

test("an unpersisted sync is expected, and offers a retry", () => {
  const failed = row("failed", {
    action: "sync_character", error_message: "flush queued but not yet persisted - retry",
  });
  assert.equal(describeOutcome({ row: failed, stopped: null }, "x").kind, "retry");
});

test("the same 'not yet persisted' wording on any OTHER action is not offered a retry", () => {
  // Gated on the action, not just the text: error_message is whatever the
  // game recorded, and this phrase only ever means "expected" for a sync.
  const failed = row("failed", { error_message: "flush queued but not yet persisted - retry" });
  const outcome = describeOutcome({ row: failed, stopped: null }, "x");
  assert.equal(outcome.kind, "failed");
  assert.ok(outcome.message.includes("flush queued but not yet persisted - retry"));
});

test("any other failure is quoted as the game recorded it", () => {
  const reason = "could not attach - player is busy in a script or offline";
  const outcome = describeOutcome({ row: row("failed", { error_message: reason }), stopped: null }, "x");
  assert.equal(outcome.kind, "failed");
  assert.ok(outcome.message.includes(reason));
});

test("a stopped wait says the row is still pending, not that it failed", () => {
  const outcome = describeOutcome({ row: row("pending"), stopped: "no-consumer" }, "x");
  assert.equal(outcome.kind, "stopped");
  assert.match(outcome.message, /still pending/);
});

test("a stopped wait mid-processing says the game may already have applied it", () => {
  const outcome = describeOutcome({ row: row("processing"), stopped: "no-consumer" }, "x");
  assert.equal(outcome.kind, "stopped");
  assert.match(outcome.message, /being processed when its consumer stopped responding/);
  assert.match(outcome.message, /may or may not have applied it/);
});

test("a timeout says the write may still run, never that the consumer stopped responding", () => {
  // Unlike no-consumer, a timeout is read off a row that CAN still say
  // overlay_responding: true -- the consumer may just be behind a backlog --
  // so it must never claim the consumer stopped, or promise it will come
  // back, the way the no-consumer wording above does.
  const pending = describeOutcome({ row: row("pending"), stopped: "timeout", waitMs: 30000 }, "x");
  assert.equal(pending.kind, "stopped");
  assert.match(pending.message, /Stopped waiting after 30s: command #41 is still pending\./);
  assert.match(pending.message, /It may still run;/);
  assert.doesNotMatch(pending.message, /stopped responding/);
  assert.doesNotMatch(pending.message, /if the consumer comes back/);

  const processing = describeOutcome({ row: row("processing"), stopped: "timeout", waitMs: 30000 }, "x");
  assert.equal(processing.kind, "stopped");
  assert.match(processing.message, /is still processing\. It may still run or may already have been applied;/);
  assert.doesNotMatch(processing.message, /stopped responding/);
});

test("a timeout with no known wait duration still reads, just without a number", () => {
  const outcome = describeOutcome({ row: row("pending"), stopped: "timeout" }, "x");
  assert.equal(outcome.kind, "stopped");
  assert.match(outcome.message, /^Stopped waiting: command #41 is still pending\./);
});

// --- the forms --------------------------------------------------------------

const KAMI = { char_id: 150000, name: "Kami" };
const REASON = "overlay not installed: run overlay/schema.sql against this database";
const EVERYTHING = [
  { action: "give_item", label: "Give item", available: true, reason: "ok" },
  { action: "adjust_zeny", label: "Adjust zeny", available: true, reason: "ok" },
  { action: "sync_character", label: "Sync from game", available: true, reason: "ok" },
];

function mount(api, options = {}) {
  const root = document.createElement("section");
  document.body.append(root);
  renderActions(root, {
    api, character: KAMI, plan: EVERYTHING, onChanged() {},
    sleep: noWait, debounceMs: 0, ...options,
  });
  return root;
}

const formTitled = (root, title) =>
  [...root.querySelectorAll("form")].find((f) => f.querySelector("h3").textContent === title);

test("an unavailable action shows the API's reason, and no form", () => {
  const root = mount(fakeApi({}), {
    plan: [{ action: "give_item", label: "Give item", available: false, reason: REASON }],
  });
  assert.equal(root.querySelectorAll("form").length, 0);
  assert.ok(root.textContent.includes(`Give item unavailable: ${REASON}`));
});

test("removing zeny asks first, stating the amount, and sends nothing if declined", async () => {
  const asked = [];
  const api = fakeApi({ "POST commands": row("executed") });
  const root = mount(api, { confirmFn: (question) => { asked.push(question); return false; } });
  const form = formTitled(root, "Adjust zeny");
  form.querySelector('input[name="delta"]').value = "-5000";
  submit(form);
  await tick();
  assert.deepEqual(asked, ["Remove 5,000 zeny from Kami? This cannot be undone."]);
  assert.equal(api.calls.length, 0);
  assert.match(form.textContent, /Cancelled\. Nothing was sent\./);
});

test("confirm: true is sent only after the person agreed", async () => {
  const api = fakeApi({ "POST commands": row("executed", { action: "adjust_zeny" }) });
  const root = mount(api, { confirmFn: () => true });
  const form = formTitled(root, "Adjust zeny");
  form.querySelector('input[name="delta"]').value = "-5000";
  submit(form);
  await tick();
  assert.deepEqual(api.calls[0].arg, {
    action: "adjust_zeny", char_id: 150000, delta: -5000, confirm: true,
  });
});

test("adding zeny neither asks nor sends confirm", async () => {
  let asked = 0;
  const api = fakeApi({ "POST commands": row("executed", { action: "adjust_zeny" }) });
  const root = mount(api, { confirmFn: () => { asked += 1; return true; } });
  const form = formTitled(root, "Adjust zeny");
  form.querySelector('input[name="delta"]').value = "250";
  submit(form);
  await tick();
  assert.equal(asked, 0);
  assert.deepEqual(api.calls[0].arg, { action: "adjust_zeny", char_id: 150000, delta: 250 });
});

test("adjust_zeny refuses 0 and fractional deltas before any dialog", async () => {
  const asked = [];
  const api = fakeApi({});
  const root = mount(api, { confirmFn: (question) => { asked.push(question); return true; } });
  const form = formTitled(root, "Adjust zeny");
  form.querySelector('input[name="delta"]').value = "-0.0001";
  submit(form);
  await tick();
  assert.equal(asked.length, 0);
  assert.match(form.textContent, /Enter a whole number of zeny, not 0\./);
  assert.equal(api.calls.length, 0);
});

test("adjust_zeny refuses a delta of exactly 0", async () => {
  const api = fakeApi({});
  const root = mount(api);
  const form = formTitled(root, "Adjust zeny");
  form.querySelector('input[name="delta"]').value = "0";
  submit(form);
  await tick();
  assert.match(form.textContent, /Enter a whole number of zeny, not 0\./);
  assert.equal(api.calls.length, 0);
});

test("an item is chosen from the server's item search, then granted and read back", async () => {
  let changed = 0;
  const api = fakeApi({
    "GET items": { items: [{ id: 909, name_english: "Jellopy" }], total: 1, limit: 8, offset: 0 },
    "POST commands": row("pending"),
    "GET commands/41": row("executed"),
  });
  const root = mount(api, { onChanged: () => { changed += 1; } });
  const form = formTitled(root, "Give item");
  type(form.querySelector('input[name="item"]'), "Jell");
  await tick(5);
  [...form.querySelectorAll("button")].find((b) => b.textContent.includes("Jellopy")).click();
  form.querySelector('input[name="amount"]').value = "3";
  submit(form);
  await tick(5);
  assert.deepEqual(
    api.calls.find((c) => c.method === "POST").arg,
    { action: "give_item", char_id: 150000, item_id: 909, amount: 3 },
  );
  assert.match(form.textContent, /Gave Jellopy ×3 to Kami: executed \(command #41/);
  assert.equal(changed, 1);
});

test("giving without choosing an item sends nothing", async () => {
  const api = fakeApi({});
  const root = mount(api);
  submit(formTitled(root, "Give item"));
  await tick();
  assert.equal(api.calls.length, 0);
  assert.match(root.textContent, /Choose an item first\./);
});

test("give_item refuses a non-integer or non-positive amount", async () => {
  const api = fakeApi({
    "GET items": { items: [{ id: 909, name_english: "Jellopy" }], total: 1, limit: 8, offset: 0 },
  });
  const root = mount(api);
  const form = formTitled(root, "Give item");
  type(form.querySelector('input[name="item"]'), "Jell");
  await tick(5);
  [...form.querySelectorAll("button")].find((b) => b.textContent.includes("Jellopy")).click();
  form.querySelector('input[name="amount"]').value = "0";
  submit(form);
  await tick();
  assert.match(form.textContent, /Enter a whole number of items\./);
  assert.equal(api.calls.filter((c) => c.method === "POST").length, 0);
});

// --- the item picker's own keystroke race -----------------------------------

test("the item picker's newest keystroke wins, even if an earlier answer lands inside the next debounce window", async () => {
  // Mirrors search.js's own fix (commit 04cbd09): the race-guard counter is
  // bumped in the input handler, at the keystroke, not inside the debounce
  // timer -- otherwise a slow answer to an earlier keystroke can still
  // land while a LATER keystroke's own timer has not yet fired.
  let releaseJell;
  const jellSlow = new Promise((resolve) => { releaseJell = resolve; });
  const api = fakeApi({
    "GET items": async (params) => {
      if (params.q === "Jell") { await jellSlow; return { items: [{ id: 909, name_english: "Jellopy" }], total: 1, limit: 8, offset: 0 }; }
      if (params.q === "Fly") return { items: [{ id: 715, name_english: "Fly Wing" }], total: 1, limit: 8, offset: 0 };
      return { items: [], total: 0, limit: 8, offset: 0 };
    },
  });
  const debounceMs = 30;
  const root = mount(api, { debounceMs });
  const form = formTitled(root, "Give item");
  const input = form.querySelector('input[name="item"]');

  type(input, "Jell");
  await tick(debounceMs + 5); // Jell's debounce fires; its slow fetch is now in flight
  type(input, "Fly"); // typed while Jell's fetch is outstanding, before Fly's own timer fires
  releaseJell(); // Jell's answer lands before Fly's debounce window has even ended
  await tick(5);
  assert.ok(![...form.querySelectorAll("button")].some((b) => b.textContent.includes("Jellopy")));
  await tick(debounceMs + 5); // Fly's own timer now fires
  assert.ok([...form.querySelectorAll("button")].some((b) => b.textContent.includes("Fly Wing")));
});

test("the previous query's match buttons are gone the instant a new keystroke is typed, not clickable during its debounce", async () => {
  const api = fakeApi({
    "GET items": { items: [{ id: 909, name_english: "Jellopy" }], total: 1, limit: 8, offset: 0 },
  });
  const debounceMs = 30;
  const root = mount(api, { debounceMs });
  const form = formTitled(root, "Give item");
  const input = form.querySelector('input[name="item"]');

  type(input, "Jell");
  await tick(debounceMs + 5); // Jellopy's match button is now on screen
  assert.ok([...form.querySelectorAll("button")].some((b) => b.textContent.includes("Jellopy")));
  type(input, "Jellx"); // one more keystroke; its own debounce has not fired yet
  assert.ok(
    ![...form.querySelectorAll("button")].some((b) => b.textContent.includes("Jellopy")),
    "the previous match must be gone immediately, not still clickable while the new query debounces",
  );
});

test("a numeric item lookup's non-404 failure is shown, not swallowed as no match", async () => {
  const api = fakeApi({
    "GET items": { items: [], total: 0, limit: 8, offset: 0 },
    "GET items/909": new ApiError(500, "the server failed (500) without saying why; its log has the detail"),
  });
  const root = mount(api);
  const form = formTitled(root, "Give item");
  type(form.querySelector('input[name="item"]'), "909");
  await tick(5);
  assert.match(form.textContent, /the server failed \(500\)/);
  assert.ok(!form.textContent.includes("No matching item."));
});

test("a hostile item name renders as text, never markup", async () => {
  const api = fakeApi({
    "GET items": { items: [{ id: 1, name_english: HOSTILE }], total: 1, limit: 8, offset: 0 },
    "POST commands": row("executed"),
  });
  const root = mount(api);
  const form = formTitled(root, "Give item");
  type(form.querySelector('input[name="item"]'), "x");
  await tick(5);
  assert.equal(markupIn(form).length, 0);
  const chosenButton = [...form.querySelectorAll("button")].find((b) => b.textContent.includes(HOSTILE));
  assert.ok(chosenButton, "the hostile name is shown, as text, on its own match button");
  chosenButton.click();
  submit(form);
  await tick();
  assert.equal(markupIn(form).length, 0);
  assert.ok(form.textContent.includes(HOSTILE));
});

test("an unpersisted sync offers Retry, which sends it again", async () => {
  let posts = 0;
  const api = fakeApi({
    "POST commands": () => {
      posts += 1;
      return posts === 1
        ? row("failed", { action: "sync_character", error_message: "flush queued but not yet persisted - retry" })
        : row("executed", { action: "sync_character" });
    },
  });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  submit(form);
  await tick();
  const retry = [...form.querySelectorAll("button")].find((b) => b.textContent === "Retry");
  assert.ok(retry, "a Retry button is offered");
  retry.click();
  await tick();
  assert.equal(posts, 2);
  assert.match(form.textContent, /Synced Kami: executed/);
});

test("a double-click on Retry sends the write only once", async () => {
  let posts = 0;
  const api = fakeApi({
    "POST commands": () => {
      posts += 1;
      return posts === 1
        ? row("failed", { action: "sync_character", error_message: "flush queued but not yet persisted - retry" })
        : row("executed", { action: "sync_character" });
    },
  });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  submit(form);
  await tick();
  const retry = [...form.querySelectorAll("button")].find((b) => b.textContent === "Retry");
  retry.click();
  retry.click();
  await tick();
  assert.equal(posts, 2);
});

test("submitting again while a write is still in flight sends nothing more", async () => {
  let posts = 0;
  const api = fakeApi({ "POST commands": () => { posts += 1; return row("executed", { action: "sync_character" }); } });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  submit(form);
  submit(form);
  await tick();
  assert.equal(posts, 1);
});

// --- outcome styling resets at the start of every run ----------------------

test("a second run is pending-styled the moment it starts, even right after a green one", async () => {
  let posts = 0;
  const api = fakeApi({
    "POST commands": () => { posts += 1; return posts === 1 ? row("executed") : row("pending"); },
    "GET commands/41": row("executed"),
  });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  const outcome = form.querySelector(".outcome");
  submit(form);
  await tick();
  assert.equal(outcome.className, "outcome done");
  submit(form);
  // Checked with nothing awaited yet: the class must flip to pending in the
  // same synchronous tick the second run starts, not only once it settles.
  assert.equal(outcome.className, "outcome pending");
  await tick();
});

// --- a stopped wait offers Check again, GET only, never a re-post ----------

test("a reachable no-consumer stop is worded around 'still pending' and offers Check again", async () => {
  const api = fakeApi({
    "POST commands": row("pending", { overlay_responding: true }),
    "GET commands/41": row("pending", { overlay_responding: false }),
  });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  submit(form);
  await tick();
  assert.match(form.textContent, /is still pending/);
  assert.match(form.textContent, /will still run if the consumer comes back/);
  const again = [...form.querySelectorAll("button")].find((b) => b.textContent === "Check again");
  assert.ok(again, "a Check again button is offered");
  assert.ok(!form.querySelector(".outcome").className.includes("failed"));
});

test("a stop mid-processing says the game may already have applied it", async () => {
  const api = fakeApi({
    "POST commands": row("pending", { overlay_responding: true }),
    "GET commands/41": row("processing", { overlay_responding: false }),
  });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  submit(form);
  await tick();
  assert.match(form.textContent, /being processed when its consumer stopped responding/);
  assert.match(form.textContent, /may or may not have applied it/);
});

test("Check again resumes with GET only, reaches executed, and calls onChanged", async () => {
  let changed = 0;
  const api = fakeApi({
    "POST commands": row("pending", { overlay_responding: true, action: "sync_character" }),
    "GET commands/41": inTurn(
      row("pending", { overlay_responding: false, action: "sync_character" }),
      row("processing", { overlay_responding: true, action: "sync_character" }),
      row("executed", { action: "sync_character" }),
    ),
  });
  const root = mount(api, { onChanged: () => { changed += 1; } });
  const form = formTitled(root, "Sync from game");
  submit(form);
  await tick();
  const again = [...form.querySelectorAll("button")].find((b) => b.textContent === "Check again");
  assert.ok(again, "a Check again button is offered");
  again.click();
  await tick();
  assert.equal(api.calls.filter((c) => c.method === "POST").length, 1);
  assert.ok(api.calls.slice(1).every((c) => c.method === "GET"));
  assert.match(form.textContent, /Synced Kami: executed/);
  assert.equal(changed, 1);
});

test("Check again does not repeat 'is still pending' right after 'Checking...' already said so", async () => {
  // lastStatus is seeded with the row's OWN status when Check again starts,
  // not null -- otherwise the first fresh read coming back with that same
  // status re-renders as if it had moved, right after "Checking..." already
  // named it.
  let releaseGet2, releaseGet3;
  const get2Gate = new Promise((resolve) => { releaseGet2 = resolve; });
  const get3Gate = new Promise((resolve) => { releaseGet3 = resolve; });
  let getCalls = 0;
  const api = fakeApi({
    "POST commands": row("pending", { overlay_responding: false, action: "sync_character" }),
    "GET commands/41": async () => {
      getCalls += 1;
      if (getCalls === 1) { await get2Gate; return row("pending", { overlay_responding: true, action: "sync_character" }); }
      await get3Gate;
      return row("executed", { action: "sync_character" });
    },
  });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  submit(form);
  await tick(); // POST returns pending/overlay_responding:false -> stops immediately, no GET yet
  const again = [...form.querySelectorAll("button")].find((b) => b.textContent === "Check again");
  assert.ok(again);
  const outcome = form.querySelector(".outcome");

  again.click();
  await tick();
  assert.match(outcome.textContent, /Checking… command #41 is pending\./);
  const nodeAfterChecking = outcome.firstChild;

  releaseGet2(); // the resumed poll's first fresh read: SAME status, "pending"
  await tick();
  assert.equal(outcome.firstChild, nodeAfterChecking, "the same status read right after 'Checking...' must not re-render");

  releaseGet3(); // now it actually changes, to "executed"
  await tick();
  assert.match(form.textContent, /Synced Kami: executed/);
});

test("a refusal before anything is queued is shown in the API's words", async () => {
  const reason = "overlay script last responded 46s ago (stale after 10s); is the map server running?";
  const api = fakeApi({ "POST commands": new ApiError(409, reason) });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  submit(form);
  await tick();
  assert.ok(form.textContent.includes(reason));
});

// --- maybeApplied: no answer at all is not a green light to retry ----------

test("a maybeApplied failure shows the History wording, offers no instant Retry, and re-enables the submit button", async () => {
  const message =
    "No answer from the server, so this may or may not have been applied. Check History before retrying.";
  const api = fakeApi({ "POST commands": new ApiError(0, message, true) });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  const button = form.querySelector('button[type="submit"]');
  submit(form);
  await tick();
  assert.match(form.textContent, /Check History before retrying\./);
  assert.equal([...form.querySelectorAll("button")].some((b) => b.textContent === "Retry"), false);
  assert.equal(button.disabled, false);
});

test("a hostile error message renders as text, never markup", async () => {
  const api = fakeApi({ "POST commands": new ApiError(409, HOSTILE) });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  submit(form);
  await tick();
  assert.equal(markupIn(form).length, 0);
  assert.ok(form.textContent.includes(HOSTILE));
});

// --- the live outcome region announces a change, not every poll tick -------

test("the outcome's live region is not re-rendered for a poll that reports the same status again", async () => {
  let releasePost, releaseGet1, releaseGet2;
  const postGate = new Promise((resolve) => { releasePost = resolve; });
  const get1Gate = new Promise((resolve) => { releaseGet1 = resolve; });
  const get2Gate = new Promise((resolve) => { releaseGet2 = resolve; });
  let getCalls = 0;
  const api = fakeApi({
    "POST commands": async () => { await postGate; return row("pending", { action: "sync_character" }); },
    "GET commands/41": async () => {
      getCalls += 1;
      if (getCalls === 1) { await get1Gate; return row("pending", { action: "sync_character" }); }
      await get2Gate;
      return row("executed", { action: "sync_character" });
    },
  });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  const outcome = form.querySelector(".outcome");

  submit(form);
  releasePost();
  await tick();
  assert.match(outcome.textContent, /is pending/);
  const nodeAfterPost = outcome.firstChild;

  releaseGet1(); // the poll's first GET reports the SAME status, "pending"
  await tick();
  assert.equal(outcome.firstChild, nodeAfterPost, "an unchanged status must not re-render the live region");

  releaseGet2(); // the second GET reports "executed"; this one DOES render
  await tick();
  assert.match(form.textContent, /Synced Kami: executed/);
});

// --- onChanged only ever fires for a write the queue actually proved -------

test("onChanged is never called for a refused, failed, or stopped outcome", async () => {
  let changed = 0;
  const onChanged = () => { changed += 1; };

  const refused = fakeApi({ "POST commands": row("failed", { error_message: "character is not online" }) });
  submit(formTitled(mount(refused, { onChanged }), "Sync from game"));
  await tick();

  const failed = fakeApi({ "POST commands": row("failed", { error_message: "some other reason entirely" }) });
  submit(formTitled(mount(failed, { onChanged }), "Sync from game"));
  await tick();

  const stopped = fakeApi({ "POST commands": row("pending", { overlay_responding: false }) });
  submit(formTitled(mount(stopped, { onChanged }), "Sync from game"));
  await tick();

  assert.equal(changed, 0);
});

// --- a 4xx mid-poll other than 401 is final, worded plainly, never styled
// as a failure ---------------------------------------------------------------

test("a 403 mid-poll shows the API's own message with the command id, not 'Lost contact', and not failed styling", async () => {
  const api = fakeApi({
    "POST commands": row("pending", { action: "sync_character" }),
    "GET commands/41": new ApiError(403, "not permitted: commands.read"),
  });
  const root = mount(api);
  const form = formTitled(root, "Sync from game");
  submit(form);
  await tick();
  assert.match(
    form.textContent,
    /Command #41 was queued, but its outcome can't be read: not permitted: commands\.read\./,
  );
  assert.match(form.textContent, /Check History before sending this again\./);
  assert.doesNotMatch(form.textContent, /Lost contact/);
  assert.ok(!form.querySelector(".outcome").className.includes("failed"));
});

// --- work must not continue once the view holding it is gone ---------------

test("polling stops the lap after the form leaves the document, and sends no further GETs", async () => {
  // Reproduces navigating away (Back, or opening another character) while a
  // write is still being watched: without the isLive check, pollCommand
  // would keep sending GETs -- up to maxPolls of them -- for a form nobody
  // can see any more, and could eventually fire the post-executed dossier
  // refresh (onChanged) for a view that no longer exists.
  let form;
  let getCalls = 0;
  let changed = 0;
  const api = fakeApi({
    "POST commands": row("pending", { action: "sync_character" }),
    "GET commands/41": () => {
      getCalls += 1;
      // Simulate a navigation removing this form from the document exactly
      // once the first poll's GET has gone out, so the SECOND lap is the
      // one that must be stopped by the isLive check, not sent.
      if (getCalls === 1) form.remove();
      return row("processing", { action: "sync_character" });
    },
  });
  const root = mount(api, { onChanged: () => { changed += 1; } });
  form = formTitled(root, "Sync from game");
  submit(form);
  await tick(10);
  assert.equal(getCalls, 1, "no GET after the form is detached");
  assert.equal(changed, 0, "no dossier refresh for a view that is no longer on screen");
});

test("isLive defaults to always-live, so runCommand's result semantics are unchanged for a poll that mounts no form", async () => {
  // Every other test in this file calls runCommand/pollCommand directly,
  // with no `isLive` option and no form at all -- this pins that the
  // default keeps them polling to completion exactly as before.
  const api = fakeApi({
    "POST commands": row("pending"),
    "GET commands/41": inTurn(row("processing"), row("executed")),
  });
  const result = await runCommand(api, {}, { sleep: noWait });
  assert.equal(result.row.status, "executed");
  assert.equal(result.stopped, null);
});
