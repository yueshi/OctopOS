import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactElement } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Input,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import {
  appsApi,
  type SetupPlan,
  type SetupPlanSummary,
  type SetupPlanTable,
} from "../../../api/modules/apps";
import PageShell from "../../../layouts/PageShell";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";

const { Text } = Typography;

const STATUS_KEYS: Record<string, string> = {
  draft: "apps.planStatusDraft",
  confirmed: "apps.planStatusConfirmed",
  dry_run_ready: "apps.planStatusDryRunReady",
  dry_run_failed: "apps.planStatusDryRunFailed",
  importing: "apps.planStatusImporting",
  imported: "apps.planStatusImported",
  import_partial: "apps.planStatusImportPartial",
  cancelled: "apps.planStatusCancelled",
  expired: "apps.planStatusExpired",
  superseded: "apps.planStatusSuperseded",
};

const STATUS_COLORS: Record<string, string> = {
  draft: "default",
  confirmed: "processing",
  dry_run_ready: "blue",
  dry_run_failed: "error",
  importing: "processing",
  imported: "success",
  import_partial: "warning",
  cancelled: "default",
  expired: "default",
  superseded: "purple",
};

interface Filters {
  status?: string;
  agent_id?: string;
  user_id?: number;
  app_id?: string;
  thread_id?: string;
  has_error?: boolean;
}

function numberOf(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function jsonText(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value);
  }
}

function statusTag(
  status: string,
  translate: (key: string) => string,
): ReactElement {
  return (
    <Tag color={STATUS_COLORS[status] ?? "default"}>
      {translate(STATUS_KEYS[status] ?? "apps.planStatusUnknown")}
    </Tag>
  );
}

function tableColumns(
  translate: (key: string) => string,
): TableColumnsType<SetupPlanTable> {
  return [
    { title: translate("adminSetupPlans.table.key"), dataIndex: "key" },
    { title: translate("adminSetupPlans.table.label"), dataIndex: "label" },
    { title: translate("adminSetupPlans.table.sheet"), dataIndex: "sheet" },
    {
      title: translate("adminSetupPlans.table.header"),
      dataIndex: "header_row",
      render: (value: number) => value + 1,
    },
    {
      title: translate("adminSetupPlans.table.columns"),
      dataIndex: "columns",
      render: (value: unknown[]) => value?.length ?? 0,
    },
    {
      title: translate("adminSetupPlans.table.pii"),
      dataIndex: "include_pii",
      render: (value: boolean) => (value ? "yes" : "no"),
    },
  ];
}

export default function AdminSetupPlansPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const timeZone = useServerTimezone();
  const [plans, setPlans] = useState<SetupPlanSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [applied, setApplied] = useState<Filters>({});
  const [status, setStatus] = useState("");
  const [agentId, setAgentId] = useState("");
  const [userId, setUserId] = useState("");
  const [appId, setAppId] = useState("");
  const [threadId, setThreadId] = useState("");
  const [hasError, setHasError] = useState("all");
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detail, setDetail] = useState<SetupPlan | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await appsApi.listSetupPlans({
        ...applied,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      });
      setPlans(result.plans);
      setTotal(result.total);
    } catch (exc: unknown) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [applied, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const applyFilters = () => {
    const parsedUser = userId.trim() ? Number(userId.trim()) : undefined;
    setApplied({
      status: status.trim() || undefined,
      agent_id: agentId.trim() || undefined,
      user_id:
        parsedUser !== undefined && Number.isFinite(parsedUser)
          ? parsedUser
          : undefined,
      app_id: appId.trim() || undefined,
      thread_id: threadId.trim() || undefined,
      has_error: hasError === "all" ? undefined : hasError === "true",
    });
    setPage(1);
  };

  const resetFilters = () => {
    setStatus("");
    setAgentId("");
    setUserId("");
    setAppId("");
    setThreadId("");
    setHasError("all");
    setApplied({});
    setPage(1);
  };

  const openDetail = async (planId: string) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    try {
      setDetail(await appsApi.getSetupPlan(planId));
    } catch (exc: unknown) {
      setDetailError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setDetailLoading(false);
    }
  };

  const columns = useMemo<TableColumnsType<SetupPlanSummary>>(
    () => [
      {
        title: t("adminSetupPlans.columns.plan"),
        dataIndex: "name",
        render: (_, row) => (
          <Space direction="vertical" size={0}>
            <Text strong>{row.name || "—"}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {row.plan_id}
            </Text>
          </Space>
        ),
      },
      {
        title: t("adminSetupPlans.columns.status"),
        dataIndex: "status",
        width: 130,
        render: (value: string) => statusTag(value, t),
      },
      {
        title: t("adminSetupPlans.columns.source"),
        dataIndex: "file",
        ellipsis: true,
      },
      {
        title: t("adminSetupPlans.columns.owner"),
        dataIndex: "user_id",
        width: 80,
      },
      {
        title: t("adminSetupPlans.columns.agent"),
        dataIndex: "agent_id",
        width: 120,
        ellipsis: true,
      },
      {
        title: t("adminSetupPlans.columns.tables"),
        dataIndex: "table_count",
        width: 70,
      },
      {
        title: t("adminSetupPlans.columns.services"),
        dataIndex: "service_count",
        width: 80,
      },
      {
        title: t("adminSetupPlans.columns.progress"),
        width: 120,
        render: (_, row) => (
          <Text>
            {row.would_import} → {row.imported}
          </Text>
        ),
      },
      {
        title: t("adminSetupPlans.columns.created"),
        dataIndex: "created_at",
        width: 170,
        render: (value: number) => formatServerDateTime(value, timeZone),
      },
      {
        title: t("adminSetupPlans.columns.error"),
        dataIndex: "error",
        ellipsis: true,
        render: (value: string, row) => {
          const text = value || row.errors?.[0] || "";
          return text ? <Text type="danger">{text}</Text> : "—";
        },
      },
    ],
    [t, timeZone],
  );

  const detailTables = detail?.tables?.length ? detail.tables : undefined;
  const dryRun = detail?.dry_run ?? {};
  const result = detail?.result ?? {};

  return (
    <PageShell
      title={t("adminSetupPlans.title")}
      subtitle={t("adminSetupPlans.subtitle")}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap>
          <Select
            allowClear
            value={status || undefined}
            placeholder={t("adminSetupPlans.filters.status")}
            onChange={(value) => setStatus(value ?? "")}
            options={Object.keys(STATUS_KEYS).map((value) => ({
              value,
              label: t(STATUS_KEYS[value]),
            }))}
            style={{ width: 160 }}
          />
          <Input
            allowClear
            value={agentId}
            placeholder={t("adminSetupPlans.filters.agent")}
            onChange={(event) => setAgentId(event.target.value)}
            style={{ width: 150 }}
          />
          <Input
            allowClear
            value={userId}
            placeholder={t("adminSetupPlans.filters.user")}
            onChange={(event) => setUserId(event.target.value)}
            style={{ width: 110 }}
          />
          <Input
            allowClear
            value={appId}
            placeholder={t("adminSetupPlans.filters.app")}
            onChange={(event) => setAppId(event.target.value)}
            style={{ width: 160 }}
          />
          <Input
            allowClear
            value={threadId}
            placeholder={t("adminSetupPlans.filters.thread")}
            onChange={(event) => setThreadId(event.target.value)}
            style={{ width: 180 }}
          />
          <Select
            value={hasError}
            onChange={setHasError}
            style={{ width: 140 }}
            options={[
              { value: "all", label: t("adminSetupPlans.filters.all") },
              { value: "true", label: t("adminSetupPlans.filters.withError") },
              {
                value: "false",
                label: t("adminSetupPlans.filters.withoutError"),
              },
            ]}
          />
          <Button type="primary" onClick={applyFilters}>
            {t("adminSetupPlans.filters.search")}
          </Button>
          <Button onClick={resetFilters}>
            {t("adminSetupPlans.filters.reset")}
          </Button>
          <Button icon={<RefreshCw size={14} />} onClick={() => void load()}>
            {t("adminSetupPlans.filters.refresh")}
          </Button>
        </Space>

        {error ? (
          <Alert
            type="error"
            showIcon
            message={t("adminSetupPlans.loadError")}
            description={error}
          />
        ) : null}

        <Table<SetupPlanSummary>
          rowKey="plan_id"
          size="small"
          loading={loading}
          dataSource={plans}
          columns={columns}
          locale={{ emptyText: t("adminSetupPlans.empty") }}
          onRow={(row) => ({
            onClick: () => void openDetail(row.plan_id),
            style: { cursor: "pointer" },
          })}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            onChange: (nextPage, nextSize) => {
              setPage(nextPage);
              setPageSize(nextSize);
            },
          }}
        />
      </Space>

      <Drawer
        title={
          detail
            ? t("adminSetupPlans.detail.title", { id: detail.plan_id })
            : t("adminSetupPlans.detail.title", { id: "" })
        }
        width={760}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        destroyOnClose
      >
        {detailLoading ? (
          <Space>
            <Spin />
            <Text type="secondary">{t("common.loading")}</Text>
          </Space>
        ) : detailError ? (
          <Alert type="error" showIcon message={detailError} />
        ) : detail ? (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Space wrap>
              {statusTag(detail.status, t)}
              {detail.app_id ? (
                <Button
                  size="small"
                  onClick={() =>
                    navigate(
                      `/apps/${encodeURIComponent(detail.app_id as string)}`,
                    )
                  }
                >
                  {t("adminSetupPlans.detail.openApp")}
                </Button>
              ) : null}
            </Space>

            <Descriptions
              size="small"
              column={2}
              bordered
              items={[
                {
                  key: "plan",
                  label: t("adminSetupPlans.detail.planId"),
                  children: detail.plan_id,
                },
                {
                  key: "revision",
                  label: t("adminSetupPlans.detail.revision"),
                  children: detail.revision,
                },
                {
                  key: "file",
                  label: t("adminSetupPlans.detail.file"),
                  children: detail.file,
                },
                {
                  key: "app",
                  label: t("adminSetupPlans.detail.app"),
                  children: detail.app_id ?? "—",
                },
                {
                  key: "created",
                  label: t("adminSetupPlans.detail.created"),
                  children: formatServerDateTime(
                    detail.created_at ?? 0,
                    timeZone,
                  ),
                },
                {
                  key: "confirmed",
                  label: t("adminSetupPlans.detail.confirmed"),
                  children: detail.confirmed_at
                    ? formatServerDateTime(detail.confirmed_at, timeZone)
                    : "—",
                },
                {
                  key: "executed",
                  label: t("adminSetupPlans.detail.executed"),
                  children: detail.executed_at
                    ? formatServerDateTime(detail.executed_at, timeZone)
                    : "—",
                },
                {
                  key: "expires",
                  label: t("adminSetupPlans.detail.expires"),
                  children: detail.expires_at
                    ? formatServerDateTime(detail.expires_at, timeZone)
                    : "—",
                },
                {
                  key: "error",
                  label: t("adminSetupPlans.detail.error"),
                  children: detail.error || "—",
                  span: 2,
                },
              ]}
            />

            <Space direction="vertical" size={6} style={{ width: "100%" }}>
              <Text strong>{t("adminSetupPlans.detail.dryRun")}</Text>
              <Text>
                {t("adminSetupPlans.dryRun.wouldImport")}:{" "}
                {numberOf(dryRun.would_import)} ·{" "}
                {t("adminSetupPlans.dryRun.skipped")}:{" "}
                {numberOf(dryRun.skipped)} ·{" "}
                {t("adminSetupPlans.dryRun.errors")}:{" "}
                {(dryRun.errors as unknown[] | undefined)?.length ?? 0} ·{" "}
                {t("adminSetupPlans.dryRun.blockedPii")}:{" "}
                {(dryRun.blocked_pii as unknown[] | undefined)?.length ?? 0}
              </Text>
              {(dryRun.errors as string[] | undefined)?.length ? (
                <Text type="danger">
                  {(dryRun.errors as string[]).slice(0, 5).join("; ")}
                </Text>
              ) : null}
            </Space>

            <Space direction="vertical" size={6} style={{ width: "100%" }}>
              <Text strong>{t("adminSetupPlans.detail.import")}</Text>
              <Text>
                {t("adminSetupPlans.import.imported")}:{" "}
                {numberOf(result.imported)} ·{" "}
                {t("adminSetupPlans.import.updated")}:{" "}
                {numberOf(result.updated)} ·{" "}
                {t("adminSetupPlans.import.skipped")}:{" "}
                {numberOf(result.skipped)} ·{" "}
                {t("adminSetupPlans.import.errors")}:{" "}
                {(result.errors as unknown[] | undefined)?.length ?? 0}
              </Text>
              {(result.errors as string[] | undefined)?.length ? (
                <Text type="danger">
                  {(result.errors as string[]).slice(0, 5).join("; ")}
                </Text>
              ) : null}
            </Space>

            <Space direction="vertical" size={6} style={{ width: "100%" }}>
              <Text strong>{t("adminSetupPlans.detail.tables")}</Text>
              <Table<SetupPlanTable>
                rowKey="key"
                size="small"
                pagination={false}
                dataSource={detailTables ?? []}
                columns={tableColumns(t)}
              />
            </Space>

            <Space direction="vertical" size={6} style={{ width: "100%" }}>
              <Text strong>{t("adminSetupPlans.detail.services")}</Text>
              {detail.services?.length ? (
                detail.services.map((service) => (
                  <Space key={service.name} wrap>
                    <Tag color="blue">{service.name}</Tag>
                    <Text type="secondary">{service.provider}</Text>
                    <Text type="secondary">
                      {(service.allowed_tools ?? []).join(", ")}
                    </Text>
                  </Space>
                ))
              ) : (
                <Text type="secondary">—</Text>
              )}
            </Space>

            <Space direction="vertical" size={6} style={{ width: "100%" }}>
              <Text strong>{t("adminSetupPlans.detail.capabilities")}</Text>
              {detail.capabilities?.length ? (
                <Space wrap>
                  {detail.capabilities.map((capability) => (
                    <Tag key={capability}>{capability}</Tag>
                  ))}
                </Space>
              ) : (
                <Text type="secondary">—</Text>
              )}
            </Space>

            {detail.report?.sheets?.length ? (
              <Space direction="vertical" size={6} style={{ width: "100%" }}>
                <Text strong>{t("adminSetupPlans.detail.report")}</Text>
                {detail.report.sheets.map((sheet) => (
                  <Text key={sheet.name}>
                    {sheet.name}: {t("adminSetupPlans.detail.rows")}{" "}
                    {sheet.rows}
                  </Text>
                ))}
              </Space>
            ) : null}

            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              <Text strong>{t("adminSetupPlans.detail.audit")}</Text>
              {detail.audit?.length ? (
                <Timeline
                  items={detail.audit.map((event) => ({
                    key: `${event.ts}-${event.action}`,
                    children: (
                      <Space direction="vertical" size={2}>
                        <Text>
                          {formatServerDateTime(event.ts, timeZone)} ·{" "}
                          {event.actor ?? "—"} · {event.action}
                        </Text>
                        {event.payload ? (
                          <pre
                            style={{
                              margin: 0,
                              fontSize: 11,
                              maxWidth: 680,
                              whiteSpace: "pre-wrap",
                            }}
                          >
                            {jsonText(event.payload)}
                          </pre>
                        ) : null}
                      </Space>
                    ),
                  }))}
                />
              ) : (
                <Text type="secondary">
                  {t("adminSetupPlans.detail.noAudit")}
                </Text>
              )}
            </Space>
          </Space>
        ) : null}
      </Drawer>
    </PageShell>
  );
}
