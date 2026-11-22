import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Popconfirm, Space, Table, Tag, Typography } from "antd";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { appsApi, type AppTask } from "../../../api/modules/apps";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { message } from "../../../utils/antdMessage";
import { apiErrorMessage } from "../../../utils/apiError";
import { formatServerDateTime } from "../../../utils/formatMessageTime";

const ACTIVE_STATUSES = new Set(["queued", "running"]);

const STATUS_COLOR: Record<string, string> = {
  queued: "default",
  running: "processing",
  succeeded: "success",
  failed: "error",
  cancelled: "warning",
};

const STATUS_LABEL_KEY: Record<string, string> = {
  queued: "apps.tasksStatusQueued",
  running: "apps.tasksStatusRunning",
  succeeded: "apps.tasksStatusSucceeded",
  failed: "apps.tasksStatusFailed",
  cancelled: "apps.tasksStatusCancelled",
};

interface Props {
  appId: string;
  canCancel: boolean;
  refreshToken?: number;
}

export default function AppTasksPanel({
  appId,
  canCancel,
  refreshToken = 0,
}: Props) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [tasks, setTasks] = useState<AppTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        setTasks(await appsApi.listTasks(appId));
        setFailed(false);
      } catch {
        setFailed(true);
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [appId],
  );

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  useEffect(() => {
    if (!tasks.some((task) => ACTIVE_STATUSES.has(task.status))) return;
    const timer = window.setInterval(() => void load(true), 3000);
    return () => window.clearInterval(timer);
  }, [tasks, load]);

  const cancel = async (taskId: string) => {
    try {
      await appsApi.cancelTask(appId, taskId);
      message.success(t("apps.tasksCancelled"));
      await load(true);
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.tasksCancelFailed"), t));
    }
  };

  const taskTypeLabel = (value: string) => {
    if (value === "summarize_row") return t("apps.taskTypeSummarize");
    if (value === "table_digest") return t("apps.taskTypeDigest");
    return value;
  };

  const resultText = (task: AppTask) => {
    if (task.error) return task.error;
    if (!task.result) return "—";
    const summary = task.result.summary;
    return typeof summary === "string" ? summary : JSON.stringify(task.result);
  };

  const columns = [
    {
      title: t("apps.tasksColumnType"),
      dataIndex: "task_type",
      width: 150,
      render: (value: string) => taskTypeLabel(value),
    },
    {
      title: t("apps.tasksColumnRow"),
      dataIndex: "row_id",
      width: 160,
      render: (value: string) => (
        <Typography.Text code ellipsis>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: t("apps.tasksColumnStatus"),
      dataIndex: "status",
      width: 110,
      render: (value: string) => (
        <Tag color={STATUS_COLOR[value] ?? "default"}>
          {t(STATUS_LABEL_KEY[value] ?? "apps.tasksColumnStatus")}
        </Tag>
      ),
    },
    {
      title: t("apps.tasksColumnResult"),
      dataIndex: "result",
      render: (_: unknown, task: AppTask) => (
        <Typography.Paragraph
          type={task.error ? "danger" : undefined}
          ellipsis={{ rows: 2 }}
          style={{ marginBottom: 0, maxWidth: 480 }}
        >
          {resultText(task)}
        </Typography.Paragraph>
      ),
    },
    {
      title: t("apps.tasksColumnCreated"),
      dataIndex: "created_at",
      width: 170,
      render: (value: number) => formatServerDateTime(value, timeZone),
    },
    {
      title: t("apps.tasksColumnActions"),
      dataIndex: "task_id",
      width: 100,
      render: (value: string, task: AppTask) => {
        const active = ACTIVE_STATUSES.has(task.status);
        return (
          <Popconfirm
            title={t("apps.tasksCancelConfirm")}
            onConfirm={() => void cancel(value)}
            disabled={!canCancel || !active}
          >
            <Button size="small" disabled={!canCancel || !active}>
              {t("apps.tasksCancel")}
            </Button>
          </Popconfirm>
        );
      },
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Space style={{ justifyContent: "space-between", width: "100%" }}>
        <Typography.Text type="secondary">
          {t("apps.tasksHint")}
        </Typography.Text>
        <Button
          size="small"
          icon={<RefreshCw size={14} />}
          loading={loading}
          onClick={() => void load()}
        >
          {t("apps.tasksRefresh")}
        </Button>
      </Space>
      {failed ? (
        <Alert type="error" showIcon message={t("apps.tasksLoadFailed")} />
      ) : null}
      <Table
        rowKey="task_id"
        size="small"
        dataSource={tasks}
        columns={columns}
        loading={loading}
        pagination={false}
        locale={{ emptyText: t("apps.tasksEmpty") }}
      />
    </Space>
  );
}
