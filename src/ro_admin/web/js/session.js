/**
 * Signing in, and remembering it.
 *
 * The token is kept in localStorage so it survives a browser restart, for as
 * long as the server lets it live: RO_ADMIN_TOKEN_TTL_SECONDS, one hour by
 * default. There is no refresh token. When the token expires, the next call's
 * 401 ends the session (see api.js) and this form comes back saying why.
 *
 * Signing out is local to this browser: it only ever removes what THIS
 * browser stored. A JWT is not revoked on the server, so it stays valid
 * until its own TTL runs out even after every tab has forgotten it -- there
 * is no server-side call that makes it unusable sooner.
 *
 * The browser holds ONE session, shared by every tab: `token()` falls back
 * to reading storage directly, on purpose, so a tab that has never signed
 * in still picks up whatever the browser is already signed in as. app.js's
 * `storage` listener (see sessionChangeFromStorage, below) exists to keep
 * every OTHER tab's in-memory copy caught up with that one shared session
 * too -- a sign-in in one tab is adopted by the rest, not treated as a
 * reason to sign them out.
 *
 * No storage choice survives XSS: injected script can use the token wherever
 * it is kept. js/dom.js and scripts/check_no_innerhtml.py are the control;
 * the choice of storage only bounds what happens afterwards.
 */
import { el, replace } from "./dom.js";

// Exported for tests: app.js never imports this constant itself -- it reacts
// to a shared-session change through sessionChangeFromStorage's return value,
// not by reading the storage key app.js's own listener fires on directly.
// The key still needs a name a test can address, to write and read the same
// slot this module does without duplicating the literal, so it is exported
// for that.
export const TOKEN_STORAGE_KEY = "ro-admin.token";

// The cross-tab decision, kept pure (given what storage now holds) so it
// has a test of its own: app.js runs startApp() to actually react to it,
// but nothing about deciding WHAT happened needs a live page.
//
// `event` is the `storage` event a tab receives when ANOTHER same-origin
// tab writes to localStorage -- never this one; the browser does not fire
// it in the tab that made the change. `currentToken` is the token THIS
// tab believed it had a moment ago -- what changed is judged against that,
// not a fresh read of the very storage that just changed.
//
// Callers: do NOT pass a fresh `session.token()` read here. `token()`
// deliberately re-reads storage on every call until it has cached a
// NON-null value (see its own comment) -- so for a tab that has never
// signed in, calling it AT REACTION TIME would already pick up the other
// tab's just-written value (localStorage is one shared store, already
// updated by the time this event fires), comparing the new value against
// itself and silently missing the change. Pass whatever state the caller
// itself already tracks and only updates deliberately -- app.js uses its
// own `meToken` for exactly this reason.
//
// `storage` is consulted only for the localStorage.clear() case below.
//
// Returns:
//   - "ended": the stored token is gone, and this tab held one. There is
//     no session left anywhere durable; drop it here too.
//   - "replaced": a DIFFERENT, non-null token is now stored -- a fresh
//     sign-in, in this tab's own browser, from another tab. This is
//     returned even when `currentToken` is null: the browser now has a
//     session this tab does not yet know about, and a tab sitting on the
//     login form is exactly the case that should pick it up, not ignore it
//     as someone else's business.
//   - null: nothing here changed (a different key entirely, or the stored
//     value already matches what this tab has).
//
// `event.key === null` is what a tab sees from another tab's
// localStorage.clear() -- every field on that event, including newValue,
// is null regardless of which keys were actually cleared, so the only way
// to know whether THIS key survived is to ask storage directly, the same
// read `session.token()` itself falls back to.
export function sessionChangeFromStorage(event, currentToken, storage) {
  if (event.key !== null && event.key !== TOKEN_STORAGE_KEY) return null;
  let stored = event.newValue;
  if (event.key === null) {
    try {
      stored = storage.getItem(TOKEN_STORAGE_KEY);
    } catch {
      stored = null;
    }
  }
  if (stored === null) return currentToken !== null ? "ended" : null;
  return stored !== currentToken ? "replaced" : null;
}

export function createSession(storage) {
  // Held in memory as well, so a browser that refuses storage (blocked site
  // data, some private modes) still has a session while the tab is open.
  let current = null;
  function token() {
    if (current === null) {
      try {
        current = storage.getItem(TOKEN_STORAGE_KEY);
      } catch {
        current = null;
      }
    }
    return current;
  }
  return {
    token,
    // Unlike token(), never falls through to storage: this tab's own
    // in-memory copy, exactly as it stands right now, or null if this tab
    // has never held one. app.js's `storage` listener compares against
    // THIS, not token() -- see sessionChangeFromStorage's own comment on
    // why a fresh token() read there is unsafe.
    held: () => current,
    save(newToken) {
      current = newToken;
      try {
        storage.setItem(TOKEN_STORAGE_KEY, newToken);
      } catch {
        // Not remembered across restarts; this tab still has it.
      }
    },
    // The operator asked THIS BROWSER to sign out: removes the stored key
    // unconditionally, even if another tab has since saved a different
    // token there. Compare-then-remove here would mean a sign-out button
    // clicked in a tab that isn't the newest one silently does nothing --
    // worse, since `current` is still cleared to null, this tab's own next
    // `token()` call would re-read storage, adopt the other tab's token,
    // and quietly sign this tab back in right after "signing out".
    signOut() {
      current = null;
      try {
        storage.removeItem(TOKEN_STORAGE_KEY);
      } catch {
        // Nothing this tab can do; there is no session left in memory
        // either way.
      }
    },
    // The 401 path (see api.js's onUnauthorized, which only fires for a
    // request THIS TAB sent using the token THIS TAB believed was current).
    // Keeps compare-then-remove, so a stale 401 -- one that was in flight
    // when a fresh sign-in replaced the token, here or in another tab --
    // cannot sign out a newer session.
    //
    // Compares against `current` directly, NEVER `token()`. `token()` falls
    // through to storage when `current` is null, and a tab that never held
    // a token of its own (never signed in, never called `save`) would then
    // adopt whatever another tab just stored there and immediately remove
    // it, expiring a session it never had a stake in.
    expire() {
      const mine = current;
      current = null;
      if (mine === null) return;
      try {
        if (storage.getItem(TOKEN_STORAGE_KEY) === mine) {
          storage.removeItem(TOKEN_STORAGE_KEY);
        }
      } catch {
        // Storage refuses to answer either way; there is nothing this tab
        // can safely remove.
      }
    },
    // Forces the NEXT token() call to re-read storage rather than answer
    // from memory. Used only for the "replaced" cross-tab case
    // (sessionChangeFromStorage): another tab just wrote a DIFFERENT
    // token this tab should now adopt, and storage already holds it --
    // there is nothing to remove, the way expire() and signOut() remove
    // something, only this tab's own stale in-memory copy to stop
    // shadowing it.
    refresh() {
      current = null;
    },
  };
}

export function renderLogin(root, { api, session, onLoggedIn, notice = null }) {
  const userid = el("input", {
    name: "userid", autocomplete: "username", autocapitalize: "none", required: true,
  });
  const password = el("input", {
    type: "password", name: "password", autocomplete: "current-password", required: true,
  });
  const button = el("button", { type: "submit" }, "Sign in");
  // Built empty, deliberately: an aria-live region generally only
  // announces a CHANGE made after it is already in the accessibility
  // tree, not content it was born with as part of one larger DOM
  // insertion (replace(root, form), below). Filled in after that
  // insertion, further down, so a screen reader actually hears the notice
  // rather than silently having it already on screen.
  const problem = el("p", { class: "problem", "aria-live": "polite" });
  const form = el(
    "form",
    { class: "login" },
    el("h1", {}, "ro-admin"),
    el("p", {}, "Sign in with your rAthena account."),
    problem,
    el("label", {}, "User id", userid),
    el("label", {}, "Password", password),
    button,
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    // .busy is what app.css keys the progress cursor off of; left set on
    // the success path along with `disabled` itself, for the same reason.
    button.classList.add("busy");
    replace(problem);
    try {
      const response = await api.post(
        "auth/login",
        { userid: userid.value, password: password.value },
        { auth: false },
      );
      session.save(response.access_token);
      // Left disabled: onLoggedIn() starts async navigation to the rest of
      // the app, and a second submit racing that navigation would start a
      // second login. Only the failure branch below hands the form back.
      onLoggedIn();
    } catch (error) {
      button.disabled = false;
      button.classList.remove("busy");
      password.value = "";
      password.focus();
      replace(problem, error.message);
    }
  });

  replace(root, form);
  userid.focus();
  // Only now, after the form (and this empty live region) is already in
  // the document -- see the comment on `problem`, above.
  if (notice) replace(problem, notice);
}
