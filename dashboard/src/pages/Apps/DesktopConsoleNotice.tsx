import { memo } from "react";
import { ExternalLink, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import styles from "./index.module.less";

interface DesktopConsoleNoticeProps {
  onOpenExternal: () => void;
  onLoadAnyway: () => void;
}

/**
 * Shown instead of embedding the dashboard in one of its own web windows.
 * A second console SPA (plus a desktop restore, on `/apps/desktop`) is the
 * heaviest page a window can host, so loading it is opt-in.
 */
function DesktopConsoleNotice({
  onOpenExternal,
  onLoadAnyway,
}: DesktopConsoleNoticeProps) {
  const { t } = useTranslation();

  return (
    <div
      className={styles.desktopConsoleNotice}
      data-testid="desktop-console-notice"
    >
      <span className={styles.desktopConsoleNoticeIcon} aria-hidden>
        <ShieldAlert size={28} strokeWidth={1.5} />
      </span>
      <strong>
        {t(
          "apps.desktop.consoleNotice.title",
          "This is the Octop console itself",
        )}
      </strong>
      <p>
        {t(
          "apps.desktop.consoleNotice.body",
          "Opening it inside the desktop runs a second full copy of the app — with its own polls and windows — and can use a lot of memory.",
        )}
      </p>
      <div className={styles.desktopConsoleNoticeActions}>
        <button
          type="button"
          className={styles.desktopConsoleNoticePrimary}
          onClick={onOpenExternal}
        >
          <ExternalLink size={13} />
          {t(
            "apps.desktop.consoleNotice.openBrowser",
            "Open in system browser",
          )}
        </button>
        <button
          type="button"
          className={styles.desktopConsoleNoticeSecondary}
          onClick={onLoadAnyway}
        >
          {t("apps.desktop.consoleNotice.loadAnyway", "Load here anyway")}
        </button>
      </div>
    </div>
  );
}

export default memo(DesktopConsoleNotice);
