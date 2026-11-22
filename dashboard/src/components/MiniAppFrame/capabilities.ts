/**
 * Page-facing capabilities for a MiniApp.
 *
 * The host bridge is the enforcement point for page actions: the iframe has
 * no JWT and no network (`connect-src 'none'`), so every request it makes
 * goes through this allowlist. A missing list means an older payload; treat
 * it as the legacy all-on set rather than breaking existing apps.
 */
export const CAP = {
  DATA_READ: "data.read",
  DATA_WRITE: "data.write",
  ASSET_READ: "asset.read",
  ASSET_WRITE: "asset.write",
  EXTERNAL_OPEN: "external.open",
  CHAT_COMPOSE: "chat.compose",
  EVENTS_READ: "events.read",
  AI_GENERATE: "ai.generate",
  AI_TASK: "ai.agent_task",
  GAME_LAUNCH: "game.launch",
  EXTERNAL_FETCH: "external.fetch",
  EVENTS_EMIT: "events.emit",
  SCHEDULES_MANAGE: "schedules.manage",
} as const;

export const ALL_CAPABILITIES: readonly string[] = Object.values(CAP);

/**
 * Capabilities an older payload without a manifest keeps. Frozen at the R1
 * set: a capability added later must be opt-in, never inherited by legacy
 * apps just because the bridge knows its name.
 */
export const LEGACY_CAPABILITIES: readonly string[] = [
  CAP.DATA_READ,
  CAP.DATA_WRITE,
  CAP.ASSET_READ,
  CAP.EXTERNAL_OPEN,
  CAP.CHAT_COMPOSE,
  CAP.EVENTS_READ,
  CAP.AI_GENERATE,
  CAP.GAME_LAUNCH,
];

/**
 * System-app capabilities. Deliberately kept out of ``CAP`` /
 * ``ALL_CAPABILITIES``: ``effectiveCapabilities(undefined)`` falls back to the
 * legacy all-on set, so adding them there would silently grant every old or
 * unversioned MiniApp access to platform configuration data.
 */
export const SYSTEM_CAP = {
  READ: "system.read",
  ACT: "system.act",
  MANAGE: "system.manage",
} as const;

export const SYSTEM_APP_CAPABILITIES: readonly string[] = [SYSTEM_CAP.READ];

const METHOD_CAPABILITY: Record<string, string> = {
  schema: CAP.DATA_READ,
  tables: CAP.DATA_READ,
  rows: CAP.DATA_READ,
  aggregate: CAP.DATA_READ,
  append: CAP.DATA_WRITE,
  update: CAP.DATA_WRITE,
  updateMany: CAP.DATA_WRITE,
  remove: CAP.DATA_WRITE,
  generate: CAP.AI_GENERATE,
  upload: CAP.ASSET_WRITE,
  runTask: CAP.AI_TASK,
  getTask: CAP.AI_TASK,
  cancelTask: CAP.AI_TASK,
  launchGame: CAP.GAME_LAUNCH,
  asset: CAP.ASSET_READ,
  openExternal: CAP.EXTERNAL_OPEN,
  composeMessage: CAP.CHAT_COMPOSE,
  service: CAP.EXTERNAL_FETCH,
  serviceDetailed: CAP.EXTERNAL_FETCH,
  serviceImage: CAP.EXTERNAL_FETCH,
  events: CAP.EVENTS_READ,
  ackEvents: CAP.EVENTS_READ,
  emitEvent: CAP.EVENTS_EMIT,
  systemQuery: SYSTEM_CAP.READ,
  systemAction: SYSTEM_CAP.ACT,
  openManager: SYSTEM_CAP.MANAGE,
};

export function effectiveCapabilities(
  capabilities?: readonly string[],
): readonly string[] {
  return capabilities ?? LEGACY_CAPABILITIES;
}

/**
 * Whether the page may call *method*.
 *
 * A method with no entry in the map is denied: it has no known capability
 * requirement, and allowing it would make every method added later open by
 * default. `me` is always allowed — it reports the page's own permission and
 * capabilities, and reveals nothing.
 */
export function isMethodAllowed(
  method: string | undefined,
  capabilities?: readonly string[],
): boolean {
  if (!method) return false;
  if (method === "me") return true;
  const required = METHOD_CAPABILITY[method];
  if (!required) return false;
  return effectiveCapabilities(capabilities).includes(required);
}
