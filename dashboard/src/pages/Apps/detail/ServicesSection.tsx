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
} from "../../../api/modules/apps";
import {
  connectorsApi,
  type ConnectorInstance,
} from "../../../api/modules/connectors";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "../../../utils/antdMessage";
import { InfoSection } from "./shared";

const { Text } = Typography;

interface Props {
  appId: string;
  open: boolean;
  canManage: boolean;
}

interface ServiceFormValues {
  name: string;
  provider: "connector" | "external";
  connector_instance_id: string;
  allowed_tools: string[];
  allowed_url_prefixes: string[];
  cache_ttl_seconds: number;
  max_calls_per_minute: number;
  enabled: boolean;
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
    if (!modalOpen || !instanceId) {
      setTools([]);
      return;
    }
    appsApi
      .sourceTools(appId, instanceId)
      .then((data) => setTools(data.tools.filter((tool) => tool.read_only)))
      .catch(() => setTools([]));
  }, [appId, modalOpen, instanceId]);

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue({
      name: "",
      provider: "external",
      connector_instance_id: "",
      allowed_tools: [],
      allowed_url_prefixes: [],
      cache_ttl_seconds: 300,
      max_calls_per_minute: 30,
      enabled: true,
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
      cache_ttl_seconds: service.cache_ttl_seconds,
      max_calls_per_minute: service.max_calls_per_minute,
      enabled: service.enabled,
    });
    setModalOpen(true);
  };

  const save = async (values: ServiceFormValues) => {
    const body: AppServiceInput = {
      provider: values.provider,
      connector_instance_id: values.connector_instance_id,
      allowed_tools: values.allowed_tools,
      allowed_url_prefixes: values.allowed_url_prefixes,
      cache_ttl_seconds: values.cache_ttl_seconds,
      max_calls_per_minute: values.max_calls_per_minute,
      enabled: values.enabled,
    };
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

  const toggle = async (service: AppServiceDeclaration, enabled: boolean) => {
    try {
      const saved = await appsApi.saveService(appId, service.name, {
        provider: service.provider,
        connector_instance_id: service.connector_instance_id,
        allowed_tools: service.allowed_tools,
        allowed_url_prefixes: service.allowed_url_prefixes,
        cache_ttl_seconds: service.cache_ttl_seconds,
        max_calls_per_minute: service.max_calls_per_minute,
        enabled,
      });
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
                  color={service.provider === "external" ? "default" : "purple"}
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
                  {instanceLabel(service.connector_instance_id)}
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
                { value: "external", label: "external" },
                { value: "connector", label: "connector" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="connector_instance_id"
            label={t("apps.services.connector")}
            rules={[{ required: true }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder={t("apps.services.selectConnector")}
              options={instances.map((item) => ({
                value: item.instance_id,
                label: `${item.display_name} (${item.kind})`,
              }))}
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
          <Space style={{ display: "flex" }} align="start">
            <Form.Item
              name="cache_ttl_seconds"
              label={t("apps.services.cacheTtl")}
            >
              <InputNumber min={0} max={86400} style={{ width: 150 }} />
            </Form.Item>
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
