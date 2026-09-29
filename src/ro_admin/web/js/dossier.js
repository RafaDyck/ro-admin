/**
 * The person page. One scroll, in reading order: who they are, their state
 * and how fresh it is, what you can do, what they carry, what happened.
 *
 * History is the server's merged timeline (GET /logs/timeline). Interleaving
 * the logs here would be the UI re-implementing an endpoint that exists.
 */
import { LABELS, renderActions } from "./actions.js";
import { actionPlan } from "./capabilities.js";
import { el, replace } from "./dom.js";
import { freshness, inventoryFreshness, number, serverTime } from "./format.js";

// The page asks the server for this many timeline entries. Named once so
// the number stated to the operator, in History's own heading, can never
// drift from the number actually requested.
const HISTORY_LIMIT = 50;

export async function loadDossier(api, charId) {
  const [character, inventory, timeline] = await Promise.all([
    api.get(`characters/${charId}`),
    api.get(`characters/${charId}/inventory`),
    api.get("logs/timeline", { char_id: charId, limit: HISTORY_LIMIT }),
  ]);
  return { character, inventory, timeline };
}

export async function renderDossier(
  root, { api, charId, me, caps, onBack, onTitle = () => {}, ...actionOptions },
) {
  const problem = el("p", { class: "problem", "aria-live": "polite" });
  // tabindex="-1" on every h1 this module ever draws here (this one, and
  // the two error-path ones below): none joins the normal Tab order, but
  // each is a valid app.js .focus() target on a route change, so a screen
  // reader announces the dossier the same way loading it, failing to load
  // it, and successfully loading it all still update the SAME heading.
  const identity = el("section", { class: "identity" }, el("h1", { tabindex: "-1" }, "Loading…"));
  const state = el("section", { class: "state" });
  const actions = el("section", { class: "actions" });
  const inventory = el("section", { class: "inventory" });
  const history = el("section", { class: "history" });
  replace(
    root,
    el("button", { type: "button", class: "back", "aria-label": "Back to search", on: { click: onBack } }, "← Search"),
    problem, identity, state, actions, inventory, history,
  );

  // Kept across refreshes, and only ever set on success: the userid label
  // is worth having, but not worth a fresh round trip on every post-action
  // refresh. A failed fetch is never cached, so the next refresh, whenever
  // it comes, simply tries again.
  let account = null;
  // Rendered once, and the SAME object thereafter: renderActions is called
  // only on the first successful load, and its forms close over this
  // reference. Every later refresh mutates it in place (Object.assign)
  // rather than replacing it, so a form's summary or a destructive confirm
  // built from `character.name` after a rename reads the CURRENT name, not
  // the one the page happened to open with. `char_id` never changes, so
  // nothing else here needs the same treatment.
  let character = null;
  let actionsShown = false;
  let loaded = false;

  // Bumped at the START of every refresh, before its first await. Two
  // quick actions can each start their own refresh, and nothing promises
  // their answers land in the order they were asked for: an older
  // refresh's answer -- a success OR a failure -- arriving after a newer
  // one's must never overwrite what the newer one already showed. Both
  // branches below check this before touching the DOM.
  let latest = 0;

  function problemMessage(error) {
    // A first load has nothing on screen yet to call "the earlier read", so
    // its wording is unchanged: just the error, plainly. Once something has
    // loaded, a later failure must say so -- the page is not blank, it is
    // OLD, and those read very differently to someone about to act on it.
    return loaded
      ? `Could not re-read this page: ${error.message}. What is shown is from the earlier read.`
      : error.message;
  }

  function retryButton() {
    const button = el("button", { type: "button" }, "Retry");
    // Guarded the same way actions.js's own buttons are: disabled the
    // instant it is clicked, and a local flag besides, so a double-click
    // (or a double `.click()` in a test) cannot start a second load before
    // the first has had any chance to disable anything.
    let clicked = false;
    button.addEventListener("click", () => {
      if (clicked) return;
      clicked = true;
      button.disabled = true;
      refresh();
    });
    return button;
  }

  async function refresh() {
    const mine = ++latest;
    let data;
    try {
      data = await loadDossier(api, charId);
      if (!account) {
        // Only for the userid. If this fails the page loses one label, not
        // itself, and nothing here remembers the failure.
        account = await api.get(`accounts/${data.character.account_id}`).catch(() => null);
      }
    } catch (error) {
      if (mine !== latest) return; // a newer refresh already answered
      if (!loaded) replace(identity, el("h1", { tabindex: "-1" }, `Character #${charId}`));
      replace(problem, problemMessage(error), " ", retryButton());
      return;
    }
    if (mine !== latest) return; // a newer refresh already answered

    // Built off-DOM, all four, before anything on screen changes. A shape
    // this page does not yet defend against (a timeline answer with no
    // `sources`, say) throws here, inside the try -- caught below, the same
    // as a network failure, and turned into a message rather than a half
    // repainted page. Because actions.js only ever `.catch(() => {})`s the
    // promise it calls this refresh through, a throw that escaped this
    // function would be silently swallowed, never reaching the operator at
    // all -- so nothing here is allowed to throw back out of `refresh`.
    let identityNodes, stateNodes, inventoryNodes, historyNodes;
    try {
      identityNodes = buildIdentity(data.character, account);
      stateNodes = buildState(data.character);
      inventoryNodes = buildInventory(data.inventory);
      historyNodes = buildHistory(data.timeline);
    } catch (error) {
      if (mine !== latest) return;
      if (!loaded) replace(identity, el("h1", { tabindex: "-1" }, `Character #${charId}`));
      replace(problem, problemMessage(error), " ", retryButton());
      return;
    }

    replace(problem);
    replace(identity, ...identityNodes);
    replace(state, ...stateNodes);
    replace(inventory, ...inventoryNodes);
    replace(history, ...historyNodes);
    if (!character) character = {};
    Object.assign(character, data.character);
    loaded = true;
    // On the first successful load AND every refresh after it -- a rename
    // must be able to update the tab title the same way it already
    // updates this page's own h1.
    onTitle(character.name);

    // Rendered once. A refresh after an action re-reads everything else and
    // leaves this alone, so the outcome the person is reading stays put.
    //
    // Its own failure boundary, not the one above: actionPlan/renderActions
    // need `character` (assigned just above) and run AFTER identity/state/
    // inventory/history are already correctly on screen, so a throw here
    // (a malformed capabilities body -- caps.actions missing, say) must
    // land in the problem line the same way a malformed timeline does,
    // leaving what already loaded alone, rather than escape refresh()
    // uncaught. Because actions.js only ever `.catch(() => {})`s the
    // promise it calls this refresh through, an uncaught throw here would
    // be silently swallowed, never reaching the operator at all.
    if (!actionsShown) {
      try {
        const plan = actionPlan(me, caps, LABELS);
        actionsShown = true;
        if (plan.length) {
          renderActions(actions, { api, character, plan, onChanged: refresh, ...actionOptions });
        } else {
          actions.remove();
        }
      } catch (error) {
        if (mine !== latest) return;
        replace(problem, problemMessage(error), " ", retryButton());
        return;
      }
    }
  }
  await refresh();
}

function facts(pairs) {
  return el("dl", {}, pairs.map(([term, value]) => [el("dt", {}, term), el("dd", {}, value)]));
}

function buildIdentity(character, account) {
  return [
    el("h1", { tabindex: "-1" }, character.name),
    facts([
      ["Character", `#${character.char_id}`],
      ["Account", account ? `${account.userid} (#${character.account_id})` : `#${character.account_id}`],
      // A bare job id on purpose. rAthena keeps no job table in SQL, and a
      // name invented here would be game data bundled into the client.
      ["Class", `job ${character.class}`],
      ["Status", character.online ? "online" : "offline"],
    ]),
  ];
}

// Every field in projections.CHARACTER_VOLATILE lives here, and nothing
// else does -- the stable fields (name, id, account, class, online status)
// are Identity's, above. Keep this list in sync with CHARACTER_VOLATILE in
// src/ro_admin/projections.py.
function buildState(character) {
  const note = freshness(character);
  return [
    el("h2", {}, "State"),
    // Heads the section, rather than sitting beside a single value: the map
    // server can hold every one of these in memory at once, not only zeny,
    // so the note qualifies all of them, not one.
    note ? el("p", { class: character.stale ? "freshness stale" : "freshness" }, note) : null,
    facts([
      ["Zeny", number(character.zeny)],
      ["Base level", number(character.base_level)],
      ["Job level", number(character.job_level)],
      ["Base exp", number(character.base_exp)],
      ["Job exp", number(character.job_exp)],
      ["Status points", number(character.status_point)],
      ["Skill points", number(character.skill_point)],
      ["Position", `${character.last_map} (${character.last_x}, ${character.last_y})`],
    ]),
  ];
}

function buildInventory(inventory) {
  const note = inventoryFreshness(inventory);
  const rows = inventory.items.map((item) =>
    el(
      "tr",
      {},
      el("td", {}, item.item_name),
      el("td", {}, `#${item.item_id}`),
      el("td", {}, number(item.amount)),
      el("td", {}, item.refine ? `+${item.refine}` : ""),
      el("td", {}, [item.equipped ? "equipped" : null, item.identified ? null : "unidentified"].filter(Boolean).join(", ")),
    ));
  return [
    el("h2", {}, "Inventory"),
    note ? el("p", { class: "freshness stale" }, note) : null,
    rows.length
      ? el(
          "table",
          {},
          // "Notes", not blank: an empty header over "equipped" / "unidentified" reads as a mistake.
          el("thead", {}, el("tr", {}, ["Item", "Id", "Amount", "Refine", "Notes"].map((h) => el("th", {}, h)))),
          el("tbody", {}, rows),
        )
      : el("p", {}, "No items."),
  ];
}

function buildHistory(timeline) {
  // The server merges every source and truncates to `limit` (routers/logs.py):
  // fewer than HISTORY_LIMIT items back means nothing was cut off, and
  // saying "the latest 50" then would be a claim the answer does not
  // support. Only a FULL page means older entries exist and are not shown.
  const cutoff = timeline.items.length >= HISTORY_LIMIT
    ? el("p", { class: "muted" }, `Showing the latest ${HISTORY_LIMIT} events; older ones are not shown.`)
    : null;
  return [
    el("h2", {}, "History"),
    cutoff,
    timeline.items.length
      ? el(
          "ol",
          { class: "timeline" },
          timeline.items.map((entry) =>
            el("li", {}, el("time", {}, serverTime(entry.date)), " ", el("span", { class: "kind" }, entry.kind), " ", entry.summary)),
        )
      : [
          el("p", {}, "Nothing recorded for this character yet."),
          // The source tables named separately, in a quieter line: the main
          // sentence is for the operator, this one is for whoever debugs it.
          el("p", { class: "muted" }, `Recorded in ${timeline.sources.join(", ")}.`),
        ],
  ];
}
