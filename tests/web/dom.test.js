import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { HOSTILE, installDom, markupIn } from "./helpers.js";
import { el, replace } from "../../src/ro_admin/web/js/dom.js";

beforeEach(() => installDom());

test("a string child is text, never markup", () => {
  const node = el("p", {}, HOSTILE);
  assert.deepEqual(markupIn(node), []);
  assert.equal(node.textContent, HOSTILE);
});

// markupIn is the detector every hostile-name test above relies on. This
// proves it actually finds what it claims to, using no forbidden sink of
// its own -- document.createElement and setAttribute build the same shapes
// a real markup parse would, without ever parsing a string as HTML.
test("markupIn finds what parsing HOSTILE would produce, and nothing else", () => {
  const withImg = document.createElement("p");
  withImg.append(document.createElement("img"));
  assert.equal(markupIn(withImg).length, 1);

  const withOnClick = document.createElement("div");
  const span = document.createElement("span");
  span.setAttribute("onclick", "x");
  withOnClick.append(span);
  assert.equal(markupIn(withOnClick).length, 1);

  const textOnly = el("p", {}, "just words");
  assert.equal(markupIn(textOnly).length, 0);
});

test("attributes outside the allowlist are refused", () => {
  assert.throws(() => el("div", { onclick: "alert(1)" }), /not allowed/);
  assert.throws(() => el("a", { href: "javascript:alert(1)" }), /not allowed/);
  assert.throws(() => el("div", { style: "color:red" }), /not allowed/);
});

test("autocapitalize is allowed, for userid fields on phone keyboards", () => {
  const node = el("input", { autocapitalize: "none" });
  assert.equal(node.getAttribute("autocapitalize"), "none");
});

test("an attribute value is text too", () => {
  const node = el("input", { value: HOSTILE, placeholder: '" onfocus="x' });
  assert.equal(node.getAttribute("value"), HOSTILE);
  assert.deepEqual(node.getAttributeNames().sort(), ["placeholder", "value"]);
});

test("listeners attach through `on`, not through attributes", () => {
  let clicks = 0;
  const button = el("button", { on: { click: () => (clicks += 1) } }, "Go");
  button.click();
  assert.equal(clicks, 1);
  assert.deepEqual(button.getAttributeNames(), []);
});

test("a non-function listener is refused", () => {
  assert.throws(() => el("button", { on: { click: undefined } }), /must be a function/);
  assert.throws(() => el("button", { on: { click: "alert(1)" } }), /must be a function/);
  assert.throws(() => el("button", { on: [() => {}] }), /must be a plain object/);
  assert.throws(() => el("button", { on: null }), /must be a plain object/);
});

test("a disallowed tag is refused", () => {
  assert.throws(() => el("script", {}, "1+1"), /not allowed/);
  assert.throws(() => el("iframe"), /not allowed/);
});

test("true sets a boolean attribute; false, null and undefined omit it", () => {
  const node = el("input", { disabled: true, required: false, hidden: null, name: undefined });
  assert.deepEqual(node.getAttributeNames(), ["disabled"]);
});

test("false on a non-boolean attribute is written out, not omitted", () => {
  const node = el("input", { spellcheck: false });
  assert.equal(node.getAttribute("spellcheck"), "false");
});

test("a boolean attribute refuses a non-boolean value", () => {
  assert.throws(() => el("input", { disabled: "false" }), /must be a boolean/);
  assert.throws(() => el("input", { hidden: 1 }), /must be a boolean/);
  assert.throws(() => el("input", { required: "" }), /must be a boolean/);
});

test('tabindex accepts only the string "-1"', () => {
  const node = el("h1", { tabindex: "-1" }, "Title");
  assert.equal(node.getAttribute("tabindex"), "-1");
  // A positive value would join the normal Tab order (never wanted here);
  // "0" would add an element that was never meant to be Tab-reachable at
  // all; -1 the NUMBER is not the same value as the "-1" this allowlist
  // checks against. All refused, the same as any other malformed attribute.
  assert.throws(() => el("h1", { tabindex: "0" }), /must be "-1"/);
  assert.throws(() => el("h1", { tabindex: "1" }), /must be "-1"/);
  assert.throws(() => el("h1", { tabindex: -1 }), /must be "-1"/);
  assert.throws(() => el("h1", { tabindex: null }), /must be "-1"/);
});

test("a refused tag is caught regardless of case", () => {
  assert.throws(() => el("SCRIPT", {}, "1+1"), /not allowed/);
  assert.throws(() => el("Iframe"), /not allowed/);
});

test("an object child is refused, not stringified", () => {
  assert.throws(() => el("p", {}, { not: "text" }), /not allowed/);
  assert.throws(() => el("p", {}, true), /not allowed/);
});

test("0 is a real child, not skipped like false", () => {
  const node = el("p", {}, 0);
  assert.equal(node.textContent, "0");
});

test("props may be omitted with null", () => {
  const node = el("p", null, "x");
  assert.equal(node.textContent, "x");
});

test("children may be nested arrays, nodes, numbers, or skipped", () => {
  const node = el("ul", {}, [el("li", {}, "a"), [el("li", {}, 2)]], null, false, undefined);
  assert.equal(node.children.length, 2);
  assert.equal(node.textContent, "a2");
});

test("replace swaps every child", () => {
  const node = el("div", {}, "old", el("span", {}, "old"));
  replace(node, "new");
  assert.equal(node.textContent, "new");
  assert.equal(node.childNodes.length, 1);
  replace(node);
  assert.equal(node.childNodes.length, 0);
});

test("replace accepts nested arrays too", () => {
  const node = el("ul", {});
  replace(node, [el("li", {}, "a"), [el("li", {}, "b")]]);
  assert.equal(node.children.length, 2);
  assert.equal(node.textContent, "ab");
});
