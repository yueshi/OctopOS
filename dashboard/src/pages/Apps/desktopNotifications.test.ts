import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  notificationsApi,
  type ServerNotification,
} from "../../api/modules/notifications";
import {
  addDesktopNotification,
  clearDesktopNotifications,
  getDesktopNotifications,
  handleDesktopNotificationFrame,
  markAllDesktopNotificationsRead,
  markDesktopNotificationRead,
  resetDesktopNotifications,
  syncDesktopNotifications,
  upsertServerNotification,
} from "./desktopNotifications";

vi.mock("../../api/modules/notifications", () => ({
  notificationsApi: {
    list: vi.fn().mockResolvedValue({ notifications: [], unread: 0 }),
    markRead: vi.fn().mockResolvedValue({ ok: true }),
    markAllRead: vi.fn().mockResolvedValue({ updated: 0 }),
    clear: vi.fn().mockResolvedValue({ deleted: 0 }),
  },
}));

function serverRow(
  overrides: Partial<ServerNotification> = {},
): ServerNotification {
  return {
    notification_id: "ntf_1",
    kind: "approval",
    title: "Server approval",
    body: "",
    path: "/apps/app1",
    dedupe_key: "access:req1",
    read: false,
    created_at: 100,
    ...overrides,
  };
}

describe("desktopNotifications", () => {
  beforeEach(() => {
    resetDesktopNotifications();
    vi.clearAllMocks();
  });

  it("adds unread notifications newest first", () => {
    addDesktopNotification({ kind: "info", title: "One" });
    addDesktopNotification({ kind: "success", title: "Two" });

    expect(getDesktopNotifications().map((item) => item.title)).toEqual([
      "Two",
      "One",
    ]);
    expect(getDesktopNotifications()[0].read).toBe(false);
  });

  it("suppresses duplicate unread notifications with one dedupe key", () => {
    addDesktopNotification({
      kind: "approval",
      title: "Approval",
      dedupeKey: "chat-1",
    });
    addDesktopNotification({
      kind: "approval",
      title: "Approval",
      dedupeKey: "chat-1",
    });

    expect(getDesktopNotifications()).toHaveLength(1);
  });

  it("marks one or all notifications as read", () => {
    const first = addDesktopNotification({ kind: "info", title: "One" })!;
    addDesktopNotification({ kind: "info", title: "Two" });

    markDesktopNotificationRead(first);
    expect(
      getDesktopNotifications().find((item) => item.id === first)?.read,
    ).toBe(true);

    markAllDesktopNotificationsRead();
    expect(getDesktopNotifications().every((item) => item.read)).toBe(true);
  });

  it("clears the list", () => {
    addDesktopNotification({ kind: "info", title: "One" });
    clearDesktopNotifications();

    expect(getDesktopNotifications()).toEqual([]);
  });

  it("merges server rows and lets them suppress a local duplicate", async () => {
    vi.mocked(notificationsApi.list).mockResolvedValue({
      notifications: [serverRow()],
      unread: 1,
    });
    addDesktopNotification({
      kind: "approval",
      title: "Local approval",
      dedupeKey: "access:req1",
    });

    await syncDesktopNotifications();

    expect(getDesktopNotifications().map((item) => item.title)).toEqual([
      "Server approval",
    ]);
    expect(getDesktopNotifications()[0].source).toBe("server");
  });

  it("marks a server row read through the API and applies read frames", async () => {
    vi.mocked(notificationsApi.list).mockResolvedValue({
      notifications: [serverRow()],
      unread: 1,
    });
    await syncDesktopNotifications();

    markDesktopNotificationRead("ntf_1");
    expect(notificationsApi.markRead).toHaveBeenCalledWith("ntf_1");
    expect(getDesktopNotifications()[0].read).toBe(true);

    upsertServerNotification(serverRow({ read: false }));
    handleDesktopNotificationFrame({
      type: "notification.read",
      notification_id: "ntf_1",
    });
    expect(getDesktopNotifications()[0].read).toBe(true);
  });

  it("upserts pushed rows and clears them on a clear frame", () => {
    upsertServerNotification(serverRow({ notification_id: "ntf_2" }));
    expect(getDesktopNotifications()).toHaveLength(1);

    handleDesktopNotificationFrame({ type: "notification.cleared" });
    expect(getDesktopNotifications()).toEqual([]);
  });
});
