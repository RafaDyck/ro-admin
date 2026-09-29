/**
 * The only way the UI builds DOM.
 *
 * Every string becomes a Text node or an attribute value, and nothing is
 * parsed as markup. Player-chosen names are rendered all over this UI, and a
 * character named `<img src=x onerror=...>` must display as exactly that.
 * scripts/check_no_innerhtml.py forbids every other route to the DOM.
 *
 * Attributes are an allowlist rather than a denylist. Refusing `on*` alone
 * would still admit `href="javascript:..."`, `src` and `style`. Listeners go
 * through `on`, a plain object of event name to function, never strings --
 * an event attribute is itself a script sink, and a listener that silently
 * fails to attach (a typo'd key, a stray array) is a bug hiding as a dead
 * button, so `on` and each of its values are checked, not just trusted.
 *
 * Boolean HTML attributes (`hidden`, `disabled`, `required`) are the only
 * ones where presence alone means true and absence means false; `true` sets
 * them empty and `false` omits them. Every other attribute -- `spellcheck`,
 * `aria-live`, and the rest -- takes its value as text, where the string
 * "false" is not the same as being absent, so `false` there is written out
 * rather than dropped.
 *
 * Children may be strings, numbers, bigints or Nodes (arrays nest freely,
 * and `null`/`undefined`/`false` are skipped so a conditional child can be
 * written inline). Anything else -- a plain object, `true`, a Node from a
 * different window -- is refused rather than silently stringified into
 * `[object Object]`, which has hidden real bugs before.
 *
 * A handful of tags are refused outright: `script`, `style`, `iframe`,
 * `object`, `embed`, `base`, `link`, `meta`, `template`, `frame` and
 * `frameset` can load or execute content this UI never needs to. CSP and
 * Trusted Types already stop them at runtime; refusing them here is a
 * second, independent layer rather than the only one.
 */
const ALLOWED_ATTRIBUTES = new Set([
  "class", "type", "name", "value", "placeholder",
  // autocapitalize: phone keyboards capitalise userids otherwise.
  "autocomplete", "autocapitalize", "spellcheck", "hidden", "disabled", "required",
  "aria-label", "aria-live",
  // tabindex: only ever "-1" here (checked below, not just allowed) --
  // makes a view's own heading a valid focus() target without adding it
  // to the normal Tab order -- app.js moves focus there on a route
  // change, so a screen reader announces the new page. Any OTHER value
  // (a positive number reordering Tab, "0" adding an element that was
  // never meant to be reached by Tab at all) is a real behavioural
  // change this allowlist exists to keep out, not a typo to shrug off.
  "tabindex",
]);

const BOOLEAN_ATTRIBUTES = new Set(["hidden", "disabled", "required"]);
// The only tabindex value this UI has ever needed, or is meant to.
const TABINDEX_VALUE = "-1";

const REFUSED_TAGS = new Set([
  "script", "style", "iframe", "object", "embed", "base", "link", "meta",
  "template", "frame", "frameset",
]);

export function el(tag, props, ...children) {
  props ??= {};
  if (REFUSED_TAGS.has(String(tag).toLowerCase())) {
    throw new Error(`dom.el: <${tag}> is not allowed`);
  }
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "on") {
      if (value === null || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) {
        throw new Error("dom.el: `on` must be a plain object of event listeners");
      }
      for (const [event, listener] of Object.entries(value)) {
        if (typeof listener !== "function") {
          throw new Error(`dom.el: listener for ${JSON.stringify(event)} must be a function`);
        }
        node.addEventListener(event, listener);
      }
      continue;
    }
    if (!ALLOWED_ATTRIBUTES.has(key)) {
      throw new Error(`dom.el: attribute ${JSON.stringify(key)} is not allowed`);
    }
    if (BOOLEAN_ATTRIBUTES.has(key)) {
      if (value === null || value === undefined || value === false) continue;
      if (value === true) {
        node.setAttribute(key, "");
        continue;
      }
      throw new Error(`dom.el: attribute ${JSON.stringify(key)} must be a boolean`);
    }
    if (key === "tabindex" && value !== TABINDEX_VALUE) {
      throw new Error(`dom.el: attribute "tabindex" must be ${JSON.stringify(TABINDEX_VALUE)}`);
    }
    if (value === null || value === undefined) continue;
    node.setAttribute(key, String(value));
  }
  append(node, children);
  return node;
}

export function replace(node, ...children) {
  node.replaceChildren();
  append(node, children);
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    if (typeof child === "string" || typeof child === "number" || typeof child === "bigint") {
      node.append(document.createTextNode(String(child)));
      continue;
    }
    if (child instanceof Node) {
      node.append(child);
      continue;
    }
    throw new Error(`dom.append: a child of type ${typeof child} is not allowed`);
  }
}
