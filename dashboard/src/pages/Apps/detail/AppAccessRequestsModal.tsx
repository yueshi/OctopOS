import { Button, Empty, Modal, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import type { AppAccessRequest } from "../../../api/modules/apps";
import { permissionLabel } from "./shared";

const { Text } = Typography;

interface Props {
  open: boolean;
  requests: AppAccessRequest[];
  busy: boolean;
  onClose: () => void;
  onResolve: (request: AppAccessRequest, approve: boolean) => void;
}

export default function AppAccessRequestsModal({
  open,
  requests,
  busy,
  onClose,
  onResolve,
}: Props) {
  const { t } = useTranslation();

  return (
    <Modal
      title={t("apps.accessRequests.title", "Access requests")}
      open={open}
      onCancel={onClose}
      footer={null}
      destroyOnHidden
    >
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        {requests.length === 0 ? (
          <Empty
            description={t("apps.accessRequests.empty", "No pending requests.")}
          />
        ) : (
          requests.map((request) => (
            <div
              key={request.request_id}
              style={{
                padding: "10px 12px",
                border: "1px solid rgba(5, 5, 5, 0.08)",
                borderRadius: 8,
              }}
            >
              <Space direction="vertical" size={6} style={{ width: "100%" }}>
                <Space size={8} wrap>
                  <Text strong>{request.requester_username || "—"}</Text>
                  <Tag>{permissionLabel(request.needed, t)}</Tag>
                  {request.note && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {request.note}
                    </Text>
                  )}
                </Space>
                <Space size={6}>
                  <Button
                    type="primary"
                    size="small"
                    loading={busy}
                    onClick={() => onResolve(request, true)}
                  >
                    {t("apps.accessRequests.approve", "Approve")}
                  </Button>
                  <Button
                    size="small"
                    danger
                    loading={busy}
                    onClick={() => onResolve(request, false)}
                  >
                    {t("apps.accessRequests.reject", "Reject")}
                  </Button>
                </Space>
              </Space>
            </div>
          ))
        )}
      </Space>
    </Modal>
  );
}
