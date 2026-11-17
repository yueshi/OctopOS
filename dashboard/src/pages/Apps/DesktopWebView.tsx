import { memo, useEffect, useState } from "react";
import {
  AlertTriangle,
  ChevronLeft,
  Circle,
  ExternalLink,
  MoreHorizontal,
  RefreshCw,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import DesktopConsoleNotice from "./DesktopConsoleNotice";
import { isDashboardConsoleUrl } from "./desktopWindows";
import styles from "./index.module.less";

interface DesktopWebViewProps {
  windowId: string;
  url: string;
  title?: string | null;
  /** Return to the MiniApp desktop, mirroring WeChat's web-view back action. */
  onClose?: (windowId: string) => void;
}

function splitUrl(url: string): { host: string; path: string } {
  try {
    const parsed = new URL(url);
    return {
      host: parsed.host,
      path: `${parsed.pathname}${parsed.search}${parsed.hash}`,
    };
  } catch {
    return { host: "", path: url };
  }
}

/**
 * External pages follow WeChat Mini Program web-view conventions: a compact
 * navigation bar with a back affordance, centred page title and the familiar
 * capsule control. The window itself is portrait-sized by the desktop window
 * manager, so mobile pages get the viewport they expect.
 */
function DesktopWebView({
  windowId,
  url,
  title,
  onClose,
}: DesktopWebViewProps) {
  const { t } = useTranslation();
  const [reloadKey, setReloadKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [loadConsole, setLoadConsole] = useState(false);
  const consoleUrl = !loadConsole && isDashboardConsoleUrl(url);
  const { host, path } = splitUrl(url);

  useEffect(() => {
    setLoadConsole(false);
  }, [url]);

  const openInBrowser = () => {
    window.open(url, "_blank", "noopener,noreferrer");
  };

  const reload = () => {
    setError(false);
    setLoading(true);
    setReloadKey((value) => value + 1);
  };

  return (
    <div
      className={styles.desktopWebView}
      data-testid="desktop-webview"
      data-url={url}
    >
      <div className={styles.desktopWebViewNav}>
        <button
          type="button"
          className={styles.desktopWebViewBack}
          aria-label={t("apps.webview.back", "Back")}
          title={t("apps.webview.back", "Back")}
          onClick={() => onClose?.(windowId)}
          disabled={!onClose}
        >
          <ChevronLeft size={23} />
        </button>
        <span className={styles.desktopWebViewTitle} title={`${host}${path}`}>
          {host || title || url}
        </span>
        <span className={styles.desktopWebViewCapsule}>
          <button
            type="button"
            className={styles.desktopWebViewCapsuleButton}
            aria-label={t("apps.webview.more", "More")}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <MoreHorizontal size={17} />
          </button>
          <span className={styles.desktopWebViewCapsuleDivider} />
          <button
            type="button"
            className={styles.desktopWebViewCapsuleButton}
            aria-label={t("apps.webview.close", "Close")}
            title={t("apps.webview.close", "Close")}
            onClick={() => onClose?.(windowId)}
            disabled={!onClose}
          >
            <Circle size={13} strokeWidth={2.4} />
          </button>
        </span>
      </div>

      {menuOpen && (
        <>
          <button
            type="button"
            className={styles.desktopWebViewMenuBackdrop}
            aria-label={t("apps.webview.closeMenu", "Close menu")}
            onClick={() => setMenuOpen(false)}
          />
          <div
            className={styles.desktopWebViewMenu}
            role="menu"
            data-testid="desktop-webview-menu"
          >
            <button
              type="button"
              className={styles.desktopWebViewMenuItem}
              onClick={() => {
                setMenuOpen(false);
                reload();
              }}
            >
              <RefreshCw size={13} />
              {t("apps.webview.reload", "Reload")}
            </button>
            <button
              type="button"
              className={styles.desktopWebViewMenuItem}
              onClick={() => {
                setMenuOpen(false);
                openInBrowser();
              }}
            >
              <ExternalLink size={13} />
              {t("apps.webview.openInBrowser", "Open in browser")}
            </button>
          </div>
        </>
      )}

      <div className={styles.desktopWebViewBody}>
        {consoleUrl ? (
          <DesktopConsoleNotice
            onOpenExternal={openInBrowser}
            onLoadAnyway={() => {
              setLoadConsole(true);
              setLoading(true);
            }}
          />
        ) : (
          <>
            {loading && !error && (
              <div className={styles.desktopWebViewLoading}>
                <span className={styles.desktopWebViewSpinner} aria-hidden />
                <span>{t("apps.webview.loading", "Loading page…")}</span>
              </div>
            )}
            {error ? (
              <div className={styles.desktopWebViewError}>
                <AlertTriangle size={26} strokeWidth={1.5} />
                <span className={styles.desktopWebViewErrorTitle}>
                  {t(
                    "apps.webview.errorTitle",
                    "This page can't be shown inside the desktop",
                  )}
                </span>
                <span className={styles.desktopWebViewErrorHint}>
                  {t(
                    "apps.webview.errorHint",
                    "The site may block embedding. Open it in your browser instead.",
                  )}
                </span>
                <button
                  type="button"
                  className={styles.desktopWebViewErrorAction}
                  onClick={openInBrowser}
                >
                  <ExternalLink size={13} />
                  {t("apps.webview.openInBrowser", "Open in browser")}
                </button>
              </div>
            ) : (
              <iframe
                key={`${url}:${reloadKey}`}
                data-testid="desktop-webview-frame"
                className={styles.desktopWebViewFrame}
                src={url}
                title={title || host || url}
                /* User-entered / agent-shared pages need their own cookies and
                   same-origin ES modules. Without allow-same-origin the frame is an
                   opaque origin: module scripts fail CORS and localStorage throws,
                   leaving modern sites blank (same issue as DesktopBrowserWindow). */
                sandbox="allow-scripts allow-same-origin allow-forms allow-modals"
                referrerPolicy="no-referrer"
                onLoad={() => setLoading(false)}
                onError={() => {
                  setLoading(false);
                  setError(true);
                }}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default memo(DesktopWebView);
