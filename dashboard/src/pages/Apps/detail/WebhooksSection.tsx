import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Form,
  Input,
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
  type AppWebhook,
  type AppWebhookInput,
} from "../../../api/modules/apps";
import { message } from "../../../utils/antdMessage";
import { apiErrorMessage } from "../../../utils/apiError";
import { InfoSection } from "./shared";

const { Text } = Typography;

interface Props {
  appId: string;
  open: boolean;
  canManage: boolean;
}

interface WebhookFormValues {
  name: string;
  url: string;
  event_types: string[];
  enabled: boolean;
  rotate_secret: boolean;
}

export default function WebhooksSection({ appId, open, canManage }: Props) {
  const { t } = useTranslation();
  const [webhooks, setWebhooks] = useState<AppWebhook[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<AppWebhook | null>(null);
  const [saving, setSaving] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [form] = Form.useForm<WebhookFormValues>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setWebhooks(await appsApi.listWebhooks(appId));
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.webhooks.loadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [appId, t]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const openCreate = () => {
    setEditing(null);
    setSecret(null);
    form.setFieldsValue({
      name: "",
      url: "",
      event_types: ["*"],
      enabled: true,
      rotate_secret: false,
    });
    setModalOpen(true);
  };

  const openEdit = (webhook: AppWebhook) => {
    setEditing(webhook);
    setSecret(null);
    form.setFieldsValue({
      name: webhook.name,
      url: webhook.url,
      event_types: webhook.event_types,
      enabled: webhook.enabled,
      rotate_secret: false,
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    const input: AppWebhookInput = {
      name: values.name.trim(),
      url: values.url.trim(),
      event_types: values.event_types,
      enabled: values.enabled,
    };
    setSaving(true);
    try {
      if (editing) {
        const updated = await appsApi.updateWebhook(appId, editing.webhook_id, {
          ...input,
          rotate_secret: values.rotate_secret,
        });
        setSecret(updated.secret);
        message.success(t("apps.webhooks.saved"));
      } else {
        const created = await appsApi.createWebhook(appId, input);
        setSecret(created.secret);
        message.success(t("apps.webhooks.created"));
      }
      setModalOpen(false);
      setEditing(null);
      await load();
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.webhooks.saveFailed"), t));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (webhook: AppWebhook) => {
    try {
      await appsApi.deleteWebhook(appId, webhook.webhook_id);
      message.success(t("apps.webhooks.deleted"));
      await load();
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.webhooks.deleteFailed"), t));
    }
  };

  return (
    <InfoSection
      title={t("apps.webhooks.title")}
      hint={t("apps.webhooks.hint")}
    >
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        {secret && (
          <Alert
            type="success"
            showIcon
            message={t("apps.webhooks.secretTitle")}
            description={
              <Space direction="vertical" size={6} style={{ width: "100%" }}>
                <Text type="secondary">{t("apps.webhooks.secretHint")}</Text>
                <Text code copyable={{ text: secret }}>
                  {secret}
                </Text>
                <Button size="small" onClick={() => setSecret(null)}>
                  {t("apps.webhooks.secretDone")}
                </Button>
              </Space>
            }
          />
        )}
        {loading ? (
          <Spin />
        ) : webhooks.length === 0 ? (
          <Text type="secondary">{t("apps.webhooks.empty")}</Text>
        ) : (
          webhooks.map((webhook) => (
            <div
              key={webhook.webhook_id}
              style={{
                padding: "10px 12px",
                border: "1px solid rgba(5, 5, 5, 0.06)",
                borderRadius: 8,
                background: "#fafafa",
              }}
            >
              <Space size={8} wrap>
                <Text strong>{webhook.name}</Text>
                <Tag color={webhook.enabled ? "green" : "default"}>
                  {webhook.enabled
                    ? t("apps.webhooks.enabled")
                    : t("apps.webhooks.disabled")}
                </Tag>
                {webhook.event_types.map((type) => (
                  <Tag key={type}>{type}</Tag>
                ))}
              </Space>
              <div style={{ marginTop: 4 }}>
                <Text
                  type="secondary"
                  style={{ fontSize: 12, wordBreak: "break-all" }}
                >
                  {webhook.url}
                </Text>
              </div>
              {canManage && (
                <Space size={4} style={{ marginTop: 6 }}>
                  <Button
                    size="small"
                    type="link"
                    icon={<Pencil size={14} />}
                    onClick={() => openEdit(webhook)}
                  >
                    {t("apps.webhooks.edit")}
                  </Button>
                  <Popconfirm
                    title={t("apps.webhooks.deleteConfirm")}
                    okText={t("common.delete")}
                    cancelText={t("common.cancel")}
                    okButtonProps={{ danger: true }}
                    onConfirm={() => void remove(webhook)}
                  >
                    <Button
                      size="small"
                      type="link"
                      danger
                      icon={<Trash2 size={14} />}
                    >
                      {t("apps.webhooks.delete")}
                    </Button>
                  </Popconfirm>
                </Space>
              )}
            </div>
          ))
        )}
        {canManage && (
          <Button size="small" icon={<Plus size={14} />} onClick={openCreate}>
            {t("apps.webhooks.add")}
          </Button>
        )}
      </Space>

      <Modal
        title={
          editing
            ? t("apps.webhooks.editTitle")
            : t("apps.webhooks.createTitle")
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
            label={t("apps.webhooks.name")}
            rules={[{ required: true, message: t("apps.requiredField") }]}
          >
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item
            name="url"
            label={t("apps.webhooks.url")}
            rules={[{ required: true, message: t("apps.requiredField") }]}
          >
            <Input placeholder="https://example.com/octop/hook" />
          </Form.Item>
          <Form.Item
            name="event_types"
            label={t("apps.webhooks.eventTypes")}
            extra={t("apps.webhooks.eventTypesHint")}
            rules={[{ required: true, message: t("apps.requiredField") }]}
          >
            <Select mode="tags" tokenSeparators={[",", " "]} />
          </Form.Item>
          <Form.Item
            name="enabled"
            label={t("apps.webhooks.enabled")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          {editing && (
            <Form.Item
              name="rotate_secret"
              label={t("apps.webhooks.rotateSecret")}
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </InfoSection>
  );
}
