import { test } from "node:test";
import assert from "node:assert/strict";
import {
  freshness, inventoryFreshness, number, serverTime,
} from "../../src/ro_admin/web/js/format.js";

test("numbers are grouped", () => {
  assert.equal(number(1234567), "1,234,567");
  assert.equal(number(-5000), "-5,000");
  assert.equal(number(0), "0");
});

test("a non-numeric value is unknown, not a confident zero or one", () => {
  assert.equal(number(null), "—");
  assert.equal(number(undefined), "—");
  assert.equal(number(""), "—");
  assert.equal(number(true), "—");
  assert.equal(number(NaN), "—");
  assert.equal(number(Infinity), "—");
});

test("a bigint is grouped like any other number", () => {
  assert.equal(number(1234567n), "1,234,567");
});

test("server times are shown as the server wrote them", () => {
  assert.equal(serverTime("2026-09-26T10:11:12"), "2026-09-26 10:11:12");
  assert.equal(serverTime("2026-09-26T10:11:12.123456"), "2026-09-26 10:11:12");
  assert.equal(serverTime(null), "");
});

test("a value carrying its own timezone keeps it, rather than dropping it", () => {
  assert.equal(serverTime("2026-09-26T10:11:12Z"), "2026-09-26 10:11:12Z");
  assert.equal(serverTime("2026-09-26T10:11:12+05:30"), "2026-09-26 10:11:12+05:30");
});

const offline = { online: false, stale: false, synced_at: null };
const unverified = { online: true, stale: true, synced_at: null };
const verified = { online: true, stale: false, synced_at: "2026-09-26T10:11:12" };
const lapsed = { online: true, stale: true, synced_at: "2026-09-26T09:00:00" };
const freshWithNoTimestamp = { online: true, stale: false, synced_at: null };

test("an offline character's row is authoritative, so nothing is said", () => {
  assert.equal(freshness(offline), null);
});

test("an online character with no evidence may be behind", () => {
  const text = freshness(unverified);
  assert.match(text, /may be behind the game/);
  assert.doesNotMatch(text, /verified/);
});

test("a recent verified sync is stated, on the server's clock", () => {
  assert.equal(
    freshness(verified),
    "verified against the game at 2026-09-26 10:11:12 (server clock)",
  );
});

test("an old sync is still reported, beside the warning", () => {
  const text = freshness(lapsed);
  assert.match(text, /may be behind the game/);
  assert.match(text, /last verified 2026-09-26 09:00:00 \(server clock\)/);
});

test("not stale but no timestamp to point at: nothing is claimed", () => {
  assert.equal(freshness(freshWithNoTimestamp), null);
});

test("the inventory's claim is its own, never the character's", () => {
  assert.equal(inventoryFreshness({ stale: false }), null);
  assert.match(inventoryFreshness({ stale: true }), /a sync does not verify the inventory/);
});
