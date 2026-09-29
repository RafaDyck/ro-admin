/**
 * The landing page: one box, and the people it finds.
 *
 * Every match is found BY THE DATABASE, through prefix filters the API serves
 * from rAthena's own indexes. The browser never holds a list to filter; that
 * was the predecessor's pattern, and it broke at the first page cap. A purely
 * numeric query is also tried as a character id and as an account id, so an
 * id pasted from a log goes straight to the person.
 *
 * Races: the counter that decides whether an answer is still wanted is
 * bumped at the KEYSTROKE, not when its debounce timer fires. A slow answer
 * to an earlier keystroke can otherwise land inside a LATER keystroke's
 * still-open debounce window -- before that keystroke's own timer has fired
 * and bumped anything -- and render under a query the operator has already
 * changed. Clearing the box is treated the same as any other keystroke, so
 * an answer still in flight when the box is cleared cannot repopulate it.
 *
 * Live region: only a short status line is `aria-live`. The results list
 * itself is not -- marking a list of up to twenty buttons live would have a
 * screen reader read the whole thing out on every keystroke.
 */
import { el, replace } from "./dom.js";

const PAGE = 10;

const GROUP_OF_KIND = {
  characters: "characters", character: "characters",
  accounts: "accounts", account: "accounts",
};

// Order matters: it is the order groups are listed in, on screen and in the
// status line.
const GROUPS = [
  { key: "characters", singular: "character", plural: "characters", label: "Characters" },
  { key: "accounts", singular: "account", plural: "accounts", label: "Accounts" },
];

export function searchRequests(query) {
  const text = query.trim();
  if (!text) return [];
  const requests = [
    { kind: "characters", path: "characters", params: { name_prefix: text, limit: PAGE } },
    { kind: "accounts", path: "accounts", params: { userid_prefix: text, limit: PAGE } },
  ];
  if (/^\d+$/.test(text)) {
    requests.push(
      { kind: "character", path: `characters/${text}` },
      { kind: "account", path: `accounts/${text}` },
    );
  }
  return requests;
}

export async function runSearch(api, query) {
  const requests = searchRequests(query);
  const settled = await Promise.allSettled(requests.map((r) => api.get(r.path, r.params)));
  const found = {
    characters: [], accounts: [], moreCharacters: false, moreAccounts: false,
    problems: { characters: [], accounts: [] },
  };
  settled.forEach((outcome, index) => {
    const { kind } = requests[index];
    if (outcome.status === "rejected") {
      // A number that is not an id is an ordinary miss, not a problem. A
      // refusal -- e.g. a 422 because the query is longer than the column --
      // IS a problem and must never be read as "no match": the operator
      // never actually learned whether anything starts with the query.
      if ((kind === "character" || kind === "account") && outcome.reason.status === 404) return;
      const group = GROUP_OF_KIND[kind];
      const message = outcome.reason.message;
      if (!found.problems[group].includes(message)) found.problems[group].push(message);
      return;
    }
    const value = outcome.value;
    if (kind === "characters") {
      found.characters.push(...value.items);
      found.moreCharacters = value.has_more;
    } else if (kind === "accounts") {
      found.accounts.push(...value.items);
      found.moreAccounts = value.has_more;
    } else if (kind === "character") {
      putFirst(found.characters, value, "char_id");
    } else {
      putFirst(found.accounts, value, "account_id");
    }
  });
  return found;
}

// An exact id match leads its group, and is not listed twice.
function putFirst(list, item, key) {
  const index = list.findIndex((existing) => existing[key] === item[key]);
  if (index !== -1) list.splice(index, 1);
  list.unshift(item);
}

// The sentences a screen reader hears on every update, each one complete on
// its own (capitalised, ending with a full stop) so they can be told apart
// when read together, and so a problem sentence can be styled red without
// losing the plain sentences around it. A group speaks for itself: its own
// count when it has matches, its own problem when it has one, and both when
// it has both -- a problem must never silently swallow a count the group
// actually has, and one group's failure must never read as "nothing
// matched" for the other. A validation refusal is never read as a clean
// miss.
function statusSentences(found, query) {
  const noProblems = found.problems.characters.length === 0 && found.problems.accounts.length === 0;
  if (found.characters.length === 0 && found.accounts.length === 0 && noProblems) {
    const idNote = /^\d+$/.test(query) ? ", and none has that id" : "";
    return [{ text: `No character or account starts with “${query}”${idNote}.`, problem: false }];
  }
  const sentences = [];
  for (const { key, singular, plural, label } of GROUPS) {
    const items = found[key];
    const problems = found.problems[key];
    for (const message of problems) {
      sentences.push({ text: `${label}: ${message}.`, problem: true });
    }
    if (items.length) {
      sentences.push({
        text: `${items.length} ${items.length === 1 ? singular : plural} found.`,
        problem: false,
      });
    } else if (problems.length === 0) {
      const idNote = /^\d+$/.test(query) ? " or has that id" : "";
      sentences.push({ text: `No ${singular} starts with “${query}”${idNote}.`, problem: false });
    }
  }
  return sentences;
}

export function renderSearch(root, { api, onOpenCharacter, debounceMs = 250 }) {
  const input = el("input", {
    type: "search",
    name: "q",
    placeholder: "Start of a character name or account userid, or an id",
    "aria-label": "Find a character or account",
    autocomplete: "off",
    spellcheck: "false",
  });
  // `status`: one short line, live. `list`: the actual results, not live.
  const status = el("div", { class: "status", "aria-live": "polite" });
  const list = el("div", { class: "results" });
  // No tabindex: unlike the dossier's or the error view's, this heading is
  // never itself a focus target. input.focus() below always runs on every
  // arrival here -- including Back from a dossier -- so app.js's
  // route-change focus handoff (see its focusHeading()) always finds the
  // box already holding focus and leaves this heading alone. It exists for
  // structure -- a screen reader landmark, a title should the page ever be
  // linked to directly -- not to be focused.
  const heading = el("h1", {}, "Find a character or account");
  replace(root, el("section", { class: "search" }, heading, input, status, list));

  let latest = 0;
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    // Bumped here, at the keystroke, not inside the timer below. An answer
    // to an earlier keystroke that arrives while a later keystroke is still
    // waiting out ITS OWN debounce window must already read as stale, and it
    // can only do that if `latest` moved the moment the later keystroke was
    // typed -- not only once that keystroke's own timer eventually fires.
    const mine = ++latest;
    const query = input.value;
    if (!query.trim()) {
      // Nothing to debounce when there is nothing to search for, and
      // clearing the box must take effect immediately: `mine` above already
      // invalidates whatever was still in flight for the previous query.
      replace(status);
      replace(list);
      return;
    }
    timer = setTimeout(async () => {
      try {
        const found = await runSearch(api, query);
        if (mine !== latest) return;
        const trimmed = query.trim();
        replace(
          status,
          statusSentences(found, trimmed).map(({ text, problem }) =>
            el("p", problem ? { class: "problem" } : {}, text),
          ),
        );
        replace(list, renderList(found, { api, onOpenCharacter }));
      } catch (error) {
        // A malformed 2xx body (an `items` that is not an array) or a child
        // dom.js refuses must not leave a previous query's results on
        // screen looking current.
        if (mine !== latest) return;
        replace(status, el("p", { class: "problem" }, error.message));
        replace(list);
      }
    }, debounceMs);
  });
  input.focus();
}

function renderList(found, { api, onOpenCharacter }) {
  const parts = [];
  if (found.characters.length) {
    parts.push(
      el("h2", {}, "Characters"),
      el("ul", {}, found.characters.map((c) => characterItem(c, onOpenCharacter))),
      found.moreCharacters ? el("p", { class: "more" }, "More characters match. Keep typing to narrow.") : null,
    );
  }
  if (found.accounts.length) {
    parts.push(
      el("h2", {}, "Accounts"),
      el("ul", {}, found.accounts.map((a) => accountItem(a, { api, onOpenCharacter }))),
      found.moreAccounts ? el("p", { class: "more" }, "More accounts match. Keep typing to narrow.") : null,
    );
  }
  return parts;
}

function characterItem(character, onOpenCharacter) {
  return el(
    "li",
    {},
    el(
      "button",
      { type: "button", class: "result", on: { click: () => onOpenCharacter(character.char_id) } },
      el("strong", {}, character.name),
      " ",
      el(
        "span",
        { class: "meta" },
        `#${character.char_id} · base ${character.base_level} · ${character.online ? "online" : "offline"}`,
      ),
    ),
  );
}

function accountItem(account, { api, onOpenCharacter }) {
  const list = el("ul", { class: "account-characters" });
  // Repeated clicks (a slow first answer, a fast second one) must not let
  // the earlier answer land after the later one: only the latest click's
  // own answer is allowed to render.
  let latestClick = 0;
  async function open() {
    const mine = ++latestClick;
    replace(list, el("li", {}, "Loading…"));
    try {
      const page = await api.get(`accounts/${account.account_id}/characters`);
      if (mine !== latestClick) return;
      replace(
        list,
        page.items.length
          ? page.items.map((c) => characterItem(c, onOpenCharacter))
          : el("li", {}, "This account has no characters."),
      );
    } catch (error) {
      if (mine !== latestClick) return;
      replace(list, el("li", { class: "problem" }, error.message));
    }
  }
  return el(
    "li",
    {},
    el(
      "button",
      { type: "button", class: "result", on: { click: open } },
      el("strong", {}, account.userid),
      " ",
      el("span", { class: "meta" }, `account #${account.account_id}${account.banned ? " · banned" : ""}`),
    ),
    list,
  );
}
