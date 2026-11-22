import { useEffect, useMemo, useState } from "react";
import { Alert, Form, Modal, Select, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  appsApi,
  type AppColumn,
  type AppTaskType,
} from "../../../api/modules/apps";
import { message } from "../../../utils/antdMessage";
import { apiErrorMessage } from "../../../utils/apiError";

interface Props {
  open: boolean;
  appId: string;
  tableKey: string;
  rowId: string;
  columns: AppColumn[];
  onClose: () => void;
  onCreated: () => void;
}

export default function RunTaskModal({
  open,
  appId,
  tableKey,
  rowId,
  columns,
  onClose,
  onCreated,
}: Props) {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [types, setTypes] = useState<AppTaskType[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [failed, setFailed] = useState(false);
  const selectedType = Form.useWatch<string>("task_type", form);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    setFailed(false);
    appsApi
      .listTaskTypes(appId)
      .then((rows) => {
        if (!alive) return;
        setTypes(rows);
        if (rows.length > 0) form.setFieldValue("task_type", rows[0].name);
      })
      .catch(() => {
        if (alive) setFailed(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [appId, form, open]);

  const writableColumns = useMemo(
    () => columns.filter((column) => column.type === "string" && !column.pii),
    [columns],
  );
  const selected = types.find((item) => item.name === selectedType);
  const supportsWrite = selected?.supports_write !== false;
  const typeLabel = (item: AppTaskType) => {
    if (item.name === "summarize_row") return t("apps.taskTypeSummarize");
    if (item.name === "table_digest") return t("apps.taskTypeDigest");
    return item.label;
  };

  const submit = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      await appsApi.createTask(appId, {
        task_type: String(values.task_type),
        table: tableKey,
        row_id: rowId,
        input: values.write_column ? { write_column: values.write_column } : {},
      });
      message.success(t("apps.runTaskCreated"));
      form.resetFields();
      onCreated();
      onClose();
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.runTaskFailed"), t));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t("apps.runTaskTitle")}
      okText={t("apps.runTaskSubmit")}
      cancelText={t("common.cancel")}
      confirmLoading={submitting}
      okButtonProps={{ disabled: loading || types.length === 0 }}
      onOk={() => void submit()}
      onCancel={() => {
        form.resetFields();
        onClose();
      }}
    >
      <Typography.Paragraph type="secondary">
        {t("apps.runTaskHint")}
      </Typography.Paragraph>
      {failed ? (
        <Alert type="error" showIcon message={t("apps.runTaskLoadFailed")} />
      ) : null}
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          name="task_type"
          label={t("apps.runTaskType")}
          rules={[{ required: true, message: t("apps.runTaskTypeRequired") }]}
        >
          <Select
            loading={loading}
            options={types.map((item) => ({
              value: item.name,
              label: `${typeLabel(item)} · Tier ${item.tier}`,
            }))}
          />
        </Form.Item>
        <Form.Item name="write_column" label={t("apps.runTaskWriteColumn")}>
          <Select
            allowClear
            disabled={!supportsWrite}
            placeholder={t("apps.runTaskWriteColumnNone")}
            options={writableColumns.map((column) => ({
              value: column.name,
              label: column.label,
            }))}
          />
        </Form.Item>
        <Typography.Text type="secondary">
          {t("apps.runTaskRow", { row: rowId })}
        </Typography.Text>
      </Form>
    </Modal>
  );
}
