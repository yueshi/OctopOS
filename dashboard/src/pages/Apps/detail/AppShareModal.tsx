import {
  Alert,
  Button,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Typography,
} from "antd";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AppColumn } from "../../../api/modules/apps";
import type { ShareDraft } from "./types";

const { Text } = Typography;

interface Props {
  open: boolean;
  appName: string;
  columns: AppColumn[];
  shares: ShareDraft[];
  loading: boolean;
  saving: boolean;
  onChange: (shares: ShareDraft[]) => void;
  onClose: () => void;
  onSave: () => void;
}

export default function AppShareModal({
  open,
  appName,
  columns,
  shares,
  loading,
  saving,
  onChange,
  onClose,
  onSave,
}: Props) {
  const { t } = useTranslation();

  return (
    <Modal
      title={t("apps.shareTitle", { name: appName })}
      open={open}
      onCancel={onClose}
      onOk={onSave}
      okText={t("apps.shareSave")}
      cancelText={t("common.cancel")}
      confirmLoading={saving}
      destroyOnHidden
    >
      <Text type="secondary">{t("apps.shareHint")}</Text>
      {columns.some((column) => column.pii) && (
        <Alert
          type="warning"
          showIcon
          message={t("apps.piiShareWarning")}
          style={{ marginTop: 12 }}
        />
      )}
      <div style={{ marginTop: 12 }}>
        {loading ? (
          <div style={{ padding: 24, textAlign: "center" }}>
            <Spin />
          </div>
        ) : (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            {shares.length === 0 && (
              <Text type="secondary">{t("apps.shareEmpty")}</Text>
            )}
            {shares.map((item, index) => (
              <Space.Compact key={index} style={{ width: "100%" }}>
                <Input
                  placeholder={t("apps.shareUser")}
                  value={item.username}
                  onChange={(event) => {
                    const next = [...shares];
                    next[index] = {
                      ...next[index],
                      username: event.target.value,
                    };
                    onChange(next);
                  }}
                />
                <Select
                  style={{ width: 190 }}
                  value={item.permission}
                  options={[
                    { value: "view", label: t("apps.permissionView") },
                    {
                      value: "edit_data",
                      label: t("apps.permissionEditData"),
                    },
                    {
                      value: "edit_structure",
                      label: t("apps.permissionEditStructure"),
                    },
                  ]}
                  onChange={(permission) => {
                    const next = [...shares];
                    next[index] = { ...next[index], permission };
                    onChange(next);
                  }}
                />
                <Button
                  danger
                  icon={<Trash2 size={14} />}
                  onClick={() => onChange(shares.filter((_, i) => i !== index))}
                />
              </Space.Compact>
            ))}
            <Button
              type="dashed"
              icon={<Plus size={14} />}
              onClick={() =>
                onChange([...shares, { username: "", permission: "view" }])
              }
              block
            >
              {t("apps.shareAdd")}
            </Button>
          </Space>
        )}
      </div>
    </Modal>
  );
}
