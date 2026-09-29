/**
 * Composition only: the session, the API, and which view the address bar
 * asks for. Every decision is made by a module with tests; this file wires
 * them to the page. A dossier's address is #/character/<id>, so a reload or
 * the back button keeps your place.
 *
 * Nothing here runs at import time. `startApp()` is the one entry point,
 * and it takes the page (`root`), the browser (`storage`, `win`) and the
 * network (`fetchImpl`) as arguments rather than reaching for globals, so
 * it can be started more than once, against a throwaway jsdom window and a
 * fetch stub, from tests/web/app.test.js -- the real page's only job is to
 * call it once, in js/main.js, with the real ones.
 */
import { createApi } from "./api.js";
import { el, replace } from "./dom.js";
import { renderDossier } from "./dossier.js";
import { renderSearch } from "./search.js";
import { createSession, renderLogin, sessionChangeFromStorage } from "./session.js";

// Pure, and exported so it has its own tests without starting the app:
// what the hash means, decided once, the same way regardless of caller.
// Anything that is not exactly `#/character/<digits>` (an optional
// trailing slash allowed, the way a person might paste or type it) is the
// search view, including a `<digits>` too large to be a real char_id --
// `Number.isSafeInteger` rejects it rather than silently rounding it to a
// DIFFERENT, smaller id that might belong to somebody else.
export function parseRoute(hash) {
  const match = /^#\/character\/(\d+)\/?$/.exec(hash ?? "");
  if (match) {
    const charId = Number(match[1]);
    if (Number.isSafeInteger(charId)) return { view: "dossier", charId };
  }
  return { view: "search" };
}

export function startApp({ root, storage, win, fetchImpl }) {
  const session = createSession(storage);
  let me = null;
  // The token `me` was fetched for. `me` is only ever trustworthy for
  // requests made under THIS token -- see route()'s own comment on why it
  // is re-fetched, rather than reused, whenever the token has moved on.
  let meToken = null;
  // The subject `me` belonged to before a cross-tab session change, for
  // the "Now signed in as X, from another tab" notice. Consumed (reset to
  // null) only where a route() call actually DRAWS a view, and in
  // showLogin() -- never unconditionally at the top of route(), which
  // would lose it the moment that particular route() call is interrupted
  // rather than actually shown: a hashchange (Back) arriving mid-switch,
  // or a second handleReplaced() (another sign-in elsewhere, in quick
  // succession) firing before the first's own route() has drawn anything.
  // Either way the notice belongs to whichever route() call ends up
  // actually current, not necessarily the one that first set it.
  let crossTabPreviousSubject = null;
  // Bumped at the START of every route() call, by showLogin(), and by
  // handleReplaced() (below), before any DOM write. route() awaits auth/me
  // and system/capabilities before it draws anything, and nothing promises
  // those answers land in the order they were asked for: two quick
  // hashchanges can start two route() calls, and an OLDER one's answer
  // arriving after a NEWER one has already drawn its view -- or after the
  // operator has signed out, or another tab has changed the session --
  // must not overwrite what is on screen now. Checked after each await,
  // before any DOM write, the same pattern search.js and dossier.js
  // already use for their own races.
  let latestRoute = 0;

  const api = createApi({
    fetchImpl,
    getToken: () => session.token(),
    onUnauthorized: (reason) => {
      // This tab's own session ended (its token was the one the request that
      // 401'd was sent with, and it was still the current one -- api.js
      // guarantees that much before ever calling this). expire(), not
      // signOut(): a newer token some OTHER tab has since saved must survive.
      session.expire();
      me = null;
      meToken = null;
      showLogin(`Signed out: ${reason}.`);
    },
  });

  function showLogin(notice = null) {
    // Invalidates whatever route() call might still be in flight. Without
    // this, opening a dossier, clicking Back, and then Sign out before the
    // next route()'s system/capabilities answer has landed let that answer
    // arrive AFTER this call, still find `mine === latestRoute` (nothing
    // had bumped it), and draw a signed-in search view over the login form
    // this call just drew -- with no token behind it any more.
    latestRoute += 1;
    // A cross-tab notice pending from BEFORE this point belongs to a
    // switch that never got to draw anything -- the session ended
    // entirely instead, or this is the very first load. Whoever signs in
    // next (typing their own credentials into this form) is a fresh start
    // no earlier switch should comment on.
    crossTabPreviousSubject = null;
    setTitle("Sign in");
    renderLogin(root, { api, session, notice, onLoggedIn: route });
  }

  // Set by signOut() the instant it clears the hash, and consumed by the
  // very next `hashchange`. Without it, clearing a non-empty hash queues a
  // `hashchange` that fires on a LATER tick, still finds no token, and
  // calls showLogin() a second time -- redundant with the direct call
  // signOut() already makes, and it would reset the freshly drawn form
  // (refocusing it, discarding whatever else may be on it by then).
  // route()'s own navigations (onBack, opening a search result) leave this
  // false, so THEIR hashchange still reaches route() normally.
  let suppressNextHashchange = false;

  function signOut() {
    // The operator asked THIS BROWSER to sign out.
    session.signOut();
    me = null;
    meToken = null;
    if (win.location.hash) {
      suppressNextHashchange = true;
      win.location.hash = "";
    }
    showLogin();
  }

  function header() {
    return el(
      "header",
      { class: "bar" },
      el("span", { class: "product" }, "ro-admin"),
      el("span", { class: "who" }, me ? me.subject : ""),
      el("button", { type: "button", on: { click: signOut } }, "Sign out"),
    );
  }

  // "ro-admin", "<name> — ro-admin", or (name omitted) plain "ro-admin"
  // again -- the three shapes the tab's title ever takes.
  function setTitle(name = null) {
    win.document.title = name ? `${name} — ro-admin` : "ro-admin";
  }

  // Moves focus to the view's own heading on a route change, the way a
  // single-page app is expected to announce "you're somewhere new" to a
  // screen reader, which a hash change alone does not. Skipped when the
  // view already sent focus somewhere more useful of its own accord --
  // search.js focuses its box, so a person who can see the screen lands
  // ready to type, and stealing that back to the heading right after would
  // undo it for no reason.
  function focusHeading(view) {
    const active = win.document.activeElement;
    if (active && view.contains(active)) return;
    view.querySelector("h1")?.focus();
  }

  // The live region (`problem`) is built EMPTY and filled only after it is
  // already in the document -- see renderLogin's own comment on why: an
  // aria-live region generally announces a CHANGE made after it joins the
  // accessibility tree, not content it was born with as part of one larger
  // insertion.
  function errorView() {
    const problem = el("p", { class: "problem", "aria-live": "polite" });
    const view = el(
      "section",
      { class: "problem-view" },
      el("h1", { tabindex: "-1" }, "Something went wrong"),
      problem,
      // The only way out that isn't a full reload: retries the SAME
      // navigation route() already knows how to make, rather than
      // duplicating any of its logic here.
      el("button", { type: "button", on: { click: () => route() } }, "Retry"),
    );
    return { view, problem };
  }

  // For an account auth/me reports with no system.read: every read this UI
  // makes, starting with system/capabilities itself, requires it, so a
  // Retry here -- errorView()'s button, or the same idea rebuilt here --
  // would just fail the identical way forever. Unlike errorView() this is
  // not a transient problem a later attempt might clear, so it gets no
  // Retry, only the one way out that actually works: signing out.
  function noPermissionsView() {
    return el(
      "section",
      { class: "problem-view" },
      el("h1", { tabindex: "-1" }, "No permissions"),
      el("p", {}, "This account has no ro-admin permissions."),
      el("button", { type: "button", on: { click: signOut } }, "Sign out"),
    );
  }

  async function route() {
    const mine = ++latestRoute;
    if (!session.token()) {
      showLogin();
      return;
    }
    let caps;
    let notice = null;
    try {
      const token = session.token();
      if (token !== meToken) {
        // `me` belongs to whichever token it was fetched under -- checked
        // here, not assumed. `me = me ?? await api.get(...)` looks
        // harmless but assigns BEFORE any guard: a stale answer to an
        // EARLIER token's auth/me, landing after a NEWER token is already
        // in use (a fresh sign-in, here or in another tab), would
        // overwrite `me` with the wrong account's name and permissions.
        // Fetched into a local, checked against `mine`, and only THEN
        // assigned.
        const fetched = await api.get("auth/me");
        if (mine !== latestRoute) return;
        // Read directly, not from a value captured at the top of this
        // function: a route() call that gets interrupted before reaching
        // here (see `crossTabPreviousSubject`'s own comment) must leave it
        // exactly as it found it, for whichever LATER call actually draws.
        if (crossTabPreviousSubject !== null && crossTabPreviousSubject !== fetched.subject) {
          notice = `Now signed in as ${fetched.subject}, from another tab.`;
        }
        me = fetched;
        meToken = token;
      }
      // A PLAYER, or any account auth/me did not grant system.read, cannot
      // read ANYTHING this API serves -- system/capabilities included --
      // so fetching it next used to answer 403 "not permitted:
      // system.read", which route()'s own catch below turned into
      // "Something went wrong" behind a Retry that could only ever fail
      // the same way again. Checked here, before that fetch, on every
      // navigation (not only the first sign-in), so the account is told
      // plainly instead.
      if (!me.permissions.includes("system.read")) {
        const view = noPermissionsView();
        replace(root, header(), view);
        setTitle();
        crossTabPreviousSubject = null;
        focusHeading(view);
        return;
      }
      // Re-read on every navigation. A tier can stop responding at any time,
      // and the actions offered must describe the server as it is now.
      caps = await api.get("system/capabilities");
      if (mine !== latestRoute) return;
    } catch (error) {
      if (mine !== latestRoute) return;
      // A 401 has already brought the sign-in form back, saying why.
      if (error.status === 401) return;
      const { view: failed, problem } = errorView();
      replace(root, header(), failed);
      setTitle();
      focusHeading(failed);
      replace(problem, error.message);
      return;
    }

    const view = el("div", { class: "view" });
    // Same empty-then-fill shape as the error view's `problem`, above.
    const noticeEl = notice ? el("p", { class: "notice", "aria-live": "polite" }) : null;
    replace(root, header(), noticeEl, view);
    if (noticeEl) replace(noticeEl, notice);
    // Consumed HERE, at the point this route() call actually drew a view
    // -- not the error view above, which leaves it alone so a Retry that
    // then succeeds can still show it. See its own declaration for why
    // this is not done unconditionally at the top of the function.
    crossTabPreviousSubject = null;
    const parsed = parseRoute(win.location.hash);
    if (parsed.view === "dossier") {
      // A safe default in case the load never succeeds at all (dossier.js
      // shows "Character #<id>" as its own h1 in exactly that case, too);
      // onTitle below overwrites it the moment a real name is known, and
      // again on every later refresh, so a rename updates the title too.
      setTitle(`Character #${parsed.charId}`);
      await renderDossier(view, {
        api,
        charId: parsed.charId,
        me,
        caps,
        onBack: () => { win.location.hash = ""; },
        // Guarded the same way every direct setTitle() call in route()
        // itself is: a rename can arrive from a refresh LONG after this
        // route() has finished (an action's own onChanged), by which time
        // a newer navigation may have already superseded it.
        onTitle: (name) => { if (mine === latestRoute) setTitle(name); },
      });
      // A newer route() -- or a cross-tab session change -- can have
      // started and even finished WHILE renderDossier's own first load was
      // still in flight. Without this, focus below would move to a
      // heading that belongs to a view no longer on screen.
      if (mine !== latestRoute) return;
    } else {
      renderSearch(view, {
        api,
        onOpenCharacter: (id) => { win.location.hash = `#/character/${id}`; },
      });
      setTitle();
    }
    focusHeading(view);
  }

  // "replaced" (see sessionChangeFromStorage, and the `storage` listener
  // below): a fresh sign-in elsewhere in THIS SAME browser -- the same
  // person's token merely changed (it expired and they signed back in, in
  // another tab), or a genuinely different person is now signed in.
  //
  // An earlier revision tried to tell those two apart before touching
  // anything -- a probe, under the new token, run before redrawing
  // anything -- so an unchanged identity could keep its exact view, an
  // in-progress outcome included. That needed the view frozen (both
  // visibly, via `inert`, and enforced, via a second api instance and a
  // flag the shared one's getToken checked) for as long as the probe
  // took, so nothing on screen could send under the still-unconfirmed new
  // token in the meantime. It worked, but the complexity it cost was not
  // worth what it bought: a navigation already in flight when "replaced"
  // arrived was dropped rather than resumed, a hashchange during the
  // probe could lift the freeze with the old (still wrong-identity) view
  // still on screen, the dossier's title could get stuck on the old
  // character, and a request made during the freeze failed even in the
  // one case (the same person) where it need not have.
  //
  // Simpler, and no less safe: always redraw, exactly like any other
  // navigation. The view is replaced with a neutral placeholder FIRST,
  // synchronously, in the SAME tick "replaced" is detected -- so the old
  // forms leave the document before anything async happens at all, and
  // nothing on screen can send under the new identity, whoever it turns
  // out to be, without needing to know who that is yet. route() then runs
  // normally: it re-reads window.location.hash itself, so whatever the
  // operator was looking at -- including a navigation that was itself
  // still in flight -- loads again, now under the new identity, and shows
  // the "Now signed in as X, from another tab" notice only if auth/me
  // says the subject actually changed.
  //
  // The cost: an in-progress on-screen outcome (a "Stopped waiting...
  // Check again", say) is lost even when the SAME person re-signed in
  // elsewhere, where nothing was actually wrong. Judged worth it --
  // History shows every queued command, so nothing the operator was
  // watching is truly gone, only the page they were reading it from.
  function handleReplaced() {
    latestRoute += 1; // invalidates any route() (or a stale handleReplaced) in flight
    // For the "Now signed in as X, from another tab" notice route()
    // shows once the new identity is confirmed -- consumed where a route()
    // call actually draws (see the declaration). `me?.subject`, not
    // `crossTabPreviousSubject` itself, UNLESS `me` is already null: two
    // sign-ins elsewhere in quick succession (alice -> bob -> carol) can
    // fire this twice before the first switch's own route() has drawn
    // anything to consume it -- by the second call, `me` is already null
    // (the first call cleared it), and falling back to whatever
    // `crossTabPreviousSubject` already holds ("alice") keeps that
    // identity as the one the eventual notice compares against, rather
    // than losing it to `null` (no notice at all) or overwriting it with
    // an intermediate identity ("bob") nothing ever actually showed.
    crossTabPreviousSubject = me?.subject ?? crossTabPreviousSubject;
    me = null;
    meToken = null;
    session.refresh();
    // The placeholder's own title, alongside its own view: without this,
    // the tab keeps showing the OLD view's name (a character's, say) while
    // the page underneath it no longer does.
    setTitle();
    replace(root, header(), el("p", { class: "muted" }, "Switching session…"));
    route();
  }

  // The browser holds ONE session, shared by every tab (see session.js's
  // own docstring). A `storage` event fires in every OTHER same-origin tab
  // when one tab writes to localStorage, never in the tab that made the
  // change; sessionChangeFromStorage turns that event into what actually
  // happened to the shared session, and this listener only acts on it:
  //   - "ended": nothing is left anywhere durable. Drop `me`, invalidate
  //     whatever route() is in flight (showLogin() does that), and show
  //     the form saying so.
  //   - "replaced": see handleReplaced(), above.
  win.addEventListener("storage", (event) => {
    // `session.held()`, not `meToken` and not a fresh `session.token()`
    // read.
    //
    // Not `session.token()`: it deliberately re-reads storage on every
    // call until it has cached a non-null value (see its own comment) --
    // so for a tab that has never signed in, calling it AT REACTION TIME
    // would already pick up the other tab's just-written value
    // (localStorage is one shared store, already updated by the time this
    // event fires), comparing the new value against itself and silently
    // missing the change.
    //
    // Not `meToken` either: `meToken` stays null until route()'s own
    // auth/me answers, which can take a moment -- so if ANOTHER tab signs
    // out WHILE this tab's first auth/me is still in flight, `meToken` is
    // still null right when this event arrives, sessionChangeFromStorage
    // reads that as "no session here to end", and the sign-out is missed
    // entirely: this tab stays on its stale page, and its NEXT request
    // still goes out bearing the token that was just invalidated.
    //
    // `session.held()` has neither problem: it is this tab's own in-memory
    // copy, set the moment `session.token()` is first called (route()'s
    // very first line calls it), well before any fetch it was used to
    // build has actually answered.
    const change = sessionChangeFromStorage(event, session.held(), storage);
    if (change === "ended") {
      session.expire();
      me = null;
      meToken = null;
      showLogin("Signed out in another tab.");
    } else if (change === "replaced") {
      handleReplaced();
    }
  });

  win.addEventListener("hashchange", () => {
    // See signOut()'s own comment on `suppressNextHashchange`: consumed
    // (reset) the instant it is checked, so it only ever swallows the ONE
    // hashchange signOut() itself caused, never some later, real one.
    if (suppressNextHashchange) {
      suppressNextHashchange = false;
      return;
    }
    route();
  });
  route();
}
