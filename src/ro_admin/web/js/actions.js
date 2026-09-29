/**
 * Writes: the one place the UI changes the game, and so the one place it is
 * most tempted to report something it has not seen.
 *
 * THE RULE: once a POST has returned a row id, this module must never
 * invite a re-send of that write. It may only offer to look again at the
 * same id, using GET only.
 *
 * A 202 means "accepted", not "done". The predecessor answered an enqueue
 * with "Updated successfully" while nothing reached the game. Here nothing
 * reads as done until the queue row itself reads `executed`.
 *
 * A row can stop being watched several ways, and none of them are "failed":
 *   - `no-consumer`: the last row this poll actually read said nothing is
 *     listening. The row itself never expires -- a pending row has no age
 *     limit, and a processing row whose consumer died is never reclaimed --
 *     so the write may still run, or may already have.
 *   - `timeout`: the wait simply ran out. Unlike `no-consumer` this says
 *     NOTHING about the consumer -- the last row read can still say
 *     `overlay_responding: true`, just behind a backlog -- so it is worded
 *     without claiming the consumer stopped or promising it will come back.
 *   - `lost-contact`: the GET itself started failing mid-poll (a network
 *     blip, a 5xx). A 401 ends the session immediately, through api.js's
 *     own hook, and is reported as lost-contact without being retried. The
 *     id still exists either way; only watching it stopped.
 *   - `unwatchable`: the GET answered with some OTHER 4xx mid-poll -- in
 *     practice a 403 or 404, the shape a token scoped to `commands.write`
 *     without `commands.read` gets on every single poll. Unlike a real
 *     lost-contact blip this will never clear on its own, so it stops the
 *     instant it is seen rather than spending lost-contact's two tolerated
 *     retries first -- but it is worded and styled exactly like
 *     lost-contact, never like a failure: the row may still be running,
 *     this UI just cannot watch it.
 * All four render a **Check again** button, which resumes polling that
 * SAME row id with GET only -- never a re-post. See `pollCommand`.
 *
 * A write can also fail before anything was ever queued, in a way whose
 * outcome on the server is genuinely unknown -- no answer at all, or a 5xx
 * after the request had already arrived. `api.js` marks that shape
 * `maybeApplied` and folds a "check History before retrying" sentence into
 * the message itself, shown exactly as sent, with no instant Retry next to
 * it: retrying blind is the one thing a maybe-applied write must not invite.
 * The only Retry this module offers at all is for `not yet persisted`, an
 * outcome the game itself recorded in the row, not a guess about one it
 * never confirmed -- and only for the action that outcome actually belongs
 * to.
 */
import { el, replace } from "./dom.js";
import { number } from "./format.js";

const TERMINAL = new Set(["executed", "failed"]);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Polls an EXISTING row by id, GET only. Once a POST has returned a row id,
// nothing in this module may invite a re-send of that write; this is the
// only function that watches a row after that point, and both `runCommand`
// (right after the POST) and a "Check again" button (after a stopped wait)
// call it with the same row.
//
// `checkFirst` decides whether the row handed in is trusted to judge
// `overlay_responding` before ever polling. `runCommand` passes a row fresh
// off the POST, so that trust is earned and an already-dead consumer is
// reported without wasting a poll. A "Check again" click hands back the
// very row that caused an earlier stop -- checking ITS `overlay_responding`
// first would immediately re-stop without ever asking again, since nothing
// about that row changes on its own. `checkFirst: false` skips that one
// check, so the first lap always fetches a fresh row before judging
// anything; every lap after that judges the row it just read, same as ever.
//
// `isLive` is checked at the top of every lap, before any of the above --
// default always-live, so every existing caller that polls without ever
// mounting a form (most of this file's own tests included) is unaffected.
// `commandForm` passes `() => form.isConnected`: once a navigation removes
// the form from the document, nothing on screen is watching this row any
// more, so the very next lap stops rather than sending another GET (or,
// worse, eventually reading `executed` and firing a dossier refresh for a
// view nobody can see). It simply returns; nothing renders, because there
// is nothing left on screen to render into.
export async function pollCommand(
  api, row, {
    onUpdate = () => {}, sleep = wait, intervalMs = 1000, maxPolls = 30, checkFirst = true,
    isLive = () => true,
  } = {},
) {
  let failures = 0;
  let judge = checkFirst;
  for (let polls = 0; !TERMINAL.has(row.status); polls += 1) {
    if (!isLive()) return { row, stopped: "detached" };
    if (judge && !row.overlay_responding) return { row, stopped: "no-consumer" };
    if (polls >= maxPolls) return { row, stopped: "timeout", waitMs: maxPolls * intervalMs };
    await sleep(intervalMs);
    try {
      row = await api.get(`commands/${row.id}`);
    } catch (error) {
      // api.js has already ended the session for a 401 whose token was still
      // current by the time it sent this GET; retrying would just draw
      // another 401, and there is no session left to retry it under, so
      // this gives up on watching immediately rather than spending its two
      // tolerated failures on a request that cannot succeed.
      if (error.status === 401) return { row, stopped: "lost-contact", error };
      // Any OTHER 4xx mid-poll is equally final, just for a different
      // reason: it is not that the session ended, it is that this token can
      // read this row no better on the next poll than it can on this one
      // (a 403/404 from a token scoped to commands.write without
      // commands.read, in practice) -- so spending the transient-failure
      // budget below on it would just delay reaching the same answer three
      // times slower. `describeOutcome` reports this distinctly from
      // lost-contact in words (it names the API's own message), but styles
      // it identically -- see the module docstring's `unwatchable`.
      if (error.status >= 400 && error.status < 500) {
        return { row, stopped: "unwatchable", error };
      }
      failures += 1;
      // A network blip or a 5xx mid-poll is not "the write failed": the row
      // already exists, and there is no list endpoint to find its id again
      // if it is lost here. Two transient failures are tolerated in case the
      // very next poll simply succeeds; a third gives up on watching, not on
      // the write -- the row may still run, or may already have.
      if (failures > 2) return { row, stopped: "lost-contact", error };
      continue;
    }
    failures = 0;
    // Only a row this function actually just read is fit to judge on the
    // NEXT lap. Setting this unconditionally, right after the check above,
    // would let a lap whose GET just failed still judge -- on the following
    // lap -- the STALE row it was handed, never having read anything fresh.
    // That matters most for `checkFirst: false`: a Check again's first GET
    // failing must not fall back to trusting the old row's own
    // `overlay_responding`, the exact stale judgement `checkFirst: false`
    // exists to avoid.
    judge = true;
    onUpdate(row);
  }
  return { row, stopped: null };
}

export async function runCommand(api, body, opts = {}) {
  const { onUpdate = () => {} } = opts;
  // The 202's status is the row's REAL status, and may already be terminal:
  // the overlay can finish a row between the API's insert and its read-back.
  const row = await api.post("commands", body);
  onUpdate(row);
  return pollCommand(api, row, opts);
}

export function describeOutcome({ row, stopped, waitMs, error }, summary) {
  const ref = `command #${row.id}`;
  if (stopped === "unwatchable") {
    // Styled exactly like lost-contact (see finish()'s use of `kind` below)
    // -- this must never read as a failure, because nothing here says the
    // write did not run. Unlike lost-contact it names the API's own refusal
    // rather than the row's last status, because that refusal, not a
    // network blip, is the reason watching stopped.
    return {
      kind: "lost-contact",
      message:
        `Command #${row.id} was queued, but its outcome can't be read: ${error.message}. ` +
        "Check History before sending this again.",
    };
  }
  if (stopped === "lost-contact") {
    return {
      kind: "lost-contact",
      message:
        `Lost contact while waiting: ${ref} was queued and last read ${row.status}. ` +
        "It may still run. Check History before sending this again.",
    };
  }
  if (stopped === "no-consumer") {
    // The row itself never expires: a Tier 1 pending row has no age limit,
    // and a processing row whose consumer died mid-work is never reclaimed
    // by anything. Either way the write may already have reached the game,
    // or may still, so this is worded as neither -- never as a failure, and
    // never as a green light to send it again. `no-consumer` is read
    // straight off the LAST row this poll actually saw, so "stopped
    // responding" is a fact here, not a guess.
    if (row.status === "processing") {
      return {
        kind: "stopped",
        message:
          `Stopped waiting: ${ref} was being processed when its consumer stopped responding. ` +
          "The game may or may not have applied it; check the character and History before sending this again.",
      };
    }
    return {
      kind: "stopped",
      message:
        `Stopped waiting: ${ref} is still pending. It will still run if the consumer comes back; ` +
        "check History before sending this again.",
    };
  }
  if (stopped === "timeout") {
    // Unlike no-consumer, a timeout says nothing about the consumer at all:
    // the last row read here can carry `overlay_responding: true` -- the
    // consumer is alive, just behind a backlog. Claiming it "stopped
    // responding," or promising it will run "if the consumer comes back,"
    // would state something this poll never observed.
    const after = Number.isFinite(waitMs) ? ` after ${Math.round(waitMs / 1000)}s` : "";
    const also = row.status === "processing" ? " or may already have been applied" : "";
    return {
      kind: "stopped",
      message: `Stopped waiting${after}: ${ref} is still ${row.status}. It may still run${also}; check History before sending this again.`,
    };
  }
  if (row.status === "executed") {
    return { kind: "done", message: `${summary}: executed (${ref}, read back from the queue).` };
  }
  const reason = row.error_message ?? "no reason was recorded";
  // A refusal, not a breakage: changes go through the game, and the game
  // cannot apply them to a character who is not there.
  if (/not online/.test(reason)) {
    return { kind: "refused", message: `Not applied: ${reason} (${ref}).` };
  }
  // Expected on a first sync: the char server commits after the flush
  // returns. Gated on the action too, not just the wording -- error_message
  // is whatever the game recorded, and `not yet persisted` only ever means
  // this for a sync_character row. Offering a re-post off that phrase alone
  // for any other action would be trusting text the game never promised to
  // keep out of some other failure.
  if (row.action === "sync_character" && /not yet persisted/.test(reason)) {
    return {
      kind: "retry",
      message: `The game has not finished saving yet (${ref}). A retry is expected to succeed. `,
    };
  }
  return { kind: "failed", message: `Failed: ${reason} (${ref}).` };
}

// The action-to-label table this UI's forms are keyed by. `capabilities.js`
// no longer holds a copy of it; the dossier passes this one straight to
// `actionPlan(me, caps, LABELS)`. Kept next to `FORMS` -- and pinned equal to
// it by a test -- so the two cannot drift apart the way two separate copies
// of the same table always eventually do.
export const LABELS = {
  give_item: "Give item",
  adjust_zeny: "Adjust zeny",
  sync_character: "Sync from game",
};

export function renderActions(
  root,
  { api, character, plan, onChanged, confirmFn = (question) => window.confirm(question), sleep, intervalMs, debounceMs },
) {
  const context = { api, character, onChanged, confirmFn, sleep, intervalMs, debounceMs };
  replace(
    root,
    el("h2", {}, "Actions"),
    plan.map((entry) =>
      entry.available
        ? FORMS[entry.action](context)
        : el("p", { class: "unavailable" }, `${entry.label} unavailable: ${entry.reason}`),
    ),
  );
}

// Exported only so a test can pin its keys against LABELS without the two
// tables drifting apart; nothing outside this module calls it directly.
export const FORMS = {
  give_item(context) {
    const picker = itemPicker(context.api, context.debounceMs);
    const amount = el("input", { type: "number", name: "amount", value: 1, "aria-label": "Amount" });
    return commandForm("Give item", [picker.node, el("label", {}, "Amount", amount)], {
      ...context,
      submitLabel: "Give",
      build() {
        const item = picker.chosen();
        if (!item) return { notice: "Choose an item first." };
        const count = Number(amount.value);
        // Whole numbers only, checked here so a stray "3.5" or "-1" never
        // reaches a dialog or a POST. Upper bounds stay the API's to enforce
        // and to word -- its 422 is shown as sent, not guessed at here.
        if (!Number.isSafeInteger(count) || count <= 0) {
          return { notice: "Enter a whole number of items." };
        }
        return {
          body: { action: "give_item", char_id: context.character.char_id, item_id: item.id, amount: count },
          summary: `Gave ${item.name} ×${number(count)} to ${context.character.name}`,
        };
      },
    });
  },

  adjust_zeny(context) {
    const delta = el("input", {
      type: "number", name: "delta", placeholder: "5000, or -5000 to remove", "aria-label": "Zeny change",
    });
    return commandForm("Adjust zeny", [el("label", {}, "Change by", delta)], {
      ...context,
      submitLabel: "Apply",
      build() {
        const value = Number(delta.value);
        // Whole numbers only, and never 0 -- checked before any dialog, so
        // "-0.0001" never asks to "Remove 0 zeny". Upper bounds stay the
        // API's to enforce and to word.
        if (!Number.isSafeInteger(value) || value === 0) {
          return { notice: "Enter a whole number of zeny, not 0." };
        }
        const body = { action: "adjust_zeny", char_id: context.character.char_id, delta: value };
        if (value < 0) {
          // The API refuses a negative delta without confirm=true, whoever
          // the caller. Sending it only after a person has read the amount is
          // what keeps that gate a gate rather than a formality.
          const question = `Remove ${number(-value)} zeny from ${context.character.name}? This cannot be undone.`;
          if (!context.confirmFn(question)) return { notice: "Cancelled. Nothing was sent." };
          body.confirm = true;
        }
        return {
          body,
          summary: value < 0
            ? `Removed ${number(-value)} zeny from ${context.character.name}`
            : `Added ${number(value)} zeny to ${context.character.name}`,
        };
      },
    });
  },

  sync_character(context) {
    return commandForm(
      "Sync from game",
      [el("p", {}, "Write this character's live state to the database, then check that it landed.")],
      {
        ...context,
        submitLabel: "Sync",
        build: () => ({
          body: { action: "sync_character", char_id: context.character.char_id },
          summary: `Synced ${context.character.name}`,
        }),
      },
    );
  },
};

function commandForm(title, fields, { submitLabel, build, api, onChanged, sleep, intervalMs }) {
  const outcome = el("p", { class: "outcome", "aria-live": "polite" });
  const button = el("button", { type: "submit" }, submitLabel);
  const form = el("form", { class: "action" }, el("h3", {}, title), fields, button, outcome);

  // Read fresh on every lap of every poll this form starts (see
  // pollCommand's own comment on `isLive`) -- `form.isConnected` is false
  // from the moment a navigation removes this form from the document, so
  // a write submitted just before leaving a dossier does not keep polling,
  // or refresh it, for a view nobody can see any more.
  const isLive = () => form.isConnected;

  // One write in flight per form, at a time. Without this a double-click on
  // Retry posted the write twice, and a click on Retry or Check again could
  // race a fresh Submit. Checked wherever a new send or poll could start,
  // and it disables the submit button for the whole time it is true.
  let busy = false;
  function setBusy(value) {
    busy = value;
    button.disabled = value;
    // .busy is what app.css keys the progress cursor off of, so it shows
    // only while a request from THIS button is actually in flight --
    // never for actionButton()'s Retry/Check again below, which disable
    // themselves permanently on click with no busy state of their own.
    button.classList.toggle("busy", value);
  }

  function actionButton(label, onClick) {
    const btn = el("button", { type: "button" }, label);
    btn.addEventListener("click", () => {
      if (busy) return;
      // Disabled the instant it is clicked, not only once `busy` flips --
      // the two are set in the same tick here, but a button left enabled
      // until some later await would still take a second click in between.
      btn.disabled = true;
      onClick();
    });
    return btn;
  }

  // `outcome` is aria-live: a screen reader speaks it on every render. A row
  // that has not moved -- still "pending" on the next tick -- must not be
  // re-rendered just because a poll came back, or a slow write would read as
  // "pending, pending, pending, pending" instead of one sentence per change.
  // Reset at the start of every run and every Check again, so the first
  // update after either always speaks, even if the row's status happens to
  // match wherever the last run left off.
  let lastStatus = null;
  function reportProgress(row) {
    if (row.status === lastStatus) return;
    lastStatus = row.status;
    outcome.className = "outcome pending";
    replace(outcome, `Queued… command #${row.id} is ${row.status}.`);
  }

  function finish(result, request) {
    const { kind, message } = describeOutcome(result, request.summary);
    outcome.className = `outcome ${kind}`;
    const extra = [];
    if (kind === "retry") {
      // The one re-send this module offers: `not yet persisted` is an
      // outcome the game itself recorded in the row, not a guess.
      extra.push(actionButton("Retry", () => submitWrite(request)));
    } else if (kind === "stopped" || kind === "lost-contact") {
      // Never re-posts: it resumes watching the SAME row id, GET only.
      extra.push(actionButton("Check again", () => checkAgain(result.row, request)));
    }
    replace(outcome, message, ...extra);
    if (kind === "done") {
      // Outside the try/catch below, and outside this call stack entirely:
      // an exception from the dossier's own refresh must never be caught
      // here and repainted over an "executed" the queue row already proved.
      Promise.resolve().then(onChanged).catch(() => {});
    }
  }

  async function submitWrite(request) {
    if (busy) return;
    setBusy(true);
    lastStatus = null;
    outcome.className = "outcome pending";
    replace(outcome, "Sending…");
    try {
      const result = await runCommand(api, request.body, { sleep, intervalMs, isLive, onUpdate: reportProgress });
      finish(result, request);
    } catch (error) {
      // Only a failure of the POST itself lands here: a 409 when the
      // consuming script is not responding, a 422 for a value the API will
      // not accept, or a maybeApplied failure whose message already carries
      // the "check History" sentence. A GET failure while polling never
      // reaches here -- pollCommand catches it and reports lost-contact
      // instead, because by then a row id already exists and losing it would
      // leave no way back to it. None of the POST failures get an instant
      // Retry: the first two would just be refused again, and the third is
      // exactly the blind-retry risk the message is warning against.
      outcome.className = "outcome failed";
      replace(outcome, error.message);
    } finally {
      setBusy(false);
    }
  }

  // The only path back to a row after a stopped wait. GET only, always --
  // see pollCommand's own docs for why the row handed back in must not be
  // trusted to judge overlay_responding a second time.
  async function checkAgain(row, request) {
    if (busy) return;
    setBusy(true);
    // Seeded with the row's OWN status, not null: the "Checking…" line just
    // below already states it, so the first fresh read coming back with
    // that very same status must not repeat it right after as if it had
    // moved.
    lastStatus = row.status;
    outcome.className = "outcome pending";
    replace(outcome, `Checking… command #${row.id} is ${row.status}.`);
    try {
      const result = await pollCommand(api, row, {
        sleep, intervalMs, checkFirst: false, isLive, onUpdate: reportProgress,
      });
      finish(result, request);
    } catch (error) {
      // pollCommand itself no longer throws for a network failure -- it
      // reports lost-contact instead -- so a throw reaching here is a real
      // bug (in dom.js, say), not something worth inviting a resend over.
      outcome.className = "outcome failed";
      replace(outcome, error.message);
    } finally {
      setBusy(false);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (busy) return;
    const request = build();
    if (request.notice) {
      outcome.className = "outcome";
      replace(outcome, request.notice);
      return;
    }
    submitWrite(request);
  });
  return form;
}

function itemPicker(api, debounceMs = 250) {
  let chosen = null;
  let latest = 0;
  let timer = null;
  const input = el("input", {
    type: "search", name: "item", placeholder: "Item name or id", autocomplete: "off", spellcheck: "false",
  });
  const matches = el("div", { class: "matches" });
  // A short live line for "no matching item" and a lookup failure -- the
  // match buttons themselves are not live, the same split search.js made:
  // marking a list of up to eight buttons live would read the whole thing
  // out on every keystroke.
  const note = el("p", { class: "picker-note", "aria-live": "polite" });
  const status = el("p", { class: "chosen" }, "No item chosen.");

  function choose(item) {
    chosen = { id: item.id, name: item.name_english };
    replace(status, `Chosen: ${chosen.name} (#${chosen.id})`);
    replace(matches);
    replace(note);
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    chosen = null;
    replace(status, "No item chosen.");
    // Cleared at every keystroke, not only when the box empties: the
    // previous query's buttons are still clickable otherwise, and a click
    // during the new query's debounce window would choose an item that no
    // longer matches what is typed.
    replace(matches);
    replace(note);
    // Bumped at the KEYSTROKE, not once the debounce timer fires: a slow
    // answer to an earlier keystroke can otherwise land inside a LATER
    // keystroke's still-open debounce window -- before that keystroke's own
    // timer has fired and bumped anything -- and render under a query the
    // operator has already changed. See search.js, fixed the same way.
    const mine = ++latest;
    const text = input.value.trim();
    if (!text) return; // nothing to debounce when there is nothing to search for
    timer = setTimeout(async () => {
      try {
        // Names come from the server's item_db, never from a table in here.
        const lookups = [api.get("items", { q: text, limit: 8 })];
        if (/^\d+$/.test(text)) {
          lookups.unshift(
            api.get(`items/${text}`).then(
              (item) => ({ items: [item] }),
              // A pasted number that is simply no item's id is an ordinary
              // miss. Any OTHER failure -- a 5xx, a network blip -- is a
              // real problem and must not be swallowed as "no such item":
              // the operator never actually learned whether it exists.
              (error) => { if (error.status === 404) return { items: [] }; throw error; },
            ),
          );
        }
        const pages = await Promise.all(lookups);
        if (mine !== latest) return;
        const seen = new Set();
        const items = [];
        for (const item of pages.flatMap((page) => page.items)) {
          if (seen.has(item.id)) continue;
          seen.add(item.id);
          items.push(item);
        }
        if (items.length) {
          replace(
            matches,
            items.map((item) =>
              el("button", { type: "button", on: { click: () => choose(item) } }, `${item.name_english} (#${item.id})`)),
          );
          note.className = "picker-note";
          replace(note);
        } else {
          replace(matches);
          note.className = "picker-note";
          replace(note, "No matching item.");
        }
      } catch (error) {
        if (mine !== latest) return;
        replace(matches);
        note.className = "picker-note problem";
        replace(note, error.message);
      }
    }, debounceMs);
  });

  return {
    node: el("div", { class: "picker" }, el("label", {}, "Item", input), matches, note, status),
    chosen: () => chosen,
  };
}
