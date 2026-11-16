import {
  Alert,
  Button,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Typography,
} from "antd";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import type {
  AppPublication,
  AppSchedule,
  AppTable,
} from "../../../api/modules/apps";
import { message } from "../../../utils/antdMessage";
import { formatServerDateTime } from "../../../utils/formatMessageTime";

const { Text } = Typography;

function staleReasonLabel(reason: string, t: TFunction): string {
  switch (reason) {
    case "html":
      return t("apps.publishStaleHtml", "HTML changed");
    case "schema":
    case "visibility":
      return t("apps.publishStaleSchema", "Table structure changed");
    case "not_published":
      return t(
        "apps.publishNotFixed",
        "This link has not fixed a version yet; publish to freeze the current one.",
      );
    case "workspace":
      return t("apps.publishStaleWorkspace", "Workspace unavailable");
    default:
      return reason;
  }
}

interface Props {
  open: boolean;
  tables: AppTable[];
  publication: AppPublication | null;
  publishUrl: string;
  busy: boolean;
  timeZone: string;
  snapshotSchedule?: AppSchedule | null;
  scheduleBusy?: boolean;
  onToggleSnapshotSchedule?: (enabled: boolean) => void;
  onSnapshotIntervalChange?: (seconds: number) => void;
  onClose: () => void;
  onCreate: () => void;
  onPublishNow: () => void;
  onRevoke: () => void;
}

export default function AppPublishModal({
  open,
  tables,
  publication,
  publishUrl,
  busy,
  timeZone,
  snapshotSchedule,
  scheduleBusy = false,
  onToggleSnapshotSchedule,
  onSnapshotIntervalChange,
  onClose,
  onCreate,
  onPublishNow,
  onRevoke,
}: Props) {
  const { t } = useTranslation();
  const publicTables = tables.filter(
    (table) => table.public_visible && table.row_scope !== "user",
  );
  const publicRows = publicTables.reduce(
    (total, table) => total + table.row_count,
    0,
  );
  const publicPiiColumns = publicTables.reduce(
    (total, table) =>
      total + table.columns.filter((column) => column.pii).length,
    0,
  );

  return (
    <Modal
      title={t("apps.publishTitle")}
      open={open}
      onCancel={onClose}
      footer={null}
      destroyOnHidden
    >
      {busy ? (
        <div style={{ padding: 24, textAlign: "center" }}>
          <Spin />
        </div>
      ) : (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Text type="secondary">{t("apps.publishHint")}</Text>
          <Alert
            type={publicTables.length > 0 ? "info" : "warning"}
            showIcon
            message={
              publicTables.length > 0
                ? t("apps.publishPublicPreview", {
                    tables: publicTables.length,
                    rows: publicRows,
                  })
                : t("apps.publishNoPublicTables")
            }
            description={
              <Space direction="vertical" size={2}>
                {publicTables.length > 0 && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {publicTables
                      .map(
                        (table) =>
                          `${table.label || table.table_key} (${
                            table.row_count
                          })`,
                      )
                      .join(" · ")}
                  </Text>
                )}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t("apps.publishLiveDataHint")}
                </Text>
              </Space>
            }
          />
          {publicPiiColumns > 0 && (
            <Alert
              type="warning"
              showIcon
              message={t("apps.piiShareWarning")}
            />
          )}
          <Text>
            {publication?.active
              ? t("apps.publishActive")
              : t("apps.publishInactive")}
          </Text>
          {publication?.active && (
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              {publication.published_at ? (
                <Text type="secondary">
                  {t("apps.publishedAt", "Published {{time}}", {
                    time: formatServerDateTime(
                      publication.published_at,
                      timeZone,
                    ),
                  })}
                </Text>
              ) : (
                <Alert
                  type="info"
                  showIcon
                  message={t(
                    "apps.publishNotFixed",
                    "This link has not fixed a version yet; publish to freeze the current one.",
                  )}
                />
              )}
              {publication.snapshot_stale && (
                <Alert
                  type="warning"
                  showIcon
                  message={t(
                    "apps.publishStale",
                    "The MiniApp changed since the last publish",
                  )}
                  description={(publication.stale_reasons ?? [])
                    .map((reason) => staleReasonLabel(reason, t))
                    .join(" · ")}
                />
              )}
              <Space direction="vertical" size={4}>
                <Space size={8}>
                  <Switch
                    size="small"
                    checked={Boolean(snapshotSchedule?.enabled)}
                    loading={scheduleBusy}
                    onChange={(checked) => onToggleSnapshotSchedule?.(checked)}
                  />
                  <Text>{t("apps.publishAutoRefresh")}</Text>
                  {snapshotSchedule?.enabled && (
                    <Select
                      size="small"
                      value={snapshotSchedule.interval_seconds}
                      onChange={(value: number) =>
                        onSnapshotIntervalChange?.(value)
                      }
                      options={[
                        {
                          value: 3600,
                          label: t("apps.publishAutoRefreshHourly"),
                        },
                        {
                          value: 21600,
                          label: t("apps.publishAutoRefresh6h"),
                        },
                        {
                          value: 86400,
                          label: t("apps.publishAutoRefreshDaily"),
                        },
                      ]}
                    />
                  )}
                </Space>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t("apps.publishAutoRefreshHint")}
                </Text>
                {snapshotSchedule?.enabled &&
                  snapshotSchedule.next_run_at > 0 && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t("apps.publishAutoRefreshNext", {
                        time: formatServerDateTime(
                          snapshotSchedule.next_run_at,
                          timeZone,
                        ),
                      })}
                    </Text>
                  )}
                {snapshotSchedule?.last_error && (
                  <Text type="danger" style={{ fontSize: 12 }}>
                    {t("apps.publishAutoRefreshError", {
                      error: snapshotSchedule.last_error,
                    })}
                  </Text>
                )}
              </Space>
            </Space>
          )}
          {publishUrl && (
            <Input
              readOnly
              value={publishUrl}
              addonAfter={
                <Button
                  type="link"
                  size="small"
                  onClick={() => {
                    void navigator.clipboard?.writeText(publishUrl);
                    message.success(t("apps.publishCopy"));
                  }}
                >
                  {t("apps.publishCopy")}
                </Button>
              }
            />
          )}
          <Space>
            {publication?.active && (
              <Button
                type={publication.snapshot_stale ? "primary" : "default"}
                onClick={onPublishNow}
                loading={busy}
              >
                {t("apps.publishNow", "Publish updates")}
              </Button>
            )}
            <Button
              type={publication?.active ? "default" : "primary"}
              onClick={onCreate}
              loading={busy}
            >
              {publication?.active
                ? t("apps.publishRotate")
                : t("apps.publishCreate")}
            </Button>
            {publication?.active && (
              <Popconfirm
                title={t("apps.publishRevokeConfirm")}
                okText={t("apps.publishRevoke")}
                cancelText={t("common.cancel")}
                okButtonProps={{ danger: true }}
                onConfirm={onRevoke}
              >
                <Button danger>{t("apps.publishRevoke")}</Button>
              </Popconfirm>
            )}
          </Space>
        </Space>
      )}
    </Modal>
  );
}
