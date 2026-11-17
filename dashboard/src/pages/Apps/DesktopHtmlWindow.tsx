import { memo, useCallback } from "react";
import { Tooltip } from "antd";
import { ArrowDownToLine, FolderOpen } from "lucide-react";
import { useTranslation } from "react-i18next";

import { workspaceApi } from "../../api/modules/workspace";
import { message } from "../../utils/antdMessage";
import { saveBlobAsFile } from "../../utils/saveBlobAsFile";
import DesktopHtmlView from "./DesktopHtmlView";
import { desktopFileBasename } from "./desktopFiles";
import styles from "./index.module.less";

interface DesktopHtmlWindowProps {
  agentId: string;
  path: string;
  fromWorkspace: boolean;
  title?: string;
  onOpenFiles?: (agentId: string, path: string, fromWorkspace: boolean) => void;
}

/** A standalone sandboxed HTML page living in its own desktop window. */
function DesktopHtmlWindow({
  agentId,
  path,
  fromWorkspace,
  title,
  onOpenFiles,
}: DesktopHtmlWindowProps) {
  const { t } = useTranslation();
  const name = title || desktopFileBasename(path);

  const download = useCallback(async () => {
    try {
      const blob = await workspaceApi.downloadAgentFile(
        agentId,
        path,
        fromWorkspace,
      );
      await saveBlobAsFile(blob, name);
    } catch {
      message.error(t("apps.desktop.files.downloadFailed", "Download failed"));
    }
  }, [agentId, fromWorkspace, name, path, t]);

  return (
    <div className={styles.desktopHtmlWindow} data-testid="desktop-html-window">
      <DesktopHtmlView
        agentId={agentId}
        path={path}
        fromWorkspace={fromWorkspace}
        actions={
          <>
            {onOpenFiles && (
              <Tooltip title={t("apps.desktop.files.backToFiles", "Files")}>
                <button
                  type="button"
                  className={styles.desktopFilesIconButton}
                  aria-label={t("apps.desktop.files.backToFiles", "Files")}
                  onClick={() => onOpenFiles(agentId, path, fromWorkspace)}
                >
                  <FolderOpen size={14} />
                </button>
              </Tooltip>
            )}
            <Tooltip title={t("apps.desktop.files.download", "Download")}>
              <button
                type="button"
                className={styles.desktopFilesIconButton}
                aria-label={t("apps.desktop.files.download", "Download")}
                onClick={() => void download()}
              >
                <ArrowDownToLine size={14} />
              </button>
            </Tooltip>
          </>
        }
        frameTestId="desktop-html-window-frame"
      />
    </div>
  );
}

export default memo(DesktopHtmlWindow);
