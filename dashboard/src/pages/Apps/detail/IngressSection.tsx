import { useCallback, useEffect, useState } from "react";
import {
  Alert,
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
import { FlaskConical, KeyRound, Pencil, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  appsApi,
  type AppIngress,
  type AppIngressInput,
} from "../../../api/modules/apps";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "../../../utils/antdMessage";
import { InfoSection } from "./shared";

const { Text } = Typography;

const MODES = ["app.inbox", "app.import", "app.refresh"] as const;

interface Props {
  appId: string;
  open: boolean;
  canManage: boolean;
}

interface IngressFormValues {
  name: string;
  mode: AppIngress["mode"];
  event_type: string;
  mapping_text: string;
  max_per_minute: number;
  enabled: boolean;
}

interface SecretInfo {
  hookUrl: string;
  secret: string;
}

export default function IngressSection({ appId, open, canManage }: Props) {
  const { t } = useTranslation();
  const [items, setItems] = useState<AppIngress[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<AppIngress | null>(null);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState("");
  const [secretInfo, setSecretInfo] = useState<SecretInfo | null>(null);
  const [form] = Form.useForm<IngressFormValues>();
  const mode = Form.useWatch("mode", form) ?? "app.inbox";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await appsApi.listIngress(appId));
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.ingress.loadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [appId, t]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const openCreate = () => {
    setEditing(null);
    setSecretInfo(null);
    form.setFieldsValue({
      name: "",
      mode: "app.inbox",
      event_type: "",
      mapping_text: "{}",
      max_per_minute: 0,
      enabled: true,
    });
    setModalOpen(true);
  };

  const openEdit = (item: AppIngress) => {
    setEditing(item);
    setSecretInfo(null);
    form.setFieldsValue({
      name: item.name,
      mode: item.mode,
      event_type: item.event_type,
      mapping_text: JSON.stringify(item.mapping ?? {}, null, 2),
      max_per_minute: item.max_per_minute,
      enabled: item.enabled,
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    let mapping: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(values.mapping_text || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("mapping must be an object");
      }
      mapping = parsed as Record<string, unknown>;
    } catch {
      message.error(t("apps.ingress.mappingInvalid"));
      return;
    }
    const input: AppIngressInput = {
      name: values.name.trim(),
      mode: values.mode,
      event_type: values.event_type.trim(),
      mapping,
      max_per_minute: values.max_per_minute ?? 0,
      enabled: values.enabled,
    };
    setSaving(true);
    try {
      if (editing) {
        await appsApi.updateIngress(appId, editing.ingress_id, input);
        message.success(t("apps.ingress.saved"));
      } else {
        const created = await appsApi.createIngress(appId, input);
        setSecretInfo({ hookUrl: created.hook_url, secret: created.secret });
        message.success(t("apps.ingress.created"));
      }
      setModalOpen(false);
      setEditing(null);
      await load();
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.ingress.saveFailed"), t));
    } finally {
      setSaving(false);
    }
  };

  const rotate = async (item: AppIngress) => {
    try {
      const result = await appsApi.rotateIngressSecret(appId, item.ingress_id);
      setSecretInfo({ hookUrl: result.hook_url, secret: result.secret });
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.ingress.rotateFailed"), t));
    }
  };

  const remove = async (item: AppIngress) => {
    try {
      await appsApi.deleteIngress(appId, item.ingress_id);
      message.success(t("apps.ingress.deleted"));
      await load();
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.ingress.deleteFailed"), t));
    }
  };

  const test = async (item: AppIngress) => {
    setTestingId(item.ingress_id);
    try {
      const result = await appsApi.testIngress(appId, item.ingress_id);
      message.success(
        t("apps.ingress.testSent", {
          event: result.event_type,
          deliveries: result.deliveries,
        }),
      );
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.ingress.testFailed"), t));
    } finally {
      setTestingId("");
    }
  };

  return (
    <InfoSection title={t("apps.ingress.title")} hint={t("apps.ingress.hint")}>
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        {secretInfo && (
          <Alert
            type="success"
            showIcon
            message={t("apps.ingress.secretTitle")}
            description={
              <Space direction="vertical" size={6} style={{ width: "100%" }}>
                <Text type="secondary">{t("apps.ingress.secretHint")}</Text>
                <Text code copyable={{ text: secretInfo.hookUrl }}>
                  {secretInfo.hookUrl}
                </Text>
                <Text code copyable={{ text: secretInfo.secret }}>
                  {secretInfo.secret}
                </Text>
                <Button size="small" onClick={() => setSecretInfo(null)}>
                  {t("apps.ingress.secretDone")}
                </Button>
              </Space>
            }
          />
        )}
        {loading ? (
          <Spin />
        ) : items.length === 0 ? (
          <Text type="secondary">{t("apps.ingress.empty")}</Text>
        ) : (
          items.map((item) => (
            <div
              key={item.ingress_id}
              style={{
                padding: "10px 12px",
                border: "1px solid rgba(5, 5, 5, 0.06)",
                borderRadius: 8,
                background: "#fafafa",
              }}
            >
              <Space size={8} wrap>
                <Text strong>{item.name}</Text>
                <Tag color="blue">{item.mode}</Tag>
                <Tag color={item.enabled ? "green" : "default"}>
                  {item.enabled
                    ? t("apps.ingress.enabled")
                    : t("apps.ingress.disabled")}
                </Tag>
                {item.event_type ? <Tag>{item.event_type}</Tag> : null}
                {item.max_per_minute ? (
                  <Tag>
                    {t("apps.ingress.perMinute", {
                      count: item.max_per_minute,
                    })}
                  </Tag>
                ) : null}
              </Space>
              <div style={{ marginTop: 4 }}>
                <Text
                  type="secondary"
                  style={{ fontSize: 12, wordBreak: "break-all" }}
                >
                  {item.hook_url}
                </Text>
              </div>
              {canManage && (
                <Space size={4} style={{ marginTop: 6 }}>
                  <Button
                    size="small"
                    type="link"
                    icon={<FlaskConical size={14} />}
                    loading={testingId === item.ingress_id}
                    onClick={() => void test(item)}
                  >
                    {t("apps.ingress.test")}
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    icon={<KeyRound size={14} />}
                    onClick={() => void rotate(item)}
                  >
                    {t("apps.ingress.rotate")}
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    icon={<Pencil size={14} />}
                    onClick={() => openEdit(item)}
                  >
                    {t("apps.ingress.edit")}
                  </Button>
                  <Popconfirm
                    title={t("apps.ingress.deleteConfirm")}
                    okText={t("common.delete")}
                    cancelText={t("common.cancel")}
                    okButtonProps={{ danger: true }}
                    onConfirm={() => void remove(item)}
                  >
                    <Button
                      size="small"
                      type="link"
                      danger
                      icon={<Trash2 size={14} />}
                    >
                      {t("apps.ingress.delete")}
                    </Button>
                  </Popconfirm>
                </Space>
              )}
            </div>
          ))
        )}
        {canManage && (
          <Button size="small" icon={<Plus size={14} />} onClick={openCreate}>
            {t("apps.ingress.add")}
          </Button>
        )}
      </Space>

      <Modal
        title={
          editing ? t("apps.ingress.editTitle") : t("apps.ingress.createTitle")
        }
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => void submit()}
        okText={t("apps.save")}
        cancelText={t("common.cancel")}
        confirmLoading={saving}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label={t("apps.ingress.name")}
            rules={[{ required: true, message: t("apps.requiredField") }]}
          >
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item
            name="mode"
            label={t("apps.ingress.mode")}
            rules={[{ required: true, message: t("apps.requiredField") }]}
          >
            <Select
              options={MODES.map((value) => ({
                value,
                label: t(`apps.ingress.modeLabels.${value.split(".")[1]}`),
              }))}
            />
          </Form.Item>
          <Form.Item
            name="event_type"
            label={t("apps.ingress.eventType")}
            extra={t("apps.ingress.eventTypeHint")}
          >
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item
            name="mapping_text"
            label={t("apps.ingress.mapping")}
            extra={t("apps.ingress.mappingHint", { mode })}
          >
            <Input.TextArea autoSize={{ minRows: 3, maxRows: 8 }} />
          </Form.Item>
          <Form.Item
            name="max_per_minute"
            label={t("apps.ingress.maxPerMinute")}
          >
            <InputNumber min={0} max={600} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="enabled"
            label={t("apps.ingress.enabled")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </InfoSection>
  );
}
