import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
} from "antd";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  appsApi,
  type AppServiceDeclaration,
  type AppServiceInput,
  type AppSourceTool,
  type ServiceToolDeclaration,
} from "../../../api/modules/apps";
import {
  connectorsApi,
  type ConnectorInstance,
} from "../../../api/modules/connectors";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "../../../utils/antdMessage";
import { InfoSection } from "./shared";

const { Text } = Typography;

const PUBLIC_HTTP_TOOLS: AppSourceTool[] = [
  {
    name: "fetch_json",
    description: "GET a public JSON endpoint",
    read_only: true,
    input_schema: {},
  },
  {
    name: "fetch_text",
    description: "GET a public text endpoint",
    read_only: true,
    input_schema: {},
  },
  {
    name: "fetch_html",
    description: "GET a public HTML page",
    read_only: true,
    input_schema: {},
  },
];

const V1B_READ_TOOLS: ServiceToolDeclaration[] = [
  {
    name: "runs.list",
    method: "GET",
    path: "/v1/runs",
    input_schema: { type: "object" },
  },
  {
    name: "runs.get",
    method: "GET",
    path: "/v1/runs/{run_id}",
    input_schema: { type: "object", required: ["run_id"] },
  },
  {
    name: "assets.content",
    method: "GET",
    path: "/v1/assets/{asset_id}/content",
    input_schema: { type: "object", required: ["asset_id"] },
  },
];

const V1B_WRITE_TOOLS: ServiceToolDeclaration[] = [
  {
    name: "runs.create",
    method: "POST",
    path: "/v1/runs",
    input_schema: { type: "object", required: ["run_id", "title"] },
  },
  {
    name: "review.decide",
    method: "POST",
    path: "/v1/runs/{run_id}/review",
    input_schema: { type: "object", required: ["run_id", "decision"] },
  },
  {
    name: "assets.upload",
    method: "POST",
    path: "/v1/assets/stream",
    input_schema: { type: "object" },
  },
];

function toolsToText(
  tools: readonly ServiceToolDeclaration[] | undefined,
): string {
  return JSON.stringify(tools ?? [], null, 2);
}

function textToTools(
  text: string,
  invalidMessage: string,
): ServiceToolDeclaration[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text || "[]");
  } catch {
    throw new Error(invalidMessage);
  }
  if (!Array.isArray(parsed)) throw new Error(invalidMessage);
  for (const item of parsed) {
    if (
      !item ||
      typeof item !== "object" ||
      typeof (item as { name?: unknown }).name !== "string" ||
      typeof (item as { method?: unknown }).method !== "string" ||
      typeof (item as { path?: unknown }).path !== "string"
    ) {
      throw new Error(invalidMessage);
    }
  }
  return parsed as ServiceToolDeclaration[];
}

function headersToText(headers: Record<string, string>): string {
  return Object.entries(headers)
    .map(([name, value]) => `${name}: ${value}`)
    .join("\n");
}

function textToHeaders(
  text: string,
  invalidMessage: string,
): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const index = line.indexOf(":");
    if (index <= 0) throw new Error(invalidMessage);
    const name = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim();
    if (!name || !value) throw new Error(invalidMessage);
    headers[name] = value;
  }
  return headers;
}

interface Props {
  appId: string;
  open: boolean;
  canManage: boolean;
}

interface ServiceFormValues {
  name: string;
  provider: "connector" | "external" | "extension";
  connector_instance_id: string;
  allowed_tools: string[];
  allowed_url_prefixes: string[];
  request_headers: string;
  cache_ttl_seconds: number;
  max_calls_per_minute: number;
  enabled: boolean;
  mode?: "read" | "write";
  base_url?: string;
  extension_id?: string;
  service_token?: string;
  read_tools_text?: string;
  write_tools_text?: string;
  allow_private_network?: boolean;
}

export default function ServicesSection({ appId, open, canManage }: Props) {
  const { t } = useTranslation();
  const [services, setServices] = useState<AppServiceDeclaration[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<AppServiceDeclaration | null>(null);
  const [saving, setSaving] = useState(false);
  const [instances, setInstances] = useState<ConnectorInstance[]>([]);
  const [tools, setTools] = useState<AppSourceTool[]>([]);
  const [form] = Form.useForm<ServiceFormValues>();
  const instanceId = Form.useWatch("connector_instance_id", form);
  const provider = Form.useWatch("provider", form) as
    | ServiceFormValues["provider"]
    | undefined;
  const selectedInstance = instances.find(
    (item) => item.instance_id === instanceId,
  );
  const supportsRequestHeaders =
    selectedInstance?.kind === "http-extract" ||
    (provider === "external" && !instanceId);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await appsApi.listServices(appId);
      setServices(data.services);
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.services.loadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [appId, t]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  useEffect(() => {
    if (!open || !canManage) return;
    connectorsApi
      .listInstances()
      .then((data) => setInstances(data))
      .catch(() => setInstances([]));
  }, [open, canManage]);

  useEffect(() => {
    if (!modalOpen || (!instanceId && provider !== "external")) {
      setTools([]);
      return;
    }
    if (!instanceId) {
      // Empty connector + external = the built-in public HTTP source.
      setTools(PUBLIC_HTTP_TOOLS);
      return;
    }
    appsApi
      .sourceTools(appId, instanceId)
      .then((data) => setTools(data.tools.filter((tool) => tool.read_only)))
      .catch(() => setTools([]));
  }, [appId, modalOpen, instanceId, provider]);

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue({
      name: "",
      provider: "external",
      connector_instance_id: "",
      allowed_tools: ["fetch_json"],
      allowed_url_prefixes: [],
      request_headers: "",
      cache_ttl_seconds: 300,
      max_calls_per_minute: 30,
      enabled: true,
      mode: "write",
      base_url: "",
      extension_id: "",
      service_token: "",
      read_tools_text: toolsToText(V1B_READ_TOOLS),
      write_tools_text: toolsToText(V1B_WRITE_TOOLS),
      allow_private_network: false,
    });
    setModalOpen(true);
  };

  const openEdit = (service: AppServiceDeclaration) => {
    setEditing(service);
    form.setFieldsValue({
      name: service.name,
      provider: service.provider,
      connector_instance_id: service.connector_instance_id,
      allowed_tools: service.allowed_tools,
      allowed_url_prefixes: service.allowed_url_prefixes,
      request_headers: headersToText(service.request_headers || {}),
      cache_ttl_seconds: service.cache_ttl_seconds,
      max_calls_per_minute: service.max_calls_per_minute,
      enabled: service.enabled,
      mode: service.mode ?? "write",
      base_url: service.base_url ?? "",
      extension_id: service.extension_id ?? "",
      service_token: "",
      read_tools_text: toolsToText(service.read_tools),
      write_tools_text: toolsToText(service.write_tools),
      allow_private_network: Boolean(service.allow_private_network),
    });
    setModalOpen(true);
  };

  const save = async (values: ServiceFormValues) => {
    const instance = instances.find(
      (item) => item.instance_id === values.connector_instance_id,
    );
    const supportsHeaders =
      instance?.kind === "http-extract" ||
      (values.provider === "external" && !values.connector_instance_id);
    let body: AppServiceInput;
    if (values.provider === "extension") {
      const readTools = textToTools(
        values.read_tools_text ?? "[]",
        t("apps.services.extensionToolsInvalid"),
      );
      const writeTools = textToTools(
        values.write_tools_text ?? "[]",
        t("apps.services.extensionToolsInvalid"),
      );
      body = {
        provider: "extension",
        connector_instance_id: "",
        allowed_tools: [],
        allowed_url_prefixes: [],
        request_headers: {},
        cache_ttl_seconds: 0,
        max_calls_per_minute: values.max_calls_per_minute,
        enabled: values.enabled,
        mode: values.mode ?? "write",
        base_url: (values.base_url ?? "").trim(),
        extension_id: (values.extension_id ?? "").trim(),
        service_token: values.service_token?.trim()
          ? values.service_token
          : undefined,
        read_tools: readTools,
        write_tools: writeTools,
        timeout_seconds: 30,
        max_request_bytes: 65536,
        max_response_bytes: 5_242_880,
        idempotency_required: true,
        confirmation: {},
        allow_private_network: Boolean(values.allow_private_network),
      };
    } else {
      body = {
        provider: values.provider,
        connector_instance_id: values.connector_instance_id,
        allowed_tools: values.allowed_tools,
        allowed_url_prefixes: values.allowed_url_prefixes,
        request_headers: supportsHeaders
          ? textToHeaders(
              values.request_headers,
              t("apps.services.requestHeadersInvalid"),
            )
          : {},
        cache_ttl_seconds: values.cache_ttl_seconds,
        max_calls_per_minute: values.max_calls_per_minute,
        enabled: values.enabled,
      };
    }
    const saved = await appsApi.saveService(appId, values.name, body);
    setServices((current) => {
      const next = current.filter((item) => item.name !== saved.name);
      return [...next, saved].sort((a, b) => a.name.localeCompare(b.name));
    });
  };

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await save(values);
      setModalOpen(false);
      message.success(t("apps.services.saved"));
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.services.saveFailed"), t));
    } finally {
      setSaving(false);
    }
  };

  const toInput = (
    service: AppServiceDeclaration,
    enabled: boolean,
  ): AppServiceInput => {
    if (service.provider === "extension") {
      return {
        provider: "extension",
        connector_instance_id: "",
        allowed_tools: [],
        allowed_url_prefixes: [],
        request_headers: {},
        cache_ttl_seconds: service.cache_ttl_seconds,
        max_calls_per_minute: service.max_calls_per_minute,
        enabled,
        mode: service.mode ?? "write",
        base_url: service.base_url ?? "",
        extension_id: service.extension_id ?? "",
        read_tools: service.read_tools ?? [],
        write_tools: service.write_tools ?? [],
        timeout_seconds: service.timeout_seconds ?? 30,
        max_request_bytes: service.max_request_bytes ?? 65536,
        max_response_bytes: service.max_response_bytes ?? 5_242_880,
        idempotency_required: service.idempotency_required ?? true,
        confirmation: service.confirmation ?? {},
        allow_private_network: Boolean(service.allow_private_network),
      };
    }
    return {
      provider: service.provider,
      connector_instance_id: service.connector_instance_id,
      allowed_tools: service.allowed_tools,
      allowed_url_prefixes: service.allowed_url_prefixes,
      request_headers: service.request_headers || {},
      cache_ttl_seconds: service.cache_ttl_seconds,
      max_calls_per_minute: service.max_calls_per_minute,
      enabled,
    };
  };

  const toggle = async (service: AppServiceDeclaration, enabled: boolean) => {
    try {
      const saved = await appsApi.saveService(
        appId,
        service.name,
        toInput(service, enabled),
      );
      setServices((current) =>
        current.map((item) => (item.name === saved.name ? saved : item)),
      );
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.services.saveFailed"), t));
    }
  };

  const remove = async (service: AppServiceDeclaration) => {
    try {
      await appsApi.deleteService(appId, service.name);
      setServices((current) =>
        current.filter((item) => item.name !== service.name),
      );
      message.success(t("apps.services.deleted"));
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.services.saveFailed"), t));
    }
  };

  const instanceLabel = (id: string) => {
    const instance = instances.find((item) => item.instance_id === id);
    return instance ? `${instance.display_name} (${instance.kind})` : id;
  };

  return (
    <InfoSection
      title={t("apps.services.title")}
      hint={t("apps.services.hint")}
    >
      {loading ? (
        <div style={{ padding: 24, textAlign: "center" }}>
          <Spin />
        </div>
      ) : (
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 12px",
              border: "1px solid rgba(5, 5, 5, 0.06)",
              borderRadius: 8,
              background: "#fafafa",
            }}
          >
            <Tag color="blue">builtin</Tag>
            <Text strong style={{ fontSize: 13 }}>
              dataset
            </Text>
            <Text type="secondary" style={{ fontSize: 12, flex: 1 }} ellipsis>
              {t("apps.services.builtinDataset")}
            </Text>
            <Tag>{t("apps.services.builtin")}</Tag>
          </div>
          {services.length === 0 && (
            <Text type="secondary">{t("apps.services.empty")}</Text>
          )}
          {services.map((service) => (
            <div
              key={service.service_id}
              style={{
                padding: "10px 12px",
                border: "1px solid rgba(5, 5, 5, 0.06)",
                borderRadius: 8,
                background: "#fafafa",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Tag
                  color={
                    service.provider === "extension"
                      ? "geekblue"
                      : service.provider === "external"
                      ? "default"
                      : "purple"
                  }
                >
                  {service.provider}
                </Tag>
                <Text strong style={{ fontSize: 13 }}>
                  {service.name}
                </Text>
                <Text
                  type="secondary"
                  style={{ fontSize: 12, flex: 1, minWidth: 0 }}
                  ellipsis
                >
                  {service.provider === "extension"
                    ? `${service.base_url ?? ""} · ${service.mode ?? "write"}`
                    : instanceLabel(service.connector_instance_id)}
                </Text>
                {canManage && (
                  <>
                    <Switch
                      size="small"
                      checked={service.enabled}
                      onChange={(checked) => void toggle(service, checked)}
                    />
                    <Button
                      type="text"
                      size="small"
                      icon={<Pencil size={13} />}
                      aria-label={t("apps.services.edit")}
                      onClick={() => openEdit(service)}
                    />
                    <Popconfirm
                      title={t("apps.services.deleteConfirm")}
                      okText={t("apps.delete")}
                      cancelText={t("common.cancel")}
                      okButtonProps={{ danger: true }}
                      onConfirm={() => void remove(service)}
                    >
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<Trash2 size={13} />}
                        aria-label={t("apps.services.delete")}
                      />
                    </Popconfirm>
                  </>
                )}
              </div>
              <Space size={4} wrap style={{ marginTop: 6 }}>
                {service.provider === "extension" ? (
                  <>
                    {(service.read_tools ?? []).map((tool) => (
                      <Tag
                        key={`r:${tool.name}`}
                        style={{ marginInlineEnd: 0 }}
                      >
                        R:{tool.name}
                      </Tag>
                    ))}
                    {(service.write_tools ?? []).map((tool) => (
                      <Tag
                        key={`w:${tool.name}`}
                        color="blue"
                        style={{ marginInlineEnd: 0 }}
                      >
                        W:{tool.name}
                      </Tag>
                    ))}
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {service.token_configured
                        ? t("apps.services.extensionTokenConfigured")
                        : t("apps.services.extensionTokenMissing")}
                      {" · "}
                      {t("apps.services.extensionTimeout", {
                        seconds: service.timeout_seconds ?? 30,
                      })}
                    </Text>
                  </>
                ) : (
                  <>
                    {service.allowed_tools.map((tool) => (
                      <Tag key={tool} style={{ marginInlineEnd: 0 }}>
                        {tool}
                      </Tag>
                    ))}
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t("apps.services.limits", {
                        ttl: service.cache_ttl_seconds,
                        calls: service.max_calls_per_minute,
                      })}
                    </Text>
                  </>
                )}
              </Space>
            </div>
          ))}
          {canManage && (
            <Button
              type="dashed"
              icon={<Plus size={14} />}
              block
              onClick={openCreate}
            >
              {t("apps.services.add")}
            </Button>
          )}
        </Space>
      )}

      <Modal
        title={editing ? t("apps.services.editTitle") : t("apps.services.add")}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => void submit()}
        okText={t("apps.save")}
        cancelText={t("common.cancel")}
        confirmLoading={saving}
        destroyOnHidden
        width={560}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label={t("apps.services.name")}
            rules={[
              { required: true },
              {
                pattern: /^[a-z][a-z0-9_-]{0,63}$/,
                message: t("apps.services.nameRule"),
              },
            ]}
          >
            <Input placeholder="douban" disabled={Boolean(editing)} />
          </Form.Item>
          <Form.Item
            name="provider"
            label={t("apps.services.provider")}
            rules={[{ required: true }]}
          >
            <Select
              options={[
                {
                  value: "external",
                  label: t("apps.services.providerExternal"),
                },
                {
                  value: "connector",
                  label: t("apps.services.providerConnector"),
                },
                {
                  value: "extension",
                  label: t("apps.services.providerExtension"),
                },
              ]}
            />
          </Form.Item>
          {provider !== "extension" && (
            <>
              <Form.Item
                name="connector_instance_id"
                label={t("apps.services.connector")}
                extra={
                  provider === "external"
                    ? t("apps.services.publicHttpHint")
                    : undefined
                }
                rules={[
                  ({ getFieldValue }) => ({
                    validator(_, value) {
                      if (getFieldValue("provider") === "connector" && !value) {
                        return Promise.reject(
                          new Error(t("apps.services.selectConnector")),
                        );
                      }
                      return Promise.resolve();
                    },
                  }),
                ]}
              >
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder={t("apps.services.selectConnector")}
                  options={[
                    ...(provider === "external"
                      ? [
                          {
                            value: "",
                            label: t("apps.services.publicHttp"),
                          },
                        ]
                      : []),
                    ...instances.map((item) => ({
                      value: item.instance_id,
                      label: `${item.display_name} (${item.kind})`,
                    })),
                  ]}
                  onChange={() =>
                    form.setFieldsValue({
                      allowed_tools: [],
                    })
                  }
                />
              </Form.Item>
              <Form.Item
                name="allowed_tools"
                label={t("apps.services.allowedTools")}
                rules={[{ required: true }]}
                extra={t("apps.services.allowedToolsHint")}
              >
                <Select
                  mode="tags"
                  options={tools.map((tool) => ({
                    value: tool.name,
                    label: tool.description
                      ? `${tool.name} — ${tool.description}`
                      : tool.name,
                  }))}
                  tokenSeparators={[","]}
                />
              </Form.Item>
              <Form.Item
                name="allowed_url_prefixes"
                label={t("apps.services.urlPrefixes")}
                extra={t("apps.services.urlPrefixesHint")}
              >
                <Select mode="tags" tokenSeparators={[","]} />
              </Form.Item>
            </>
          )}
          {provider === "extension" && (
            <>
              <Form.Item
                name="base_url"
                label={t("apps.services.extensionBaseUrl")}
                rules={[{ required: true }]}
                extra={t("apps.services.extensionBaseUrlHint")}
              >
                <Input placeholder="https://video.example.com" />
              </Form.Item>
              <Space style={{ display: "flex" }} align="start">
                <Form.Item name="mode" label={t("apps.services.extensionMode")}>
                  <Select
                    style={{ width: 160 }}
                    options={[
                      {
                        value: "write",
                        label: t("apps.services.extensionModeWrite"),
                      },
                      {
                        value: "read",
                        label: t("apps.services.extensionModeRead"),
                      },
                    ]}
                  />
                </Form.Item>
                <Form.Item
                  name="extension_id"
                  label={t("apps.services.extensionId")}
                >
                  <Input placeholder="octop.video" style={{ width: 200 }} />
                </Form.Item>
              </Space>
              <Form.Item
                name="service_token"
                label={t("apps.services.extensionToken")}
                extra={t("apps.services.extensionTokenHint")}
              >
                <Input.Password autoComplete="new-password" />
              </Form.Item>
              <Form.Item
                name="read_tools_text"
                label={t("apps.services.extensionReadTools")}
                extra={t("apps.services.extensionToolsHint")}
              >
                <Input.TextArea rows={7} spellCheck={false} />
              </Form.Item>
              <Form.Item
                name="write_tools_text"
                label={t("apps.services.extensionWriteTools")}
                extra={t("apps.services.extensionToolsHint")}
              >
                <Input.TextArea rows={8} spellCheck={false} />
              </Form.Item>
              <Form.Item
                name="allow_private_network"
                label={t("apps.services.extensionPrivateNetwork")}
                valuePropName="checked"
                extra={t("apps.services.extensionPrivateNetworkHint")}
              >
                <Switch />
              </Form.Item>
            </>
          )}
          {supportsRequestHeaders && (
            <Form.Item
              name="request_headers"
              label={t("apps.services.requestHeaders")}
              extra={t("apps.services.requestHeadersHint")}
            >
              <Input.TextArea
                rows={3}
                placeholder={t("apps.services.requestHeadersPlaceholder")}
              />
            </Form.Item>
          )}
          <Space style={{ display: "flex" }} align="start">
            {provider !== "extension" && (
              <Form.Item
                name="cache_ttl_seconds"
                label={t("apps.services.cacheTtl")}
              >
                <InputNumber min={0} max={86400} style={{ width: 150 }} />
              </Form.Item>
            )}
            <Form.Item
              name="max_calls_per_minute"
              label={t("apps.services.maxCalls")}
            >
              <InputNumber min={1} max={600} style={{ width: 150 }} />
            </Form.Item>
            <Form.Item
              name="enabled"
              label={t("apps.services.enabled")}
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </InfoSection>
  );
}
