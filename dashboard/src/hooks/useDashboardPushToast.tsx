import { App, Button, Space } from "antd";
import { Bell } from "lucide-react";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useDashboardNotifySocket } from "./useDashboardNotifySocket";
import { useAgent } from "../context/AgentContext";
import { appsApi } from "../api/modules/apps";
import { ExpertIcon } from "../pages/Experts/components/iconForName";
import { message } from "../utils/antdMessage";
import { apiErrorMessage } from "../utils/apiError";
import {
  emitSessionEvent,
  invalidateHistory,
} from "../pages/Chat/hooks/chatStore";
import {
  parseDashboardPushFrame,
  truncatePushText,
} from "../utils/dashboardPushToast";
import styles from "./useDashboardPushToast.module.less";

interface ApprovalRequiredFrame {
  type: "approval_required";
  agent_id: string;
  thread_id?: string;
  deep_link?: string;
}

interface ThreadUpdatedFrame {
  type: "thread.updated";
  agent_id: string;
  thread_id: string;
  turn_active?: boolean;
  unread_count?: number;
}

interface AppAccessRequestFrame {
  type: "app_access_request";
  request_id: string;
  agent_id: string;
  app_id?: string | null;
  app_name?: string;
  requester?: string;
  deep_link?: string;
}

interface AppAccessResolvedFrame {
  type: "app_access_resolved";
  request_id: string;
  agent_id: string;
  app_id?: string | null;
  app_name?: string;
  status: "approved" | "rejected";
  deep_link?: string;
}

function PushToastIcon({
  iconUrl,
  iconName,
}: {
  iconUrl?: string | null;
  iconName?: string | null;
}) {
  return (
    <span className={styles.icon}>
      {iconUrl || iconName ? (
        <ExpertIcon iconUrl={iconUrl} iconName={iconName} size={16} />
      ) : (
        <Bell size={16} />
      )}
    </span>
  );
}

/** Listen for dashboard text pushes and show a bottom-right toast. */
export function useDashboardPushToast(): void {
  const { notification } = App.useApp();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { refresh, agents } = useAgent();
  const agentsRef = useRef(agents);
  agentsRef.current = agents;
  const notificationRef = useRef(notification);
  notificationRef.current = notification;
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const tRef = useRef(t);
  tRef.current = t;
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useDashboardNotifySocket((raw) => {
    const frame = raw as Partial<
      | ApprovalRequiredFrame
      | ThreadUpdatedFrame
      | AppAccessRequestFrame
      | AppAccessResolvedFrame
    >;
    if (frame?.type === "app_access_request") {
      showAccessRequestToast(frame as AppAccessRequestFrame);
      return;
    }
    if (frame?.type === "app_access_resolved") {
      showAccessResolvedToast(frame as AppAccessResolvedFrame);
      return;
    }
    if (frame?.type === "approval_required") {
      showApprovalToast(frame as ApprovalRequiredFrame);
      return;
    }
    if (frame?.type === "thread.updated") {
      const updated = frame as ThreadUpdatedFrame;
      const threadId = String(updated.thread_id || "").trim();
      if (!threadId) return;
      if (updated.turn_active) {
        // Keep the optimistic Web bubble intact while the turn is running;
        // refresh only the sidebar's active/unread state.
        emitSessionEvent({
          kind: "sessionsChanged",
          sessionId: "",
          agentId: updated.agent_id,
        });
        return;
      }
      void refreshRef.current({ silent: true });
      // The chat socket's turn.ended frame may still be in flight on another
      // WebSocket; force the stale marker so an open thread refetches even if
      // its local streaming flag has not cleared yet.
      invalidateHistory(threadId, { force: true });
      emitSessionEvent({
        kind: "sessionsChanged",
        sessionId: threadId,
        agentId: updated.agent_id,
      });
      return;
    }
    const parsed = parseDashboardPushFrame(raw);
    if (!parsed) return;
    void refreshRef.current({ silent: true });
    // A proactive run wrote to this thread outside the chat socket. Stale it
    // here (not in the chat page listener) so the cached page is dropped
    // even when the push lands while another route is open.
    invalidateHistory(parsed.thread_id);
    // Refresh the sidebar and the open thread's history without a reload.
    emitSessionEvent({
      kind: "sessionsChanged",
      sessionId: parsed.thread_id,
      agentId: parsed.agent_id,
    });
    const agent = agentsRef.current.find((a) => a.agent_id === parsed.agent_id);
    const accent = agent?.color?.trim() || "#6366f1";
    const title = parsed.agent_name
      ? tRef.current("chat.pushToast.title", { name: parsed.agent_name })
      : tRef.current("chat.pushToast.titleFallback");
    const noticeKey = `dash-push-${parsed.thread_id}-${Date.now()}`;
    notificationRef.current.open({
      key: noticeKey,
      placement: "bottomRight",
      className: styles.notice,
      style: {
        ["--push-accent" as string]: accent,
      },
      message: (
        <span className={styles.header}>
          <PushToastIcon
            iconUrl={agent?.icon_url}
            iconName={agent?.icon_name}
          />
          <span className={styles.title}>{title}</span>
        </span>
      ),
      description: (
        <span className={styles.body}>{truncatePushText(parsed.text)}</span>
      ),
      duration: null,
      closable: true,
      onClick: () => {
        notificationRef.current.destroy(noticeKey);
        navigateRef.current(`/chat/${parsed.agent_id}/${parsed.thread_id}`);
      },
    });
  });

  async function resolveAccessRequest(
    frame: AppAccessRequestFrame,
    approve: boolean,
  ) {
    const noticeKey = `access-request-${frame.request_id}`;
    try {
      const updated = approve
        ? await appsApi.approveAccessRequest(frame.request_id)
        : await appsApi.rejectAccessRequest(frame.request_id);
      notificationRef.current.destroy(noticeKey);
      if (approve && updated.status === "pending") {
        message.info(
          tRef.current(
            "apps.accessRequests.partiallyApproved",
            "Your part is done; waiting for the other owner.",
          ),
        );
      } else {
        message.success(
          tRef.current(
            approve
              ? "apps.accessRequests.approved"
              : "apps.accessRequests.rejected",
            approve ? "Access approved" : "Access rejected",
          ),
        );
      }
    } catch (err) {
      message.error(
        apiErrorMessage(
          err,
          tRef.current(
            "apps.accessRequests.failed",
            "Could not resolve the request",
          ),
          tRef.current,
        ),
      );
    }
  }

  function showAccessRequestToast(frame: AppAccessRequestFrame) {
    if (!frame.request_id) return;
    const noticeKey = `access-request-${frame.request_id}`;
    const detail = [frame.requester, frame.app_name || frame.app_id]
      .filter(Boolean)
      .join(" → ");
    notificationRef.current.open({
      key: noticeKey,
      placement: "bottomRight",
      className: styles.notice,
      message: (
        <span className={styles.header}>
          <PushToastIcon />
          <span className={styles.title}>
            {tRef.current("apps.desktop.accessRequestTitle")}
          </span>
        </span>
      ),
      description: (
        <div>
          <div className={styles.body}>{detail}</div>
          <Space size={8} style={{ marginTop: 8 }}>
            <Button
              size="small"
              type="primary"
              onClick={(event) => {
                event.stopPropagation();
                void resolveAccessRequest(frame, true);
              }}
            >
              {tRef.current("apps.accessRequests.approve", "Approve")}
            </Button>
            <Button
              size="small"
              onClick={(event) => {
                event.stopPropagation();
                void resolveAccessRequest(frame, false);
              }}
            >
              {tRef.current("apps.accessRequests.reject", "Reject")}
            </Button>
          </Space>
        </div>
      ),
      duration: null,
      closable: true,
      onClick: () => {
        notificationRef.current.destroy(noticeKey);
        // The server omits deep_link for owners who cannot open the app page
        // (for example an expert owner without an app ACL).
        if (frame.deep_link) navigateRef.current(frame.deep_link);
      },
    });
  }

  function showAccessResolvedToast(frame: AppAccessResolvedFrame) {
    if (!frame.request_id) return;
    const noticeKey = `access-resolved-${frame.request_id}`;
    const name = frame.app_name || frame.app_id || "";
    const approved = frame.status === "approved";
    notificationRef.current.open({
      key: noticeKey,
      placement: "bottomRight",
      className: styles.notice,
      message: (
        <span className={styles.header}>
          <PushToastIcon />
          <span className={styles.title}>
            {tRef.current("apps.desktop.accessResolvedTitle")}
          </span>
        </span>
      ),
      description: (
        <span className={styles.body}>
          {tRef.current(
            approved
              ? "apps.desktop.accessApproved"
              : "apps.desktop.accessRejected",
            { name },
          )}
        </span>
      ),
      duration: 8,
      closable: true,
      onClick: () => {
        notificationRef.current.destroy(noticeKey);
        const path =
          frame.deep_link ||
          (frame.app_id ? `/apps/${encodeURIComponent(frame.app_id)}` : "");
        if (path) navigateRef.current(path);
      },
    });
  }

  function showApprovalToast(frame: ApprovalRequiredFrame) {
    const agent = agentsRef.current.find((a) => a.agent_id === frame.agent_id);
    const accent = agent?.color?.trim() || "#6366f1";
    const title = agent?.name
      ? tRef.current("teams.approvalToast.title", { name: agent.name })
      : tRef.current("teams.approvalToast.titleFallback");
    const noticeKey = `approval-${frame.agent_id}-${Date.now()}`;
    notificationRef.current.open({
      key: noticeKey,
      placement: "bottomRight",
      className: styles.notice,
      style: {
        ["--push-accent" as string]: accent,
      },
      message: (
        <span className={styles.header}>
          <PushToastIcon
            iconUrl={agent?.icon_url}
            iconName={agent?.icon_name}
          />
          <span className={styles.title}>{title}</span>
        </span>
      ),
      description: (
        <span className={styles.body}>
          {tRef.current("teams.approvalToast.description")}
        </span>
      ),
      duration: null,
      closable: true,
      onClick: () => {
        notificationRef.current.destroy(noticeKey);
        navigateRef.current(
          frame.deep_link || `/chat/${frame.agent_id}/${frame.thread_id ?? ""}`,
        );
      },
    });
  }
}
