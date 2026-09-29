/**
 * Pure formatting: no DOM, no network.
 *
 * Timestamps from the API are the DATABASE's clock with no timezone (MySQL
 * DATETIME). They are shown as written and never compared with this browser's
 * clock. The API host, the database and the operator's laptop are routinely
 * different machines, and a few minutes of skew would turn "verified 4s ago"
 * into a confident lie. Whether a value is fresh is the API's judgement
 * (`stale`), made on one clock; this module only words it.
 */
const GROUPED = new Intl.NumberFormat("en-US");

export function number(value) {
  const finite = typeof value === "number" && Number.isFinite(value);
  const big = typeof value === "bigint";
  // null, "", true and friends are not zero or one -- they are unknown, and
  // formatting them as a number states a fact the caller never gave us.
  if (!finite && !big) return "—";
  return GROUPED.format(value);
}

// A DB-clock value carries no timezone and is shown as written. If the
// string DOES carry one -- a trailing Z or a numeric offset -- that is
// information the source chose to include, and dropping it would turn an
// unambiguous instant into a naive one.
const TZ_SUFFIX = /(Z|[+-]\d{2}:\d{2})$/;

export function serverTime(iso) {
  if (!iso) return "";
  const text = String(iso);
  const [suffix] = text.match(TZ_SUFFIX) ?? [""];
  const base = suffix ? text.slice(0, text.length - suffix.length) : text;
  return base.replace("T", " ").slice(0, 19) + suffix;
}

const MAY_BE_BEHIND =
  "may be behind the game: an online character is saved on logout or at the next autosave";

export function freshness(character) {
  // Offline: nothing holds newer state, so the stored row is the truth.
  if (!character.online) return null;
  if (!character.stale) {
    // "Verified" with no timestamp to point at would be a confident claim
    // resting on nothing -- the same shape of lie clock skew would tell,
    // just from a missing value instead of a wrong one.
    return character.synced_at
      ? `verified against the game at ${serverTime(character.synced_at)} (server clock)`
      : null;
  }
  return character.synced_at
    ? `${MAY_BE_BEHIND}; last verified ${serverTime(character.synced_at)} (server clock)`
    : MAY_BE_BEHIND;
}

export function inventoryFreshness(inventory) {
  // Weaker than the character's claim, and never borrowed from it. A sync
  // verifies the `char` row; nothing observes the inventory land.
  return inventory.stale
    ? "may be behind the game: saved on logout or at the next autosave, and a sync does not verify the inventory"
    : null;
}
