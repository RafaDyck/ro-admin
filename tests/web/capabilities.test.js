import { test } from "node:test";
import assert from "node:assert/strict";
import { actionPlan } from "../../src/ro_admin/web/js/capabilities.js";

const ADMIN = {
  subject: "admin",
  level: 99,
  permissions: ["logs.read", "characters.read", "system.read", "commands.read", "commands.write"],
};
const STAFF = {
  subject: "gm",
  level: 10,
  permissions: ["logs.read", "characters.read", "system.read", "commands.read"],
};
const ON = { available: true, reason: "overlay responding, last seen 0s ago" };
const OFF = {
  available: false,
  reason: "overlay not installed: run overlay/schema.sql against this database",
};

// The table this UI has forms for. In the shipped app, actions.js owns and
// exports this, keyed the same way as its FORMS; the dossier passes it in.
const LABELS = {
  give_item: "Give item",
  adjust_zeny: "Adjust zeny",
  sync_character: "Sync from game",
};

function caps(tier1, tier2) {
  return {
    actions: {
      give_item: { tier: "tier1", ...tier1 },
      adjust_zeny: { tier: "tier1", ...tier1 },
      sync_character: { tier: "tier2", ...tier2 },
    },
  };
}

test("without commands.write there is no action bar at all", () => {
  assert.deepEqual(actionPlan(STAFF, caps(ON, ON), LABELS), []);
});

test("an admin on a full install is offered every action", () => {
  const plan = actionPlan(ADMIN, caps(ON, ON), LABELS);
  assert.deepEqual(plan.map((a) => a.action), ["give_item", "adjust_zeny", "sync_character"]);
  assert.ok(plan.every((a) => a.available));
});

test("a tier that is off is explained in the API's own words", () => {
  const plan = actionPlan(ADMIN, caps(ON, OFF), LABELS);
  const sync = plan.find((a) => a.action === "sync_character");
  assert.equal(sync.available, false);
  assert.equal(sync.reason, OFF.reason);
  assert.equal(plan.find((a) => a.action === "give_item").available, true);
});

test("reason passes through even when the action is available", () => {
  const plan = actionPlan(ADMIN, caps(ON, ON), LABELS);
  assert.equal(plan.find((a) => a.action === "give_item").reason, ON.reason);
});

test("the label is the caller's own text, not a copy held here", () => {
  const plan = actionPlan(ADMIN, caps(ON, ON), LABELS);
  assert.equal(plan.find((a) => a.action === "give_item").label, "Give item");
  assert.equal(plan.find((a) => a.action === "sync_character").label, "Sync from game");
});

test("an action this UI has no form for is not offered", () => {
  const withExtra = caps(ON, ON);
  withExtra.actions.rename_character = { tier: "tier1", ...ON };
  assert.ok(!actionPlan(ADMIN, withExtra, LABELS).some((a) => a.action === "rename_character"));
});

test("a labelled action with no matching capability is not offered either", () => {
  const labelsWithExtra = { ...LABELS, teleport_character: "Teleport" };
  const plan = actionPlan(ADMIN, caps(ON, ON), labelsWithExtra);
  assert.ok(!plan.some((a) => a.action === "teleport_character"));
  assert.deepEqual(plan.map((a) => a.action), ["give_item", "adjust_zeny", "sync_character"]);
});
