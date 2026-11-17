import { useEffect, useState, type ReactNode } from "react";
import { Empty, Segmented, Spin } from "antd";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { getApiUrl } from "../../api/config";
import {
  workspaceApi,
  type HtmlPreviewSession,
} from "../../api/modules/workspace";
import styles from "./index.module.less";

export interface DesktopHtmlViewProps {
  agentId: string;
  path: string;
  fromWorkspace: boolean;
  /** Bump to reload the preview (and the source). */
  refreshToken?: number;
  /** Extra toolbar actions rendered before the refresh button. */
  actions?: ReactNode;
  /** Hide the path label when the parent window chrome already shows it. */
  hidePath?: boolean;
  frameTestId?: string;
  className?: string;
}

/**
 * Sandboxed HTML renderer shared by the Files window and standalone HTML
 * windows. Workspace files use the short-lived capability URL; host-absolute
 * files fall back to `srcDoc`.
 */
export default function DesktopHtmlView({
  agentId,
  path,
  fromWorkspace,
  refreshToken = 0,
  actions,
  hidePath = false,
  frameTestId = "desktop-html-frame",
  className = "",
}: DesktopHtmlViewProps) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"preview" | "source">("preview");
  const [session, setSession] = useState<HtmlPreviewSession | null>(null);
  const [srcDoc, setSrcDoc] = useState("");
  const [source, setSource] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(false);
    setSession(null);
    setSrcDoc("");
    setSource("");
    const load = async () => {
      if (fromWorkspace) {
        try {
          const next = await workspaceApi.createHtmlPreviewSession(
            agentId,
            path,
          );
          if (!alive) return;
          if (next.entry) {
            setSession(next);
            return;
          }
        } catch {
          // Fall through to srcDoc; a capability URL needs a validated file.
        }
      }
      try {
        const row = await workspaceApi.readAgentFile(
          agentId,
          path,
          fromWorkspace,
        );
        if (alive) setSrcDoc(row.content);
      } catch {
        if (alive) setError(true);
      }
    };
    void load().finally(() => {
      if (alive) setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [agentId, fromWorkspace, path, refreshToken, reloadKey]);

  useEffect(() => {
    if (mode !== "source" || source) return;
    let alive = true;
    void workspaceApi
      .readAgentFile(agentId, path, fromWorkspace)
      .then((row) => {
        if (alive) setSource(row.content);
      })
      .catch(() => {
        if (alive) setError(true);
      });
    return () => {
      alive = false;
    };
  }, [agentId, fromWorkspace, mode, path, source]);

  const iframeSrc = session
    ? getApiUrl(session.base_path + encodeURIComponent(session.entry))
    : null;

  return (
    <div
      className={[styles.desktopFilesViewerPane, className]
        .filter(Boolean)
        .join(" ")}
    >
      <div className={styles.desktopFilesViewerBar}>
        {!hidePath && (
          <span className={styles.desktopFilesViewerPath} title={path}>
            {path}
          </span>
        )}
        {actions}
        <Segmented
          size="small"
          value={mode}
          options={[
            {
              label: t("apps.desktop.files.preview", "Preview"),
              value: "preview",
            },
            {
              label: t("apps.desktop.files.source", "Source"),
              value: "source",
            },
          ]}
          onChange={(value) => setMode(value as "preview" | "source")}
        />
        <button
          type="button"
          className={styles.desktopFilesIconButton}
          aria-label={t("apps.desktop.files.refresh", "Refresh")}
          onClick={() => setReloadKey((value) => value + 1)}
        >
          <RefreshCw size={14} />
        </button>
      </div>
      <div className={styles.desktopFilesViewerBody}>
        {mode === "source" ? (
          source ? (
            <pre className={styles.desktopFilesSource}>{source}</pre>
          ) : error ? (
            <Empty
              className={styles.desktopFilesCenter}
              description={t(
                "apps.desktop.files.readFailed",
                "Could not read this file.",
              )}
            />
          ) : (
            <div className={styles.desktopFilesCenter}>
              <Spin />
            </div>
          )
        ) : loading ? (
          <div className={styles.desktopFilesCenter}>
            <Spin />
          </div>
        ) : error ? (
          <Empty
            className={styles.desktopFilesCenter}
            description={t(
              "apps.desktop.files.previewFailed",
              "HTML preview is unavailable. Use Source or download the file.",
            )}
          />
        ) : iframeSrc || srcDoc ? (
          <iframe
            key={`${iframeSrc ?? "srcdoc"}:${reloadKey}:${refreshToken}`}
            data-testid={frameTestId}
            title={t("apps.desktop.files.preview", "Preview")}
            className={styles.desktopFilesHtmlFrame}
            src={iframeSrc ?? undefined}
            srcDoc={iframeSrc ? undefined : srcDoc}
            sandbox="allow-scripts allow-forms"
            referrerPolicy="no-referrer"
            onLoad={() => setLoading(false)}
          />
        ) : (
          <Empty
            className={styles.desktopFilesCenter}
            description={t("workspace.emptyFile", "This file is empty")}
          />
        )}
      </div>
    </div>
  );
}
