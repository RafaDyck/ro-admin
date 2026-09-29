/**
 * The page's one bootstrap. app.js's startApp() takes the browser (storage,
 * window) and the network (fetch) as arguments instead of reaching for
 * globals, so it can be started against a throwaway jsdom window and a
 * fetch stub in tests/web/app.test.js. This file supplies the real ones,
 * exactly once, for the real page -- nothing here is imported by a test.
 */
import { startApp } from "./app.js";

function safeLocalStorage() {
  // Even reading window.localStorage can throw (blocked site data, some
  // private modes). startApp() already treats a null storage as "no
  // durable session, but the tab still works" -- see session.js.
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

startApp({
  root: document.getElementById("app"),
  storage: safeLocalStorage(),
  win: window,
});
