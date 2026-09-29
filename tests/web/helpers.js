// Test support for the web UI. Development only; nothing under tests/ ships.
import { JSDOM } from "jsdom";

// The shipped modules use `document` and `Node` as globals, the way a browser
// provides them. A fresh window per test keeps one test's DOM out of the
// next; the previous window is closed first so its timers and listeners
// don't keep running underneath the new one.
let currentWindow;

export function installDom() {
  currentWindow?.close();
  const { window } = new JSDOM("<!doctype html><html><body></body></html>");
  currentWindow = window;
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.Node = window.Node;
  return window;
}

// A stand-in for js/api.js. `routes` maps "METHOD path" to a value, an Error
// to throw, or a function of the params/body returning either. Values cross
// a JSON round trip, the way they would over the wire: it clones them like
// structuredClone would, but also drops anything JSON cannot carry (a
// function, a Date, undefined), and records each call's `arg` the same way
// so a module that mutates its body after the call cannot rewrite what a
// test already asserted on.
export function fakeApi(routes) {
  const calls = [];
  // `undefined` (a GET with no params) does not survive a JSON round trip at
  // all -- JSON.stringify(undefined) is itself undefined, not a string --
  // so it is passed through rather than fed to JSON.parse.
  const toJson = (value) => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)));
  async function call(method, path, arg) {
    calls.push({ method, path, arg: toJson(arg) });
    const route = routes[`${method} ${path}`];
    if (route === undefined) throw new Error(`fakeApi: no route for ${method} ${path}`);
    const value = typeof route === "function" ? await route(arg) : route;
    if (value instanceof Error) throw value;
    return toJson(value);
  }
  return {
    calls,
    get: (path, params) => call("GET", path, params),
    post: (path, body) => call("POST", path, body),
  };
}

// A localStorage stand-in shared by every test that needs one: session.js's
// own tests, and app.js's (startApp() takes a storage object directly,
// rather than reaching for window.localStorage itself -- see app.js).
export function memoryStorage() {
  const items = new Map();
  return {
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: (key) => items.delete(key),
  };
}

// A stand-in for the browser's fetch, in the shape js/api.js's `fetchImpl`
// expects: called with (url, init), answering an object with `ok`,
// `status` and `json()`. This is what makes startApp() testable at all --
// it takes fetchImpl as an argument instead of calling the real fetch --
// so tests/web/app.test.js can stand up the whole page against canned
// answers, the same way fakeApi stands in for js/api.js itself in every
// other test file.
//
// `handlers` maps "METHOD path" (no leading /api/v1/, no query string) to
// a { status, body } object, or a function of `init` returning one -- or
// one that throws/rejects, to emulate a request that never reached the
// server the way a real fetch failure would (api.js's own request() turns
// that into ApiError(0, ...)).
export function fetchStub(handlers) {
  const calls = [];
  return {
    calls,
    fetchImpl: async (url, init = {}) => {
      calls.push({ url, init });
      const path = new URL(url, "http://localhost").pathname.replace(/^\/api\/v1\//, "");
      const key = `${init.method ?? "GET"} ${path}`;
      const handler = handlers[key];
      if (handler === undefined) throw new Error(`fetchStub: no handler for ${key}`);
      const { status = 200, body = {} } = typeof handler === "function" ? await handler(init) : handler;
      return { ok: status < 400, status, json: async () => body };
    },
  };
}

export const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

export function type(input, value) {
  input.value = value;
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
}

export function submit(form) {
  form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
}

// A name a player could really choose.
export const HOSTILE = '<img src=x onerror="window.__pwned=1">';

// Finds the elements that parsing a string like HOSTILE as HTML would
// produce: an img/script/iframe/svg tag, or any on* attribute. It is not a
// general markup detector -- it looks for exactly what HOSTILE's shape
// creates, nothing broader -- but that is what the hostile-name tests need.
// They assert on STRUCTURE because jsdom never loads images: an injected
// onerror would not fire here even if the injection happened, so a
// `window.__pwned` check would pass against the very bug it exists to catch.
export function markupIn(root) {
  return [...root.querySelectorAll("*")].filter(
    (node) =>
      ["img", "script", "iframe", "svg"].includes(node.localName) ||
      [...node.attributes].some((attribute) => attribute.name.startsWith("on")),
  );
}
