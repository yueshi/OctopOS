import {
  Alert,
  Button,
  Drawer,
  Empty,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import {
  appsApi,
  type AppColumn,
  type AppRowHistoryEntry,
} from "../../../api/modules/apps";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { apiErrorMessage } from "../../../utils/apiError";
import { formatServerDateTime } from "../../../utils/formatMessageTime";

const { Text } = Typography;

const PAGE_SIZE = 50;

interface DiffRow {
  field: string;
  label: string;
  before: string;
  after: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  appId: string;
  rowId: string;
  columns: AppColumn[];
  /** Owners and editors see the stored values; viewers see PII masks. */
  canSeePii: boolean;
}

function actionColor(action: string): string {
  switch (action) {
    case "delete":
      return "red";
    case "replace":
      return "orange";
    default:
      return "blue";
  }
}

function actionLabel(action: string, t: TFunction): string {
  switch (action) {
    case "update":
      return t("apps.historyActionUpdate");
    case "delete":
      return t("apps.historyActionDelete");
    case "replace":
      return t("apps.historyActionReplace");
    default:
      return action;
  }
}

function actorLabel(actor: string, t: TFunction): string {
  if (actor.startsWith("user:")) {
    return t("apps.historyActorUser", { id: actor.slice("user:".length) });
  }
  if (actor === "_system") return t("apps.historyActorSystem");
  return actor;
}

/** Only the fields whose value differs between the before/after snapshots. */
function diffRows(entry: AppRowHistoryEntry, columns: AppColumn[]): DiffRow[] {
  const before = entry.before ?? {};
  const after = entry.after ?? {};
  const labels = new Map(
    columns.map((column) => [column.name, column.label || column.name]),
  );
  return [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter((name) => before[name] !== after[name])
    .map((name) => ({
      field: name,
      label: labels.get(name) ?? name,
      before: before[name] ?? "—",
      after: after[name] ?? "—",
    }));
}

export default function RowHistoryDrawer({
  open,
  onClose,
  appId,
  rowId,
  columns,
  canSeePii,
}: Props) {
  const { t } = useTranslation();
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);
  const isMobile = useIsMobile();
  const timeZone = useServerTimezone();
  const [entries, setEntries] = useState<AppRowHistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const requestRef = useRef(0);

  const loadFirst = useCallback(async () => {
    if (!appId || !rowId) return;
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    setEntries([]);
    setHasMore(false);
    try {
      const result = await appsApi.rowHistory(appId, rowId, PAGE_SIZE, 0);
      if (requestRef.current !== requestId) return;
      setEntries(result.entries);
      setHasMore(result.entries.length === PAGE_SIZE);
    } catch (err) {
      if (requestRef.current !== requestId) return;
      setError(
        apiErrorMessage(
          err,
          tRef.current("apps.historyLoadFailed"),
          tRef.current,
        ),
      );
    } finally {
      if (requestRef.current === requestId) setLoading(false);
    }
  }, [appId, rowId]);

  useEffect(() => {
    if (open && rowId) void loadFirst();
  }, [open, rowId, loadFirst]);

  const loadMore = async () => {
    if (!appId || !rowId || loadingMore) return;
    setLoadingMore(true);
    try {
      const result = await appsApi.rowHistory(
        appId,
        rowId,
        PAGE_SIZE,
        entries.length,
      );
      setEntries((previous) => [...previous, ...result.entries]);
      setHasMore(result.entries.length === PAGE_SIZE);
    } catch (err) {
      setError(
        apiErrorMessage(
          err,
          tRef.current("apps.historyLoadFailed"),
          tRef.current,
        ),
      );
    } finally {
      setLoadingMore(false);
    }
  };

  const diffColumns = useMemo<ColumnsType<DiffRow>>(
    () => [
      {
        title: t("apps.historyField"),
        dataIndex: "label",
        width: 140,
        ellipsis: true,
      },
      {
        title: t("apps.historyBefore"),
        dataIndex: "before",
        render: (value: string) => (
          <Text style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            {value}
          </Text>
        ),
      },
      {
        title: t("apps.historyAfter"),
        dataIndex: "after",
        render: (value: string) => (
          <Text style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            {value}
          </Text>
        ),
      },
    ],
    [t],
  );

  const masked = !canSeePii && columns.some((column) => column.pii);

  return (
    <Drawer
      title={t("apps.historyTitle")}
      open={open}
      onClose={onClose}
      width={isMobile ? "100%" : 680}
      styles={{ body: { padding: "20px 24px 28px" } }}
      destroyOnHidden
    >
      {masked && (
        <Alert
          type="warning"
          showIcon
          message={t("apps.historyPiiMasked")}
          style={{ marginBottom: 12 }}
        />
      )}
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button size="small" onClick={() => void loadFirst()}>
              {t("common.retry")}
            </Button>
          }
          style={{ marginBottom: 12 }}
        />
      )}
      {loading ? (
        <div style={{ padding: 24, textAlign: "center" }}>
          <Spin />
        </div>
      ) : entries.length === 0 ? (
        <Empty description={t("apps.historyEmpty")} />
      ) : (
        <>
          <Timeline
            items={entries.map((entry) => {
              const rows = diffRows(entry, columns);
              return {
                color: actionColor(entry.action),
                children: (
                  <div>
                    <Space size={6} wrap>
                      <Tag color={actionColor(entry.action)}>
                        {actionLabel(entry.action, t)}
                      </Tag>
                      <Tag>
                        {t("apps.historyVersion", { version: entry.version })}
                      </Tag>
                      <Text type="secondary">{actorLabel(entry.actor, t)}</Text>
                      {entry.source ? (
                        <Text type="secondary">· {entry.source}</Text>
                      ) : null}
                      <Text type="secondary">
                        · {formatServerDateTime(entry.changed_at, timeZone)}
                      </Text>
                    </Space>
                    {rows.length > 0 ? (
                      <Table<DiffRow>
                        size="small"
                        rowKey="field"
                        pagination={false}
                        columns={diffColumns}
                        dataSource={rows}
                        style={{ marginTop: 8 }}
                      />
                    ) : (
                      <Text
                        type="secondary"
                        style={{ display: "block", marginTop: 6 }}
                      >
                        {t("apps.historyNoFieldChanges")}
                      </Text>
                    )}
                  </div>
                ),
              };
            })}
          />
          {hasMore && (
            <Button block loading={loadingMore} onClick={() => void loadMore()}>
              {t("apps.historyLoadMore")}
            </Button>
          )}
        </>
      )}
    </Drawer>
  );
}
