import { Button, Card, Descriptions, Space, Typography } from "antd";
import type { TFunction } from "i18next";
import { Copy, ShieldAlert, UserPlus, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { appsApi } from "../../../api/modules/apps";
import { request } from "../../../api/request";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "../../../utils/antdMessage";
import type { ToolRenderProps } from "../types";

const { Text } = Typography;

interface PermissionCardData {
  code?: string;
  message?: string;
  app_id?: string;
  agent_id?: string;
  agent_owner?: string;
  app_owner?: string;
  agent_shared?: boolean;
  acting_user?: string;
  needed?: string;
  granted?: string | null;
}

const PERMISSION_KEYS: Record<string, { key: string; fallback: string }> = {
  view: { key: "apps.permissionCard.view", fallback: "View" },
  edit_data: { key: "apps.permissionCard.editData", fallback: "Add rows" },
  edit_structure: {
    key: "apps.permissionCard.editStructure",
    fallback: "Edit structure",
  },
};

function permissionText(t: TFunction, needed: string | undefined): string {
  const entry = needed ? PERMISSION_KEYS[needed] : undefined;
  return entry ? String(t(entry.key, entry.fallback)) : needed || "—";
}

function isPermissionData(value: unknown): value is PermissionCardData {
  return !!value && typeof value === "object";
}

/** Rendered when an `app_*` tool is blocked by the agent or MiniApp ACL. */
export function AppPermissionCard({ data }: ToolRenderProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const info = useMemo(() => (isPermissionData(data) ? data : {}), [data]);
  const [busy, setBusy] = useState(false);
  const [shared, setShared] = useState(false);
  const [requestStatus, setRequestStatus] = useState<
    "pending" | "approved" | "rejected" | null
  >(null);

  const isAgentOwner = Boolean(
    info.acting_user &&
      info.agent_owner &&
      info.acting_user === info.agent_owner,
  );
  const isAppOwner = Boolean(
    info.app_id && info.acting_user && info.acting_user === info.app_owner,
  );
  const canRequest = Boolean(info.agent_id) && !isAgentOwner && !isAppOwner;

  // Restore the status of an earlier request so the card does not offer a
  // duplicate while the owner is still deciding.
  useEffect(() => {
    if (!canRequest || !info.agent_id) return;
    let alive = true;
    appsApi
      .accessRequests({ scope: "mine" })
      .then((data) => {
        if (!alive) return;
        const match = data.requests.find(
          (request) =>
            request.agent_id === info.agent_id &&
            (info.app_id
              ? request.app_id === info.app_id
              : request.app_id === null),
        );
        setRequestStatus(match?.status ?? null);
      })
      .catch(() => {
        // Status is advisory; a failed probe keeps the request button.
      });
    return () => {
      alive = false;
    };
  }, [canRequest, info.agent_id, info.app_id]);

  const copyDetails = useCallback(() => {
    void navigator.clipboard?.writeText(JSON.stringify(info, null, 2));
    message.success(
      t("apps.permissionCard.copied", "Permission details copied"),
    );
  }, [info, t]);

  const shareExpert = useCallback(async () => {
    if (!info.agent_id) return;
    setBusy(true);
    try {
      await request(`/agents/${encodeURIComponent(info.agent_id)}`, {
        method: "PATCH",
        body: JSON.stringify({ is_shared: true }),
      });
      setShared(true);
      message.success(t("apps.permissionCard.sharedDone", "Expert shared"));
    } catch (err) {
      message.error(
        apiErrorMessage(
          err,
          t("apps.permissionCard.shareFailed", "Could not share the expert"),
          t,
        ),
      );
    } finally {
      setBusy(false);
    }
  }, [info.agent_id, t]);

  const manageCollaborators = useCallback(() => {
    if (!info.app_id) return;
    navigate(`/apps/${encodeURIComponent(info.app_id)}?share=1`);
  }, [info.app_id, navigate]);

  const requestAccess = useCallback(async () => {
    if (!info.agent_id) return;
    setBusy(true);
    try {
      await appsApi.createAccessRequest({
        agent_id: info.agent_id,
        app_id: info.app_id || null,
        needed:
          info.needed === "view" || info.needed === "edit_structure"
            ? info.needed
            : "edit_data",
      });
      setRequestStatus("pending");
      message.success(
        t(
          "apps.permissionCard.requestPending",
          "Request submitted; waiting for the owner",
        ),
      );
    } catch (err) {
      message.error(
        apiErrorMessage(
          err,
          t(
            "apps.permissionCard.requestFailed",
            "Could not submit the request",
          ),
          t,
        ),
      );
    } finally {
      setBusy(false);
    }
  }, [info.agent_id, info.app_id, info.needed, t]);

  const agentShared = Boolean(info.agent_shared) || shared;
  const showShare = isAgentOwner && !agentShared;
  const showCollaborators = isAppOwner;
  const showRequest =
    canRequest && requestStatus !== "pending" && requestStatus !== "approved";

  return (
    <Card
      size="small"
      data-testid="app-permission-card"
      style={{
        width: 340,
        maxWidth: "100%",
        borderColor: "#f59e0b",
        background: "#fffbeb",
      }}
      styles={{ body: { padding: 12 } }}
    >
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Space size={6} align="center">
          <ShieldAlert size={15} style={{ color: "#b45309" }} />
          <Text strong>
            {t("apps.permissionCard.title", "Permission required")}
          </Text>
        </Space>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {info.message ||
            t(
              "apps.permissionCard.description",
              "This action was not executed because you do not have access to the expert or MiniApp.",
            )}
        </Text>
        <Descriptions
          column={1}
          size="small"
          colon={false}
          styles={{
            label: { width: 92, color: "#92400e" },
            content: { color: "#78350f" },
          }}
        >
          <Descriptions.Item
            label={t("apps.permissionCard.actingUser", "Acting user")}
          >
            {info.acting_user || "—"}
          </Descriptions.Item>
          <Descriptions.Item
            label={t("apps.permissionCard.agentOwner", "Expert owner")}
          >
            {info.agent_owner || "—"}
            {agentShared
              ? ` (${t("apps.permissionCard.shared", "shared")})`
              : ""}
          </Descriptions.Item>
          {info.app_id ? (
            <Descriptions.Item
              label={t("apps.permissionCard.appOwner", "MiniApp owner")}
            >
              {info.app_owner || "—"}
            </Descriptions.Item>
          ) : null}
          <Descriptions.Item
            label={t("apps.permissionCard.needed", "Required access")}
          >
            {permissionText(t, info.needed)}
          </Descriptions.Item>
        </Descriptions>
        <Text type="secondary" style={{ fontSize: 11 }}>
          {t(
            "apps.permissionCard.hint",
            "Ask the owner to share the expert and add you as a MiniApp collaborator, or continue with the owner account.",
          )}
        </Text>
        {requestStatus && (
          <Text
            type={requestStatus === "rejected" ? "danger" : "success"}
            style={{ fontSize: 12 }}
          >
            {t(
              requestStatus === "pending"
                ? "apps.permissionCard.requestPending"
                : requestStatus === "approved"
                ? "apps.permissionCard.requestApproved"
                : "apps.permissionCard.requestRejected",
              requestStatus === "pending"
                ? "Request submitted; waiting for the owner"
                : requestStatus === "approved"
                ? "Access approved; retry the action"
                : "Access request rejected",
            )}
          </Text>
        )}
        <Space size={6} wrap>
          {showShare && (
            <Button
              size="small"
              type="primary"
              loading={busy}
              icon={<UserPlus size={13} />}
              onClick={() => void shareExpert()}
            >
              {t("apps.permissionCard.shareExpert", "Share expert")}
            </Button>
          )}
          {showCollaborators && (
            <Button
              size="small"
              type="primary"
              icon={<Users size={13} />}
              onClick={manageCollaborators}
            >
              {t(
                "apps.permissionCard.manageCollaborators",
                "Manage collaborators",
              )}
            </Button>
          )}
          {showRequest && (
            <Button
              size="small"
              loading={busy}
              onClick={() => void requestAccess()}
            >
              {t("apps.permissionCard.requestAccess", "Request access")}
            </Button>
          )}
          <Button size="small" icon={<Copy size={13} />} onClick={copyDetails}>
            {t("apps.permissionCard.copy", "Copy details")}
          </Button>
        </Space>
      </Space>
    </Card>
  );
}
