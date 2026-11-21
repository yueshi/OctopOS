import type { ChatMessage } from "../hooks/sseHelpers";

function textOf(message: ChatMessage): string {
  return (message.content || "").trim();
}

function lastUserIndex(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") return i;
  }
  return -1;
}

/** Local turn still on screen: from the latest user message through the end. */
function inFlightTail(current: ChatMessage[]): ChatMessage[] {
  const idx = lastUserIndex(current);
  if (idx < 0) return current;
  return current.slice(idx);
}

function newestTimestamp(messages: ChatMessage[]): number {
  let newest = 0;
  for (const message of messages) {
    newest = Math.max(newest, message.timestamp || 0);
  }
  return newest;
}

/**
 * A genuine local turn starts with a user row newer than the fetched page.
 * An assistant-only fragment (page reloaded mid-turn) or a cached old turn
 * (the page window moved past it) must not be appended after server rows.
 */
function localTurnIsNewer(tail: ChatMessage[], loaded: ChatMessage[]): boolean {
  const userIdx = lastUserIndex(tail);
  if (userIdx < 0) return false;
  return (tail[userIdx].timestamp || 0) > newestTimestamp(loaded);
}

/**
 * True when `base` already ends with this local turn (server persisted it
 * under different ids). A later send of the same text is kept.
 */
function sameTurnAlreadyLoaded(
  base: ChatMessage[],
  tail: ChatMessage[],
): boolean {
  const tailUserIdx = lastUserIndex(tail);
  if (tailUserIdx < 0) return false;
  const tailUser = tail[tailUserIdx];
  const tailText = textOf(tailUser);
  if (!tailText) return false;
  const baseUserIdx = lastUserIndex(base);
  if (baseUserIdx < 0) return false;
  const baseUser = base[baseUserIdx];
  if (textOf(baseUser) !== tailText) return false;
  const baseTs = baseUser.timestamp || 0;
  const tailTs = tailUser.timestamp || 0;
  if (baseTs > 0 && tailTs > baseTs + 1000) return false;
  return true;
}

/**
 * Apply a server history page without dropping a turn the user just sent.
 *
 * Opening an old thread fetches history before the optimistic bubble exists.
 * The response then replaces the list and the bubble vanishes, while the
 * WebSocket turn keeps running. Messages after the last shared id are that
 * local turn and stay on screen until the server page already includes them.
 */
export function mergeHistoryWithLocalTail(
  loaded: ChatMessage[],
  current: ChatMessage[],
): ChatMessage[] {
  if (current.length === 0) return loaded;
  if (loaded.length === 0) return current;

  const loadedIds = new Set(loaded.map((message) => message.id));
  const firstOverlap = current.findIndex((message) =>
    loadedIds.has(message.id),
  );

  if (firstOverlap < 0) {
    const tail = inFlightTail(current);
    // Orphaned live fragment or a cached turn older than the server page:
    // the page is authoritative.
    if (!localTurnIsNewer(tail, loaded)) return loaded;
    if (sameTurnAlreadyLoaded(loaded, tail)) return loaded;
    return [...loaded, ...tail];
  }

  let lastOverlap = firstOverlap;
  for (let i = current.length - 1; i >= 0; i--) {
    if (loadedIds.has(current[i].id)) {
      lastOverlap = i;
      break;
    }
  }

  const olderPrefix = current.slice(0, firstOverlap);
  const localTail = current.slice(lastOverlap + 1);
  const base = [...olderPrefix, ...loaded];
  if (localTail.length === 0) return base;

  const overlapId = current[lastOverlap].id;
  const loadedOverlap = loaded.findIndex((message) => message.id === overlapId);
  const serverAfter = loadedOverlap >= 0 ? loaded.slice(loadedOverlap + 1) : [];
  if (serverAfter.length === 0) return [...base, ...localTail];
  // Same rule: an orphan fragment or an old cached turn must not be appended
  // after the server's complete copy of the turn.
  if (!localTurnIsNewer(localTail, loaded)) return base;
  if (sameTurnAlreadyLoaded(serverAfter, localTail)) return base;
  return [...base, ...localTail];
}
