import { useSyncExternalStore } from "react";

import {
  notificationsApi,
  type ServerNotification,
} from "../../api/modules/notifications";

export type DesktopNotificationKind =
  | "approval"
  | "success"
  | "info"
  | "error"
  | "task"
  | "app_task"
  | "security"
  | "cron";

export interface DesktopNotificationInput {
  kind: DesktopNotificationKind;
  title: string;
  body?: string;
  /** Desktop window to focus when the notification is clicked. */
  windowId?: string;
  /** Dashboard route to open when the notification is clicked. */
  path?: string;
  /** Suppress duplicate unread notifications with the same key. */
  dedupeKey?: string;
}

export interface DesktopNotification extends DesktopNotificationInput {
  id: string;
  createdAt: number;
  read: boolean;
  /** Server rows survive refresh; local rows are ephemeral chat hints. */
  source?: "server" | "local";
}

const MAX_NOTIFICATIONS = 50;
const KINDS = new Set<DesktopNotificationKind>([
  "approval",
  "success",
  "info",
  "error",
  "task",
  "app_task",
  "security",
  "cron",
]);

let localItems: DesktopNotification[] = [];
let serverItems: DesktopNotification[] = [];
let items: DesktopNotification[] = [];
let sequence = 0;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function normalizeKind(kind: string): DesktopNotificationKind {
  return KINDS.has(kind as DesktopNotificationKind)
    ? (kind as DesktopNotificationKind)
    : "info";
}

function fromServer(row: ServerNotification): DesktopNotification {
  return {
    id: row.notification_id,
    kind: normalizeKind(row.kind),
    title: row.title,
    body: row.body || undefined,
    path: row.path ?? undefined,
    dedupeKey: row.dedupe_key ?? undefined,
    createdAt: row.created_at * 1000,
    read: row.read,
    source: "server",
  };
}

function recompute(): void {
  const serverKeys = new Set(
    serverItems
      .map((item) => item.dedupeKey)
      .filter((key): key is string => Boolean(key)),
  );
  const local = localItems.filter(
    (item) => !item.dedupeKey || !serverKeys.has(item.dedupeKey),
  );
  items = [...serverItems, ...local]
    .sort((a, b) => b.createdAt - a.createdAt)
    .slice(0, MAX_NOTIFICATIONS);
  emit();
}

function fireAndForget(request: () => Promise<unknown>): void {
  try {
    void request().catch(() => {
      // The inbox remains usable offline; the next sync reconciles.
    });
  } catch {
    // A synchronous client error must not break the UI.
  }
}

export function addDesktopNotification(
  input: DesktopNotificationInput,
): string | null {
  if (
    input.dedupeKey &&
    (serverItems.some((item) => item.dedupeKey === input.dedupeKey) ||
      localItems.some(
        (item) => !item.read && item.dedupeKey === input.dedupeKey,
      ))
  ) {
    return null;
  }
  sequence += 1;
  const item: DesktopNotification = {
    ...input,
    id: `desk-${Date.now()}-${sequence}`,
    createdAt: Date.now(),
    read: false,
    source: "local",
  };
  localItems = [item, ...localItems].slice(0, MAX_NOTIFICATIONS);
  recompute();
  return item.id;
}

/** Fetch the durable inbox and merge it with local, ephemeral hints. */
export async function syncDesktopNotifications(): Promise<void> {
  try {
    const data = await notificationsApi.list();
    serverItems = data.notifications.map(fromServer);
    recompute();
  } catch {
    // Offline or unauthenticated: keep whatever the last good snapshot had.
  }
}

/** Upsert one row pushed over the dashboard notification socket. */
export function upsertServerNotification(row: ServerNotification): void {
  const mapped = fromServer(row);
  serverItems = [
    mapped,
    ...serverItems.filter((item) => item.id !== mapped.id),
  ].slice(0, MAX_NOTIFICATIONS);
  recompute();
}

function markServerRead(id: string, callApi: boolean): boolean {
  const target = serverItems.find((item) => item.id === id);
  if (!target) return false;
  if (!target.read) {
    serverItems = serverItems.map((item) =>
      item.id === id ? { ...item, read: true } : item,
    );
    recompute();
  }
  if (callApi && !target.read) {
    fireAndForget(() => notificationsApi.markRead(id));
  }
  return true;
}

function setAllServerRead(callApi: boolean): void {
  const hadUnread = serverItems.some((item) => !item.read);
  if (hadUnread) {
    serverItems = serverItems.map((item) =>
      item.read ? item : { ...item, read: true },
    );
    recompute();
  }
  if (callApi && hadUnread) {
    fireAndForget(() => notificationsApi.markAllRead());
  }
}

function clearServer(callApi: boolean): void {
  const hadRows = serverItems.length > 0;
  if (hadRows) {
    serverItems = [];
    recompute();
  }
  if (callApi && hadRows) {
    fireAndForget(() => notificationsApi.clear());
  }
}

/** Handle server frames that keep tabs in sync after a read/clear elsewhere. */
export function handleDesktopNotificationFrame(raw: unknown): boolean {
  const frame = raw as {
    type?: string;
    notification?: ServerNotification;
    notification_id?: string;
  };
  if (frame?.type === "notification" && frame.notification) {
    upsertServerNotification(frame.notification);
    return true;
  }
  if (frame?.type === "notification.read" && frame.notification_id) {
    markServerRead(frame.notification_id, false);
    return true;
  }
  if (frame?.type === "notification.read_all") {
    setAllServerRead(false);
    return true;
  }
  if (frame?.type === "notification.cleared") {
    clearServer(false);
    return true;
  }
  return false;
}

export function markDesktopNotificationRead(id: string): void {
  if (markServerRead(id, true)) return;
  let changed = false;
  localItems = localItems.map((item) => {
    if (item.id !== id || item.read) return item;
    changed = true;
    return { ...item, read: true };
  });
  if (changed) recompute();
}

export function markAllDesktopNotificationsRead(): void {
  setAllServerRead(true);
  if (localItems.every((item) => item.read)) return;
  localItems = localItems.map((item) =>
    item.read ? item : { ...item, read: true },
  );
  recompute();
}

export function clearDesktopNotifications(): void {
  const hadLocal = localItems.length > 0;
  const hadServer = serverItems.length > 0;
  localItems = [];
  serverItems = [];
  if (hadLocal || hadServer) recompute();
  if (hadServer) {
    fireAndForget(() => notificationsApi.clear());
  }
}

/** Test/account-switch helper: drop both snapshots without a network call. */
export function resetDesktopNotifications(): void {
  localItems = [];
  serverItems = [];
  items = [];
  emit();
}

export function subscribeDesktopNotifications(
  listener: () => void,
): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getDesktopNotifications(): DesktopNotification[] {
  return items;
}

export function useDesktopNotifications(): DesktopNotification[] {
  return useSyncExternalStore(
    subscribeDesktopNotifications,
    getDesktopNotifications,
    getDesktopNotifications,
  );
}
