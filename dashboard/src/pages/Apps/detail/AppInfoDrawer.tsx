import {
  Button,
  Descriptions,
  Drawer,
  Popconfirm,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  Image as ImageIcon,
  Link2,
  RefreshCw,
  Table2,
  Trash2,
  Upload as UploadIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import type { AppDetail, AppFile, AppSource } from "../../../api/modules/apps";
import { fileTreeIcon } from "../../../utils/fileTreeIcon";
import { formatBytes } from "../../../utils/formatBytes";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { useIsMobile } from "../../../hooks/useIsMobile";
import CapabilitiesSection from "./CapabilitiesSection";
import IngressSection from "./IngressSection";
import ServicesSection from "./ServicesSection";
import WebhooksSection from "./WebhooksSection";
import { InfoSection } from "./shared";

const { Text } = Typography;

/** Assets keep no file extension, so resolve their icon by folder. */
function fileIcon(file: AppFile) {
  if (file.name.startsWith("assets/")) {
    return <ImageIcon size={14} color="#8b5cf6" aria-hidden />;
  }
  return fileTreeIcon(file.path, 14);
}

function sourceScheduleLabel(seconds: number, t: TFunction): string {
  if (seconds % 86400 === 0)
    return t("apps.scheduleDays", { count: seconds / 86400 });
  if (seconds % 3600 === 0)
    return t("apps.scheduleHours", { count: seconds / 3600 });
  return t("apps.scheduleMinutes", {
    count: Math.max(1, Math.round(seconds / 60)),
  });
}

interface Props {
  open: boolean;
  onClose: () => void;
  detail: AppDetail;
  permissionText: string;
  sources: AppSource[];
  files: AppFile[];
  filesLoading: boolean;
  canManage: boolean;
  canEditData: boolean;
  canEditStructure: boolean;
  capabilityDraft: string[];
  savingCapabilities: boolean;
  onCapabilityDraftChange: (next: string[]) => void;
  onSaveCapabilities: () => void;
  onSetTableVisibility: (tableKey: string, checked: boolean) => void;
  onOpenImport: (tableKey: string) => void;
  onOpenSourceModal: (tableKey: string) => void;
  onRunSource: (source: AppSource) => void;
  onRemoveSource: (source: AppSource) => void;
}

export default function AppInfoDrawer({
  open,
  onClose,
  detail,
  permissionText,
  sources,
  files,
  filesLoading,
  canManage,
  canEditData,
  canEditStructure,
  capabilityDraft,
  savingCapabilities,
  onCapabilityDraftChange,
  onSaveCapabilities,
  onSetTableVisibility,
  onOpenImport,
  onOpenSourceModal,
  onRunSource,
  onRemoveSource,
}: Props) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const isMobile = useIsMobile();

  return (
    <Drawer
      title={t("apps.infoTitle")}
      open={open}
      onClose={onClose}
      width={isMobile ? "100%" : 560}
      styles={{ body: { padding: "20px 24px 28px" } }}
    >
      <Descriptions
        column={1}
        size="small"
        bordered
        labelStyle={{ width: 120, whiteSpace: "nowrap" }}
      >
        <Descriptions.Item label={t("apps.createdByLabel")}>
          {detail.owner_username || "—"}
        </Descriptions.Item>
        <Descriptions.Item label={t("apps.agentLabel")}>
          {detail.agent_name || detail.agent_id}
        </Descriptions.Item>
        <Descriptions.Item label={t("apps.permissionLabel")}>
          {permissionText}
        </Descriptions.Item>
        <Descriptions.Item label={t("apps.rowsLabel")}>
          {t("apps.rows", { count: detail.row_count })}
        </Descriptions.Item>
        <Descriptions.Item label={t("apps.createdAt")}>
          {formatServerDateTime(detail.created_at, timeZone)}
        </Descriptions.Item>
        <Descriptions.Item label={t("apps.updatedAt")}>
          {formatServerDateTime(detail.updated_at, timeZone)}
        </Descriptions.Item>
        <Descriptions.Item label={t("apps.appId")}>
          <Text code copyable={{ text: detail.app_id }}>
            {detail.app_id}
          </Text>
        </Descriptions.Item>
      </Descriptions>

      <CapabilitiesSection
        canManage={canManage}
        draft={capabilityDraft}
        saving={savingCapabilities}
        onChange={onCapabilityDraftChange}
        onSave={onSaveCapabilities}
      />

      <ServicesSection
        appId={detail.app_id}
        open={open}
        canManage={canManage}
      />

      <IngressSection appId={detail.app_id} open={open} canManage={canManage} />

      <WebhooksSection
        appId={detail.app_id}
        open={open}
        canManage={canManage}
      />

      <InfoSection title={t("apps.tablesTitle")} hint={t("apps.tablesHint")}>
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          {detail.tables.map((table) => {
            const source = sources.find(
              (item) => item.table_key === table.table_key,
            );
            return (
              <div
                key={table.table_key}
                style={{
                  padding: "10px 12px",
                  border: "1px solid rgba(5, 5, 5, 0.06)",
                  borderRadius: 8,
                  background: "#fafafa",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    minWidth: 0,
                  }}
                >
                  <Table2 size={14} style={{ flexShrink: 0 }} />
                  <Text
                    strong
                    ellipsis
                    style={{ fontSize: 13, minWidth: 0, flex: "1 1 auto" }}
                  >
                    {table.label || table.table_key}
                  </Text>
                  {table.is_main && (
                    <Tag style={{ marginInlineEnd: 0, flexShrink: 0 }}>
                      {t("apps.mainTable")}
                    </Tag>
                  )}
                  {table.row_scope === "user" && (
                    <Tag style={{ marginInlineEnd: 0, flexShrink: 0 }}>
                      {t("apps.userTableTag", "Per-user")}
                    </Tag>
                  )}
                  <Text
                    type="secondary"
                    style={{
                      fontSize: 12,
                      whiteSpace: "nowrap",
                      flexShrink: 0,
                    }}
                  >
                    {t("apps.tableRows", { count: table.row_count })}
                  </Text>
                </div>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: 8,
                    marginTop: 8,
                  }}
                >
                  {canManage && (
                    <Tooltip
                      title={
                        table.row_scope === "user"
                          ? t(
                              "apps.userTableNotPublicHint",
                              "Per-user tables are never shown on the public link.",
                            )
                          : t("apps.tableVisibilityHint")
                      }
                    >
                      <Space size={4}>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {t("apps.publicTable")}
                        </Text>
                        <Switch
                          size="small"
                          checked={
                            table.public_visible && table.row_scope !== "user"
                          }
                          disabled={table.row_scope === "user"}
                          onChange={(checked) =>
                            onSetTableVisibility(table.table_key, checked)
                          }
                        />
                      </Space>
                    </Tooltip>
                  )}
                  <span style={{ flex: 1 }} />
                  {canEditData && (
                    <Button
                      type="text"
                      size="small"
                      icon={<UploadIcon size={13} />}
                      onClick={() => onOpenImport(table.table_key)}
                    >
                      {t("apps.importButton")}
                    </Button>
                  )}
                  {canEditStructure && (
                    <Button
                      type="text"
                      size="small"
                      icon={<Link2 size={13} />}
                      onClick={() => onOpenSourceModal(table.table_key)}
                    >
                      {t("apps.sourceButton")}
                    </Button>
                  )}
                </div>
                {source && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: 8,
                      marginTop: 8,
                      paddingTop: 8,
                      borderTop: "1px dashed rgba(5, 5, 5, 0.08)",
                    }}
                  >
                    <Tag
                      color={
                        source.last_status === "error"
                          ? "red"
                          : source.last_status === "ok"
                          ? "green"
                          : "default"
                      }
                      style={{
                        marginInlineEnd: 0,
                        fontSize: 11,
                        lineHeight: "18px",
                      }}
                    >
                      {source.last_status === "error"
                        ? t("apps.sourceStatusError")
                        : source.last_status === "ok"
                        ? t("apps.sourceStatusOk")
                        : t("apps.sourceStatusPending")}
                    </Tag>
                    <Text
                      type="secondary"
                      style={{ fontSize: 12, flex: "1 1 200px", minWidth: 0 }}
                      ellipsis
                    >
                      {source.kind === "workspace_file"
                        ? String(source.params.path || "")
                        : String(source.params.tool || "")}
                      {source.schedule_seconds > 0 &&
                        ` · ${t("apps.sourceEvery", {
                          text: sourceScheduleLabel(source.schedule_seconds, t),
                        })}`}
                      {source.last_run_at &&
                        ` · ${t("apps.sourceLastSync")} ${formatServerDateTime(
                          source.last_run_at,
                          timeZone,
                        )}`}
                      {source.last_status === "ok" &&
                        source.last_row_count !== null &&
                        ` · ${t("apps.tableRows", {
                          count: source.last_row_count,
                        })}`}
                    </Text>
                    {canEditData && (
                      <Button
                        type="text"
                        size="small"
                        icon={<RefreshCw size={13} />}
                        onClick={() => onRunSource(source)}
                      >
                        {t("apps.sourceRun")}
                      </Button>
                    )}
                    {canEditStructure && (
                      <Popconfirm
                        title={t("apps.sourceDeleteTitle")}
                        description={t("apps.sourceDeleteConfirm")}
                        okButtonProps={{ danger: true }}
                        okText={t("apps.delete")}
                        cancelText={t("common.cancel")}
                        onConfirm={() => onRemoveSource(source)}
                      >
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<Trash2 size={13} />}
                        />
                      </Popconfirm>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </Space>
      </InfoSection>

      <InfoSection title={t("apps.filesTitle")} hint={t("apps.filesHint")}>
        {filesLoading ? (
          <div style={{ padding: 24, textAlign: "center" }}>
            <Spin />
          </div>
        ) : (
          <>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("apps.filesCount", { count: files.length })}
            </Text>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: isMobile
                  ? "1fr"
                  : "repeat(2, minmax(0, 1fr))",
                gap: 8,
                marginTop: 8,
                maxHeight: 300,
                overflowY: "auto",
              }}
            >
              {files.map((file) => (
                <div
                  key={file.name}
                  title={file.path}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    minWidth: 0,
                    padding: "7px 10px",
                    border: "1px solid rgba(5, 5, 5, 0.06)",
                    borderRadius: 8,
                    background: "#fafafa",
                  }}
                >
                  <span
                    style={{
                      display: "inline-flex",
                      flexShrink: 0,
                    }}
                  >
                    {fileIcon(file)}
                  </span>
                  <Text
                    ellipsis
                    style={{ fontSize: 13, minWidth: 0, flex: "1 1 auto" }}
                  >
                    {file.name}
                  </Text>
                  <Text
                    type="secondary"
                    style={{
                      fontSize: 12,
                      whiteSpace: "nowrap",
                      flexShrink: 0,
                    }}
                  >
                    {formatBytes(file.size)}
                  </Text>
                </div>
              ))}
            </div>
          </>
        )}
      </InfoSection>
    </Drawer>
  );
}
