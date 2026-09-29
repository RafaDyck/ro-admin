/**
 * Which actions to offer. An action is shown only if the API would accept it.
 *
 * Neither half is decided here. /auth/me says whether this person may write,
 * computed by the function that enforces it. /system/capabilities says, per
 * action, whether anything would run it, and if not, why, in a sentence
 * written to name the operator's next step. So this module holds no copy of
 * the permission table and no mapping of actions to tiers -- and, unlike an
 * earlier version, no label text either. `labels` is the caller's own table
 * ({action: label}), the same one actions.js keys its forms by. Two separate
 * copies of that table, one here and one there, would drift the moment
 * either gained an action the other hadn't caught up to, and an action known
 * to one but not the other would throw at render time rather than simply
 * not appear. A button the API would refuse, or one this UI has no form
 * for, is the client claiming a capability the product does not have.
 */
export function actionPlan(me, caps, labels) {
  if (!me.permissions.includes("commands.write")) return [];
  // Walks the caller's labels rather than the API's list: an action this UI
  // has no form for is left out, rather than rendered as a button that does
  // nothing.
  return Object.keys(labels)
    .filter((action) => Object.hasOwn(caps.actions, action))
    .map((action) => ({
      action,
      label: labels[action],
      available: caps.actions[action].available,
      reason: caps.actions[action].reason,
    }));
}
