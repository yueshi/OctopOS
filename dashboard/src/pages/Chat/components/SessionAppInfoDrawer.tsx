import { useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Input,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { ExternalLink, Table2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import {
  appsApi,
  type AppDetail,
  type AppTable,
} from "../../../api/modules/apps";
import CapabilityPicker from "../../../components/MiniAppFrame/CapabilityPicker";
import { permissionLabel } from "../../../components/MiniAppFrame/permissionLabel";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "../../../utils/antdMessage";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { fullscreenContainer } from "../../../utils/overlayContainer";

const { Text } = Typography;

export interface SessionAppInfoDrawerProps {
  open: boolean;
  appId: string | null;
  onClose: () => void;
  /** Fired after a successful save so the host can refresh its snapshot. */
  onUpdated?: (detail: AppDetail) => void;
}

function InfoSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section>
      <Text strong style={{ fontSize: 13 }}>
        {title}
      </Text>
      {hint ? (
        <Text
          type="secondary"
          style={{
            display: "block",
            marginTop: 2,
            fontSize: 11,
            lineHeight: 1.5,
          }}
        >
          {hint}
        </Text>
      ) : null}
      <div style={{ marginTop: 8 }}>{children}</div>
    </section>
  );
}

/**
 * Quick view / adjust surface for a MiniApp opened from the chat dock.
 *
 * Admin actions stay owner-gated, matching the app detail page: name and
 * description need `edit_structure`, while capabilities and public table
 * visibility are owner-only. Full management lives on the detail page.
 */
export default function SessionAppInfoDrawer({
  open,
  appId,
  onClose,
  onUpdated,
}: SessionAppInfoDrawerProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const timeZone = useServerTimezone();
  const isMobile = useIsMobile();
  const currentUser = useCurrentUser();

  const [detail, setDetail] = useState<AppDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [savingMeta, setSavingMeta] = useState(false);
  const [capabilityDraft, setCapabilityDraft] = useState<string[]>([]);
  const [savingCapabilities, setSavingCapabilities] = useState(false);
  const [busyTable, setBusyTable] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !appId) return;
    let alive = true;
    setLoading(true);
    setLoadError(null);
    setDetail(null);
    appsApi
      .get(appId)
      .then((data) => {
        if (!alive) return;
        setDetail(data);
        setName(data.name);
        setDescription(data.description);
        setCapabilityDraft(data.capabilities ?? []);
      })
      .catch((err) => {
        if (alive) setLoadError(err);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, appId]);

  const isOwner =
    currentUser && detail
      ? detail.owner_username === currentUser.username
      : undefined;
  const isAdmin = currentUser?.role === "admin";
  const canEditStructure = detail?.permission === "edit_structure";
  const canManage = canEditStructure && (isOwner === true || isAdmin === true);
  const loadErrorMessage = loadError
    ? apiErrorMessage(
        loadError,
        t("apps.loadFailed", "Failed to load MiniApps"),
        t,
      )
    : "";
  const metaDirty =
    !!detail &&
    (name.trim() !== detail.name || description.trim() !== detail.description);

  const saveMeta = async () => {
    if (!detail) return;
    if (!name.trim()) {
      message.warning(t("apps.nameRequired", "Name is required"));
      return;
    }
    setSavingMeta(true);
    try {
      const updated = await appsApi.update(detail.app_id, {
        name: name.trim(),
        description: description.trim(),
      });
      setDetail(updated);
      setName(updated.name);
      setDescription(updated.description);
      setCapabilityDraft(updated.capabilities ?? []);
      onUpdated?.(updated);
      message.success(t("apps.saveSuccess", "Saved"));
    } catch (err) {
      message.error(
        apiErrorMessage(err, t("apps.saveFailed", "Failed to save"), t),
      );
    } finally {
      setSavingMeta(false);
    }
  };

  const saveCapabilities = async () => {
    if (!detail) return;
    setSavingCapabilities(true);
    try {
      const updated = await appsApi.setCapabilities(
        detail.app_id,
        capabilityDraft,
      );
      setDetail(updated);
      setCapabilityDraft(updated.capabilities ?? []);
      onUpdated?.(updated);
      message.success(t("apps.capabilities.saved", "Capabilities saved"));
    } catch (err) {
      message.error(
        apiErrorMessage(
          err,
          t("apps.capabilities.saveFailed", "Could not save capabilities"),
          t,
        ),
      );
    } finally {
      setSavingCapabilities(false);
    }
  };

  const setTablePublic = async (table: AppTable, checked: boolean) => {
    if (!detail) return;
    setBusyTable(table.table_key);
    try {
      const updated = await appsApi.setTableVisibility(
        detail.app_id,
        table.table_key,
        checked,
      );
      setDetail((prev) =>
        prev
          ? {
              ...prev,
              tables: prev.tables.map((item) =>
                item.table_key === updated.table_key ? updated : item,
              ),
            }
          : prev,
      );
    } catch (err) {
      message.error(
        apiErrorMessage(err, t("apps.saveFailed", "Failed to save"), t),
      );
    } finally {
      setBusyTable(null);
    }
  };

  const openDetailPage = () => {
    if (!detail) return;
    onClose();
    navigate(`/apps/${encodeURIComponent(detail.app_id)}`);
  };

  return (
    <Drawer
      title={t("apps.infoTitle", "App info")}
      open={open}
      onClose={onClose}
      getContainer={fullscreenContainer}
      width={isMobile ? "100%" : 440}
      styles={{
        header: { padding: "12px 16px" },
        body: { padding: "14px 18px 18px" },
      }}
      destroyOnHidden
      extra={
        <Button
          type="link"
          size="small"
          icon={<ExternalLink size={13} />}
          onClick={openDetailPage}
          disabled={!detail}
        >
          {t("apps.openDetailPage", "Open in app detail")}
        </Button>
      }
    >
      {loading ? (
        <div style={{ padding: 24, textAlign: "center" }}>
          <Spin />
        </div>
      ) : loadErrorMessage ? (
        <Alert type="error" showIcon message={loadErrorMessage} />
      ) : detail ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              {t("apps.name", "Name")}
            </Text>
            {canEditStructure ? (
              <div style={{ display: "flex", gap: 8 }}>
                <Input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={100}
                />
                <Button
                  type="primary"
                  loading={savingMeta}
                  disabled={!metaDirty}
                  onClick={() => void saveMeta()}
                >
                  {t("apps.save", "Save")}
                </Button>
              </div>
            ) : (
              <Text strong>{detail.name}</Text>
            )}
            <Text type="secondary" style={{ fontSize: 11, marginTop: 2 }}>
              {t("apps.description", "Description")}
            </Text>
            {canEditStructure ? (
              <Input.TextArea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                autoSize={{ minRows: 2, maxRows: 3 }}
                maxLength={2000}
              />
            ) : (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {detail.description || "—"}
              </Text>
            )}
          </div>

          <Descriptions
            size="small"
            column={2}
            colon={false}
            labelStyle={{ fontSize: 11, whiteSpace: "nowrap" }}
            contentStyle={{ fontSize: 12 }}
          >
            <Descriptions.Item label={t("apps.createdByLabel", "Created by")}>
              {detail.owner_username || "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("apps.agentLabel", "Agent")}>
              {detail.agent_name || detail.agent_id}
            </Descriptions.Item>
            <Descriptions.Item
              label={t("apps.permissionLabel", "My permission")}
            >
              {permissionLabel(detail.permission, t, isOwner)}
            </Descriptions.Item>
            <Descriptions.Item label={t("apps.rowsLabel", "Rows")}>
              {t("apps.rows", "{{count}} rows", {
                count: detail.row_count,
              })}
            </Descriptions.Item>
            <Descriptions.Item label={t("apps.createdAt", "Created")}>
              {formatServerDateTime(detail.created_at, timeZone)}
            </Descriptions.Item>
            <Descriptions.Item label={t("apps.updatedAt", "Updated")}>
              {formatServerDateTime(detail.updated_at, timeZone)}
            </Descriptions.Item>
            <Descriptions.Item label={t("apps.appId", "App ID")} span={2}>
              <Text
                code
                copyable={{ text: detail.app_id }}
                style={{ fontSize: 11 }}
              >
                {detail.app_id}
              </Text>
            </Descriptions.Item>
          </Descriptions>

          <InfoSection
            title={t("apps.capabilities.title", "Page capabilities")}
            hint={t(
              "apps.capabilities.hint",
              "Controls what the MiniApp page may ask the host to do; agent tools and this dashboard are not affected.",
            )}
          >
            <CapabilityPicker
              value={capabilityDraft}
              disabled={!canManage}
              saving={savingCapabilities}
              onChange={setCapabilityDraft}
              onSave={canManage ? () => void saveCapabilities() : undefined}
            />
          </InfoSection>

          <InfoSection title={t("apps.tablesTitle", "Tables")}>
            <Space direction="vertical" size={6} style={{ width: "100%" }}>
              {detail.tables.map((table) => (
                <div
                  key={table.table_key}
                  style={{
                    padding: "6px 8px",
                    border: "1px solid rgba(5, 5, 5, 0.06)",
                    borderRadius: 6,
                    background: "#fafafa",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      minWidth: 0,
                    }}
                  >
                    <Table2 size={13} style={{ flexShrink: 0 }} />
                    <Text
                      strong
                      ellipsis
                      style={{ fontSize: 12, minWidth: 0, flex: "1 1 auto" }}
                    >
                      {table.label || table.table_key}
                    </Text>
                    {table.is_main && (
                      <Tag
                        style={{
                          marginInlineEnd: 0,
                          flexShrink: 0,
                          fontSize: 11,
                          lineHeight: "16px",
                        }}
                      >
                        {t("apps.mainTable", "Main")}
                      </Tag>
                    )}
                    {table.row_scope === "user" && (
                      <Tag
                        style={{
                          marginInlineEnd: 0,
                          flexShrink: 0,
                          fontSize: 11,
                          lineHeight: "16px",
                        }}
                      >
                        {t("apps.userTableTag", "Per-user")}
                      </Tag>
                    )}
                    <Text
                      type="secondary"
                      style={{
                        fontSize: 11,
                        whiteSpace: "nowrap",
                        flexShrink: 0,
                      }}
                    >
                      {t("apps.tableRows", "{{count}} rows", {
                        count: table.row_count,
                      })}
                    </Text>
                    {canManage && (
                      <Tooltip
                        title={
                          table.row_scope === "user"
                            ? t(
                                "apps.userTableNotPublicHint",
                                "Per-user tables are never shown on the public link.",
                              )
                            : t(
                                "apps.tableVisibilityHint",
                                "When on, this table is visible on the public read-only link.",
                              )
                        }
                      >
                        <Switch
                          size="small"
                          aria-label={t("apps.publicTable", "Public link")}
                          checked={
                            table.public_visible && table.row_scope !== "user"
                          }
                          disabled={table.row_scope === "user"}
                          loading={busyTable === table.table_key}
                          onChange={(checked) =>
                            void setTablePublic(table, checked)
                          }
                        />
                      </Tooltip>
                    )}
                  </div>
                </div>
              ))}
            </Space>
          </InfoSection>
        </div>
      ) : null}
    </Drawer>
  );
}
