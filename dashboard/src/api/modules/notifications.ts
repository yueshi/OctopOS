import { request } from "../request";

/** One durable notification row from the server inbox. */
export interface ServerNotification {
  notification_id: string;
  kind: string;
  title: string;
  body: string;
  path: string | null;
  dedupe_key: string | null;
  read: boolean;
  /** Unix epoch seconds. */
  created_at: number;
}

export interface NotificationList {
  notifications: ServerNotification[];
  unread: number;
}

export const notificationsApi = {
  list: (limit = 50) =>
    request<NotificationList>(`/notifications?limit=${limit}`),

  markRead: (notificationId: string) =>
    request<{ ok: boolean }>(
      `/notifications/${encodeURIComponent(notificationId)}/read`,
      { method: "POST" },
    ),

  markAllRead: () =>
    request<{ updated: number }>("/notifications/read-all", {
      method: "POST",
    }),

  clear: () =>
    request<{ deleted: number }>("/notifications", { method: "DELETE" }),
};
