import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { FileSpreadsheet, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import {
  appsApi,
  type AppColumn,
  type AppInspectReport,
  type AppInspectSheet,
  type SetupPlan,
} from "../../../api/modules/apps";
import type { ToolRenderProps } from "../types";

const { Text } = Typography;

interface DraftColumn {
  key: string;
  source: string;
  label: string;
  name: string;
  type: string;
  required: boolean;
  include: boolean;
  pii: string | null;
  values: string[];
}

interface DraftTable {
  key: string;
  label: string;
  sheet: string;
  headerRow: number;
  columns: DraftColumn[];
  mapping: Record<string, string>;
  upsertKey: string[];
}

interface DraftService {
  name: string;
  provider: string;
  connectorInstanceId: string;
  connectorKind: string;
  allowedTools: string;
  allowedUrls: string;
  cacheTtl: number;
  maxCalls: number;
  enabled: boolean;
  requestHeaders: Record<string, string>;
}

function splitList(value: string): string[] {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function servicesFromPlan(plan: SetupPlan): DraftService[] {
  return (plan.services ?? []).map((service) => {
    const raw = service as {
      name?: string;
      provider?: string;
      connector_instance_id?: string;
      connector_kind?: string | null;
      allowed_tools?: string[];
      allowed_url_prefixes?: string[];
      cache_ttl_seconds?: number;
      max_calls_per_minute?: number;
      enabled?: boolean;
      request_headers?: Record<string, string>;
    };
    return {
      name: raw.name ?? "",
      provider: raw.provider ?? "external",
      connectorInstanceId: raw.connector_instance_id ?? "",
      connectorKind: raw.connector_kind ?? "http-extract",
      allowedTools: (raw.allowed_tools ?? []).join(", "),
      allowedUrls: (raw.allowed_url_prefixes ?? []).join(", "),
      cacheTtl: raw.cache_ttl_seconds ?? 300,
      maxCalls: raw.max_calls_per_minute ?? 30,
      enabled: raw.enabled ?? true,
      requestHeaders: raw.request_headers ?? {},
    };
  });
}

const TYPE_OPTIONS = [
  "string",
  "text",
  "integer",
  "number",
  "boolean",
  "date",
  "datetime",
  "enum",
].map((value) => ({ value, label: value }));

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

function isInspectReport(data: unknown): data is AppInspectReport {
  return (
    !!data &&
    typeof data === "object" &&
    Array.isArray((data as { sheets?: unknown }).sheets)
  );
}

function planIdOf(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const value = (data as { plan_id?: unknown }).plan_id;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function numberOf(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function stringsOf(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function columnsFromReport(
  sheet: AppInspectSheet | null,
  includePii: boolean,
): DraftColumn[] {
  if (!sheet) return [];
  return sheet.columns.map((column) => ({
    key: column.source,
    source: column.source,
    label: column.source,
    name: column.name_suggestion,
    type: column.type,
    required: false,
    include: !column.pii || includePii,
    pii: column.pii ?? null,
    values: [],
  }));
}

function columnsFromApi(
  columns: AppColumn[],
  includePii: boolean,
): DraftColumn[] {
  return columns.map((column) => ({
    key: column.name,
    source: column.name,
    label: column.label || column.name,
    name: column.name,
    type: column.type || "string",
    required: Boolean(column.required),
    include: !column.pii || includePii,
    pii: column.pii ?? null,
    values: column.values ?? [],
  }));
}

function tablesFromPlan(plan: SetupPlan): DraftTable[] {
  const tables =
    plan.tables && plan.tables.length > 0
      ? plan.tables
      : [
          {
            key: "main",
            label: plan.name,
            sheet: plan.sheet,
            header_row: plan.header_row,
            columns: plan.columns,
            mapping: {},
            upsert_key: [],
            include_pii: plan.include_pii,
          },
        ];
  return tables.map((table) => ({
    key: table.key || "main",
    label: table.label || table.sheet || table.key || "main",
    sheet: table.sheet,
    headerRow: table.header_row ?? 0,
    columns: columnsFromApi(table.columns, table.include_pii),
    mapping: table.mapping ?? {},
    upsertKey: table.upsert_key ?? [],
  }));
}

function tablesFromReport(report: AppInspectReport): DraftTable[] {
  const sheet = report.sheets[0] ?? null;
  return [
    {
      key: "main",
      label: sheet?.name || "main",
      sheet: sheet?.name || "",
      headerRow: sheet?.header_row ?? 0,
      columns: columnsFromReport(sheet, false),
      mapping: {},
      upsertKey: [],
    },
  ];
}

function toApiColumn(row: DraftColumn): AppColumn {
  return {
    name: row.name.trim(),
    label: row.label || row.source,
    type: row.type,
    required: row.required,
    values: row.values,
    ...(row.pii ? { pii: row.pii } : {}),
  };
}

/** Structured inspection + durable setup plan card. Legacy tool results are read-only. */
export function AppInspectCard({ data, status }: ToolRenderProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const planId = planIdOf(data);
  const [plan, setPlan] = useState<SetupPlan | null>(null);
  const [loading, setLoading] = useState(Boolean(planId));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [appName, setAppName] = useState("");
  const [draftTables, setDraftTables] = useState<DraftTable[]>([]);
  const [activeTable, setActiveTable] = useState(0);
  const [draftServices, setDraftServices] = useState<DraftService[]>([]);
  const [capabilitiesEnabled, setCapabilitiesEnabled] = useState(false);
  const [draftCapabilities, setDraftCapabilities] = useState<string[]>([]);

  const legacyReport = isInspectReport(data) ? data : null;
  const report = plan?.report ?? legacyReport;
  const active = draftTables[activeTable] ?? draftTables[0] ?? null;
  const rows = active?.columns ?? [];
  const sheet =
    (active?.sheet
      ? report?.sheets.find((item) => item.name === active.sheet)
      : undefined) ??
    report?.sheets[0] ??
    null;
  const planStatus = plan?.status ?? "draft";
  const terminal = ["imported", "cancelled", "expired", "superseded"].includes(
    planStatus,
  );
  const editable =
    Boolean(planId) &&
    (planStatus === "draft" || planStatus === "dry_run_failed") &&
    !busy;

  useEffect(() => {
    if (!planId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    appsApi
      .getSetupPlan(planId)
      .then((next) => {
        if (cancelled) return;
        setPlan(next);
        setAppName(next.name);
        setDraftTables(tablesFromPlan(next));
        setActiveTable(0);
        setDraftServices(servicesFromPlan(next));
        setCapabilitiesEnabled(next.capabilities != null);
        setDraftCapabilities(next.capabilities ?? []);
        setError(null);
      })
      .catch((exc: unknown) => {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : String(exc));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [planId]);

  useEffect(() => {
    if (!planId) return;
    if (planStatus !== "confirmed" && planStatus !== "importing") return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      appsApi
        .getSetupPlan(planId)
        .then((next) => {
          if (!cancelled) {
            setPlan((prev) => ({
              ...next,
              report: next.report ?? prev?.report,
            }));
          }
        })
        .catch(() => {
          // Transient poll failure: keep the current card and try again.
        });
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [planId, planStatus]);

  useEffect(() => {
    if (plan || !legacyReport) return;
    setDraftTables(tablesFromReport(legacyReport));
    setDraftServices([]);
    setCapabilitiesEnabled(false);
    setDraftCapabilities([]);
    const base = legacyReport.file.split("/").pop() || "App";
    setAppName(base.replace(/\.[^.]+$/, "") || "App");
  }, [legacyReport, plan]);

  const update = (key: string, patch: Partial<DraftColumn>) => {
    setDraftTables((prev) =>
      prev.map((table, index) =>
        index === activeTable
          ? {
              ...table,
              columns: table.columns.map((row) =>
                row.key === key ? { ...row, ...patch } : row,
              ),
            }
          : table,
      ),
    );
  };

  const includesPii = draftTables.some((table) =>
    table.columns.some((row) => row.include && row.pii),
  );
  const allTablesReady =
    draftTables.length > 0 &&
    draftTables.every((table) => table.columns.some((row) => row.include));
  const actions = plan?.allowed_actions ?? [];
  const servicesReady = draftServices.every(
    (service) =>
      !!service.name.trim() && splitList(service.allowedTools).length > 0,
  );
  const canConfirm =
    !!planId &&
    actions.includes("confirm") &&
    allTablesReady &&
    servicesReady &&
    !!appName.trim() &&
    status === "done";

  const applyPlan = (next: SetupPlan) => {
    setPlan((prev) => ({
      ...next,
      report: next.report ?? prev?.report,
    }));
    const tables = tablesFromPlan(next);
    setDraftTables(tables);
    setActiveTable((prev) => Math.min(prev, Math.max(0, tables.length - 1)));
    setDraftServices(servicesFromPlan(next));
    setCapabilitiesEnabled(next.capabilities != null);
    setDraftCapabilities(next.capabilities ?? []);
    setError(null);
  };

  const run = async (action: () => Promise<SetupPlan>) => {
    setBusy(true);
    setError(null);
    try {
      applyPlan(await action());
    } catch (exc: unknown) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const confirm = () => {
    if (!plan || !canConfirm) return;
    const apiTables = draftTables.map((table) => {
      const columns = table.columns
        .filter((row) => row.include)
        .map((row) => toApiColumn(row));
      return {
        key: table.key,
        label: table.label,
        sheet: table.sheet,
        header_row: table.headerRow,
        columns,
        mapping: table.mapping,
        upsert_key: table.upsertKey,
        include_pii: columns.some((column) => !!column.pii),
      };
    });
    const main = apiTables[0];
    void run(() =>
      appsApi.confirmSetupPlan(plan.plan_id, {
        revision: plan.revision,
        name: appName.trim(),
        columns: main?.columns ?? [],
        include_pii: main?.include_pii ?? false,
        tables: apiTables,
        services: draftServices.map((service) => ({
          name: service.name.trim(),
          provider: service.provider,
          connector_instance_id: service.connectorInstanceId.trim(),
          connector_kind: service.connectorKind.trim() || null,
          allowed_tools: splitList(service.allowedTools),
          allowed_url_prefixes: splitList(service.allowedUrls),
          request_headers: service.requestHeaders,
          cache_ttl_seconds: service.cacheTtl,
          max_calls_per_minute: service.maxCalls,
          enabled: service.enabled,
        })),
        capabilities: capabilitiesEnabled ? draftCapabilities : undefined,
      }),
    );
  };

  const updateService = (index: number, patch: Partial<DraftService>) => {
    setDraftServices((prev) =>
      prev.map((service, i) =>
        i === index ? { ...service, ...patch } : service,
      ),
    );
  };

  const addService = () => {
    setDraftServices((prev) => [
      ...prev,
      {
        name: "",
        provider: "external",
        connectorInstanceId: "",
        connectorKind: "http-extract",
        allowedTools: "",
        allowedUrls: "",
        cacheTtl: 300,
        maxCalls: 30,
        enabled: true,
        requestHeaders: {},
      },
    ]);
  };

  const removeService = (index: number) => {
    setDraftServices((prev) => prev.filter((_, i) => i !== index));
  };

  const execute = () => {
    if (!plan) return;
    void run(() => appsApi.executeSetupPlan(plan.plan_id, plan.revision));
  };

  const cancel = () => {
    if (!plan) return;
    void run(() => appsApi.cancelSetupPlan(plan.plan_id));
  };

  if (!report) {
    if (error) {
      return <Alert type="error" showIcon message={error} />;
    }
    return (
      <Text type="secondary">
        {loading ? t("apps.planLoading") : t("apps.inspectCardInvalid")}
      </Text>
    );
  }

  const dryRun = plan?.dry_run ?? {};
  const result = plan?.result ?? {};
  const dryErrors = stringsOf(dryRun.errors);
  const dryBlocked = stringsOf(dryRun.blocked_pii);
  const importErrors = stringsOf(result.errors);
  const appId = plan?.app_id ?? null;
  const tableLines = (
    payload: Record<string, unknown>,
    metric: string,
  ): string[] => {
    const tables = payload.tables;
    if (!tables || typeof tables !== "object") return [];
    return Object.entries(
      tables as Record<string, Record<string, unknown>>,
    ).map(([key, item]) => `${key}: ${numberOf(item?.[metric])}`);
  };
  const dryDescription = [
    ...tableLines(dryRun, "would_import"),
    ...dryErrors.slice(0, 3),
  ].join("; ");
  const importDescription = [
    ...tableLines(result, "imported"),
    ...importErrors.slice(0, 3),
  ].join("; ");

  return (
    <Space direction="vertical" size={10} style={{ width: "100%" }}>
      <Space size={6} wrap>
        <FileSpreadsheet size={16} />
        <Text strong>{plan?.file ?? report.file}</Text>
        <Text type="secondary">
          {t("apps.inspectRows", { count: sheet?.rows ?? 0 })}
        </Text>
        {planId ? (
          <Tag color={planStatus === "imported" ? "green" : "blue"}>
            {t(STATUS_KEYS[planStatus] ?? "apps.planStatusUnknown")}
          </Tag>
        ) : (
          <Tag>{t("apps.planReadonly")}</Tag>
        )}
      </Space>

      <Space wrap>
        {draftTables.length > 1 ? (
          <Select
            size="small"
            value={activeTable}
            onChange={setActiveTable}
            options={draftTables.map((table, index) => ({
              value: index,
              label: table.label || table.key,
            }))}
            style={{ minWidth: 140 }}
          />
        ) : null}
        <Text type="secondary">{t("apps.inspectSheet")}</Text>
        <Text>{active?.sheet || sheet?.name || "-"}</Text>
        <Text type="secondary">
          {t("apps.inspectHeaderRow", {
            row: (active?.headerRow ?? sheet?.header_row ?? 0) + 1,
          })}
        </Text>
        {plan ? (
          <>
            <Text type="secondary">{t("apps.name")}</Text>
            <Input
              size="small"
              value={appName}
              maxLength={100}
              disabled={!editable}
              onChange={(event) => setAppName(event.target.value)}
              style={{ width: 180 }}
            />
          </>
        ) : null}
      </Space>

      {plan && editable ? (
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Text strong>{t("apps.planServicesTitle")}</Text>
          <Space wrap>
            <Checkbox
              checked={capabilitiesEnabled}
              disabled={!editable}
              onChange={(event) => setCapabilitiesEnabled(event.target.checked)}
            >
              {t("apps.planEnableCapabilities")}
            </Checkbox>
            <Select
              mode="multiple"
              size="small"
              value={draftCapabilities}
              disabled={!editable || !capabilitiesEnabled}
              onChange={setDraftCapabilities}
              options={(plan.allowed_capabilities ?? []).map((value) => ({
                value,
                label: value,
              }))}
              placeholder={t("apps.planCapabilityOptions")}
              style={{ minWidth: 260 }}
            />
          </Space>
          {draftServices.length === 0 ? (
            <Text type="secondary">{t("apps.planNoServices")}</Text>
          ) : null}
          {draftServices.map((service, index) => (
            <Space
              key={index}
              direction="vertical"
              size={6}
              style={{
                width: "100%",
                padding: 8,
                border: "1px solid rgba(127,127,127,0.25)",
                borderRadius: 6,
              }}
            >
              <Space wrap>
                <Input
                  size="small"
                  value={service.name}
                  disabled={!editable}
                  placeholder={t("apps.planServiceName")}
                  onChange={(event) =>
                    updateService(index, { name: event.target.value })
                  }
                  style={{ width: 150 }}
                />
                <Select
                  size="small"
                  value={service.provider}
                  disabled={!editable}
                  onChange={(value) =>
                    updateService(index, {
                      provider: value,
                      ...(value === "connector" ? { requestHeaders: {} } : {}),
                    })
                  }
                  options={[
                    { value: "external", label: "external" },
                    { value: "connector", label: "connector" },
                  ]}
                  style={{ width: 120 }}
                />
                <Input
                  size="small"
                  value={service.connectorInstanceId}
                  disabled={!editable}
                  placeholder={t("apps.planConnectorInstance")}
                  onChange={(event) =>
                    updateService(index, {
                      connectorInstanceId: event.target.value,
                    })
                  }
                  style={{ width: 180 }}
                />
                <Input
                  size="small"
                  value={service.connectorKind}
                  disabled={!editable || service.provider === "connector"}
                  placeholder={t("apps.planConnectorKind")}
                  onChange={(event) =>
                    updateService(index, {
                      connectorKind: event.target.value,
                      ...(event.target.value !== "http-extract"
                        ? { requestHeaders: {} }
                        : {}),
                    })
                  }
                  style={{ width: 150 }}
                />
                <Checkbox
                  checked={service.enabled}
                  disabled={!editable}
                  onChange={(event) =>
                    updateService(index, { enabled: event.target.checked })
                  }
                >
                  {t("apps.planServiceEnabled")}
                </Checkbox>
                <Button
                  size="small"
                  disabled={!editable}
                  onClick={() => removeService(index)}
                >
                  {t("apps.planRemoveService")}
                </Button>
              </Space>
              <Input
                size="small"
                value={service.allowedTools}
                disabled={!editable}
                placeholder={t("apps.planAllowedTools")}
                onChange={(event) =>
                  updateService(index, { allowedTools: event.target.value })
                }
              />
              <Input
                size="small"
                value={service.allowedUrls}
                disabled={!editable}
                placeholder={t("apps.planAllowedUrls")}
                onChange={(event) =>
                  updateService(index, { allowedUrls: event.target.value })
                }
              />
              <Space wrap>
                <InputNumber
                  size="small"
                  min={0}
                  max={86400}
                  value={service.cacheTtl}
                  disabled={!editable}
                  addonBefore={t("apps.planCacheTtl")}
                  onChange={(value) =>
                    updateService(index, { cacheTtl: Number(value ?? 300) })
                  }
                />
                <InputNumber
                  size="small"
                  min={1}
                  max={600}
                  value={service.maxCalls}
                  disabled={!editable}
                  addonBefore={t("apps.planMaxCalls")}
                  onChange={(value) =>
                    updateService(index, { maxCalls: Number(value ?? 30) })
                  }
                />
              </Space>
            </Space>
          ))}
          <Button size="small" disabled={!editable} onClick={addService}>
            {t("apps.planAddService")}
          </Button>
        </Space>
      ) : null}

      {plan &&
      !editable &&
      (plan.services?.length || plan.capabilities?.length) ? (
        <Space direction="vertical" size={4}>
          {plan.services?.length ? (
            <Text type="secondary">
              {t("apps.planServices", {
                names: plan.services
                  .map((service) => `${service.name} (${service.provider})`)
                  .join(", "),
              })}
            </Text>
          ) : null}
          {plan.capabilities?.length ? (
            <Text type="secondary">
              {t("apps.planCapabilities", {
                names: plan.capabilities.join(", "),
              })}
            </Text>
          ) : null}
        </Space>
      ) : null}

      {sheet?.warnings?.length ? (
        <Text type="warning">{sheet.warnings.join(" · ")}</Text>
      ) : null}

      {loading ? <Spin size="small" /> : null}

      <Table
        size="small"
        rowKey="key"
        pagination={false}
        dataSource={rows}
        scroll={{ x: true }}
        columns={[
          {
            title: t("apps.inspectInclude"),
            width: 70,
            render: (_, row: DraftColumn) => (
              <Checkbox
                checked={row.include}
                disabled={!editable}
                onChange={(event) =>
                  update(row.key, { include: event.target.checked })
                }
              />
            ),
          },
          {
            title: t("apps.inspectSource"),
            dataIndex: "source",
            width: 140,
            ellipsis: true,
            render: (value: string, row: DraftColumn) => (
              <Space size={4}>
                {value}
                {row.pii ? <Tag color="red">{row.pii}</Tag> : null}
              </Space>
            ),
          },
          {
            title: t("apps.inspectName"),
            width: 160,
            render: (_, row: DraftColumn) => (
              <Input
                size="small"
                value={row.name}
                disabled={!editable}
                onChange={(event) =>
                  update(row.key, { name: event.target.value })
                }
              />
            ),
          },
          {
            title: t("apps.inspectType"),
            width: 120,
            render: (_, row: DraftColumn) => (
              <Select
                size="small"
                value={row.type}
                disabled={!editable}
                onChange={(value) => update(row.key, { type: value })}
                options={TYPE_OPTIONS}
                style={{ width: "100%" }}
              />
            ),
          },
          {
            title: t("apps.inspectRequired"),
            width: 70,
            render: (_, row: DraftColumn) => (
              <Checkbox
                checked={row.required}
                disabled={!editable}
                onChange={(event) =>
                  update(row.key, { required: event.target.checked })
                }
              />
            ),
          },
        ]}
      />

      {includesPii && planStatus !== "imported" ? (
        <Text type="danger">
          <ShieldAlert size={14} /> {t("apps.inspectPiiWarning")}
        </Text>
      ) : null}

      {error || plan?.error ? (
        <Alert type="error" showIcon message={error || plan?.error} />
      ) : null}

      {planStatus === "dry_run_ready" || planStatus === "import_partial" ? (
        <Alert
          type={planStatus === "import_partial" ? "warning" : "success"}
          showIcon
          message={t("apps.planDryRunSummary", {
            would: numberOf(dryRun.would_import),
            skipped: numberOf(dryRun.skipped),
            errors: dryErrors.length,
            blocked: dryBlocked.length,
          })}
          description={dryDescription || undefined}
        />
      ) : null}

      {planStatus === "imported" || planStatus === "import_partial" ? (
        <Alert
          type={planStatus === "imported" ? "success" : "warning"}
          showIcon
          message={t("apps.planImportSummary", {
            imported: numberOf(result.imported),
            updated: numberOf(result.updated),
            skipped: numberOf(result.skipped),
            errors: importErrors.length,
          })}
          description={importDescription || undefined}
        />
      ) : null}

      {terminal ? (
        <Text type="secondary">
          {t(STATUS_KEYS[planStatus] ?? "apps.planStatusUnknown")}
        </Text>
      ) : null}

      <Space wrap>
        {planId && actions.includes("confirm") ? (
          <Button
            type="primary"
            disabled={!canConfirm}
            loading={busy}
            onClick={confirm}
          >
            {planStatus === "confirmed"
              ? t("apps.planRetryConfirm")
              : planStatus === "dry_run_failed"
              ? t("apps.planReconfirm")
              : t("apps.planConfirm")}
          </Button>
        ) : null}
        {planId && actions.includes("execute") ? (
          <Button type="primary" loading={busy} onClick={execute}>
            {planStatus === "import_partial"
              ? t("apps.planRetryImport")
              : t("apps.planExecute")}
          </Button>
        ) : null}
        {planId && actions.includes("cancel") ? (
          <Button disabled={busy} onClick={cancel}>
            {t("apps.planCancel")}
          </Button>
        ) : null}
        {appId &&
        (planStatus === "imported" || planStatus === "import_partial") ? (
          <Button
            onClick={() => navigate(`/apps/${encodeURIComponent(appId)}`)}
          >
            {t("apps.planOpenApp")}
          </Button>
        ) : null}
        {!planId ? (
          <Text type="secondary">{t("apps.planReadonlyHint")}</Text>
        ) : null}
        {planStatus === "confirmed" || planStatus === "importing" ? (
          <Text type="secondary">
            {planStatus === "importing"
              ? t("apps.planImportingHint")
              : t("apps.planConfirmingHint")}
          </Text>
        ) : null}
        {planStatus === "dry_run_ready" || planStatus === "import_partial" ? (
          <Text type="secondary">{t("apps.planExecuteHint")}</Text>
        ) : null}
      </Space>
    </Space>
  );
}
