import type { RefObject } from "react";
import { Button, Empty, Spin, Typography } from "antd";
import { MessageSquareText } from "lucide-react";
import { useTranslation } from "react-i18next";

const { Text } = Typography;

interface Props {
  loading: boolean;
  escaped: boolean;
  blocked: boolean;
  hasHtml: boolean;
  html: string;
  reloadKey: number;
  frameRef: RefObject<HTMLIFrameElement>;
  srcDoc: string;
  onOpenChat: () => void;
}

export default function AppInterfaceTab({
  loading,
  escaped,
  blocked,
  hasHtml,
  html,
  reloadKey,
  frameRef,
  srcDoc,
  onOpenChat,
}: Props) {
  const { t } = useTranslation();

  return (
    <div
      style={{
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {loading ? (
        <div style={{ padding: 48, textAlign: "center" }}>
          <Spin />
        </div>
      ) : escaped ? (
        <div style={{ padding: 24 }}>
          <Text type="warning">
            {t(
              "apps.sandboxEscaped",
              "This MiniApp tried to leave its sandbox, so it was stopped.",
            )}
          </Text>
        </div>
      ) : blocked ? (
        <div style={{ padding: 24 }}>
          <Text type="warning">
            {t(
              "apps.sandboxEmbeddedFrame",
              "This MiniApp embeds a frame, which is not allowed. Regenerate it.",
            )}
          </Text>
        </div>
      ) : hasHtml && html ? (
        <iframe
          key={reloadKey}
          ref={frameRef}
          title={t("apps.iframeTitle")}
          sandbox="allow-scripts"
          srcDoc={srcDoc}
          style={{
            flex: 1,
            width: "100%",
            minHeight: 0,
            border: 0,
            borderRadius: 8,
            background: "#fff",
          }}
        />
      ) : (
        <Empty
          image={
            <MessageSquareText size={48} strokeWidth={1.2} opacity={0.45} />
          }
          description={t("apps.noHtml")}
          style={{ padding: "64px 0" }}
        >
          <Button type="primary" onClick={onOpenChat}>
            {t("apps.openChat")}
          </Button>
        </Empty>
      )}
    </div>
  );
}
