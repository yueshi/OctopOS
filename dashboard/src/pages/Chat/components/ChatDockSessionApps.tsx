import { useCallback, useState } from "react";
import { Button, Empty, Typography } from "antd";
import {
  AppWindow,
  ChevronLeft,
  ChevronRight,
  Info,
  RefreshCw,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";

import { appPreviewPath, type AppDetail } from "../../../api/modules/apps";
import MiniAppFrame from "../../../components/MiniAppFrame";
import { usePreviewImage } from "../../../hooks/usePreviewImage";
import type { SessionApp } from "../utils/sessionApps";
import SessionAppInfoDrawer from "./SessionAppInfoDrawer";
import styles from "../index.module.less";

const { Text } = Typography;

type SessionAppPatch = Partial<
  Pick<SessionApp, "name" | "description" | "capabilities">
>;

function SessionAppThumb({ app }: { app: SessionApp }) {
  const url = usePreviewImage(
    app.has_preview ? appPreviewPath(app.app_id) : null,
  );
  if (!url) {
    return (
      <span className={styles.sessionAppThumbEmpty}>
        <AppWindow size={18} strokeWidth={1.4} />
      </span>
    );
  }
  return (
    <img
      className={styles.sessionAppThumb}
      src={url}
      alt={app.name}
      draggable={false}
    />
  );
}

export interface ChatDockSessionAppsProps {
  apps: readonly SessionApp[];
  /** Called when a row is chosen, before its MiniApp opens in the panel. */
  onSelectApp?: (app: SessionApp) => void;
  /**
   * Host override for `octopApp.composeMessage`. Defaults to navigating to the
   * chat route; desktop windows fill their own composer instead.
   */
  onComposeMessage?: (text: string) => void;
  /** Host override for `octopApp.openExternal` (desktop web windows). */
  onOpenExternal?: (url: string) => void;
  /** False while the hosting desktop window is hidden or minimized. */
  active?: boolean;
}

/**
 * MiniApps created in the open conversation, shown in the chat dock.
 *
 * The session has no server-side app list, so the list is derived from the
 * `app_card` tool results in the transcript (see `collectSessionApps`).
 * Selecting one runs it inside the panel; the back button returns to the list.
 */
export default function ChatDockSessionApps({
  apps,
  onSelectApp,
  onComposeMessage,
  onOpenExternal,
  active = true,
}: ChatDockSessionAppsProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [infoOpen, setInfoOpen] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [patches, setPatches] = useState<Record<string, SessionAppPatch>>({});
  const withPatch = (app: SessionApp): SessionApp => {
    const patch = patches[app.app_id];
    return patch ? { ...app, ...patch } : app;
  };
  const selectedBase = apps.find((app) => app.app_id === selectedId) ?? null;
  const selected = selectedBase ? withPatch(selectedBase) : null;

  const prefillViaChat = useCallback(
    (text: string) => {
      navigate(location.pathname, { state: { prefillInput: text } });
    },
    [location.pathname, navigate],
  );

  const handleAppUpdated = useCallback((detail: AppDetail) => {
    setPatches((prev) => ({
      ...prev,
      [detail.app_id]: {
        name: detail.name,
        description: detail.description,
        capabilities: detail.capabilities,
      },
    }));
  }, []);

  if (apps.length === 0) {
    return (
      <div className={styles.sessionAppsEmpty}>
        <Empty
          image={<AppWindow size={40} strokeWidth={1.2} opacity={0.45} />}
          description={
            <Text type="secondary">
              {t(
                "chat.sessionAppsEmpty",
                "No MiniApp in this conversation yet.",
              )}
            </Text>
          }
        />
      </div>
    );
  }

  if (selected) {
    const infoLabel = t("apps.info", "Info");
    return (
      <div className={styles.sessionAppsOpen}>
        <div className={styles.sessionAppsBar}>
          <Button
            type="text"
            size="small"
            icon={<ChevronLeft size={16} />}
            onClick={() => {
              setInfoOpen(false);
              setSelectedId(null);
            }}
          >
            {t("chat.sessionAppsBack", "Back to list")}
          </Button>
          <Text strong ellipsis={{ tooltip: selected.name }}>
            {selected.name}
          </Text>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            className={styles.sessionAppsRefreshBtn}
            aria-label={t("chat.sessionAppsReload", "Reload MiniApp")}
            title={t("chat.sessionAppsReload", "Reload MiniApp")}
            onClick={() => setReloadToken((value) => value + 1)}
          >
            <RefreshCw size={15} strokeWidth={2.2} aria-hidden />
          </button>
          <button
            type="button"
            className={styles.sessionAppsInfoBtn}
            aria-label={infoLabel}
            title={infoLabel}
            onClick={() => setInfoOpen(true)}
          >
            <Info size={16} strokeWidth={2.2} aria-hidden />
          </button>
        </div>
        <div className={styles.sessionAppsFrame}>
          <MiniAppFrame
            appId={selected.app_id}
            title={selected.name}
            capabilities={selected.capabilities}
            active={active}
            foreground={active}
            reloadToken={reloadToken}
            onComposeMessage={onComposeMessage ?? prefillViaChat}
            onOpenExternal={onOpenExternal}
            fill
          />
        </div>
        <SessionAppInfoDrawer
          open={infoOpen}
          appId={selected.app_id}
          onClose={() => setInfoOpen(false)}
          onUpdated={handleAppUpdated}
        />
      </div>
    );
  }

  return (
    <div className={styles.sessionAppsList}>
      {apps.map((app) => {
        const shown = withPatch(app);
        return (
          <button
            key={app.app_id}
            type="button"
            className={styles.sessionAppsRow}
            onClick={() => {
              onSelectApp?.(app);
              setSelectedId(app.app_id);
            }}
          >
            <SessionAppThumb app={shown} />
            <span className={styles.sessionAppsRowMain}>
              <Text strong ellipsis={{ tooltip: shown.name }}>
                {shown.name}
              </Text>
              <Text type="secondary" className={styles.sessionAppsRowMeta}>
                {typeof shown.row_count === "number"
                  ? t("apps.rows", "{{count}} rows", {
                      count: shown.row_count,
                    })
                  : shown.description || ""}
              </Text>
            </span>
            <ChevronRight
              size={16}
              className={styles.sessionAppsRowChevron}
              aria-hidden
            />
          </button>
        );
      })}
    </div>
  );
}
