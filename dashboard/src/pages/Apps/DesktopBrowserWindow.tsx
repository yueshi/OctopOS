import {
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { ExternalLink, Globe, RefreshCw, AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";

import DesktopConsoleNotice from "./DesktopConsoleNotice";
import { isDashboardConsoleUrl } from "./desktopWindows";
import styles from "./index.module.less";

/** Normalize a user-typed address; empty clears the page, null is invalid. */
export function normalizeBrowserUrl(raw: string): string | null {
  const text = raw.trim();
  if (!text) return "";
  const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(text)
    ? text
    : `https://${text}`;
  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}

interface DesktopBrowserWindowProps {
  windowId: string;
  agentId: string;
  agentName?: string;
  url: string;
  /** Persist the navigated URL in the desktop window state. */
  onNavigate: (windowId: string, url: string) => void;
}

/**
 * A free-form browser window: blank by default, with an address bar.
 * Pages load in a sandboxed iframe, so a hostile page cannot touch the
 * dashboard origin; sites that forbid embedding are opened externally instead.
 */
function DesktopBrowserWindow({
  windowId,
  agentId,
  agentName,
  url,
  onNavigate,
}: DesktopBrowserWindowProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(url);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(url));
  const [reloadKey, setReloadKey] = useState(0);
  const [loadConsole, setLoadConsole] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const consoleUrl = Boolean(url) && !loadConsole && isDashboardConsoleUrl(url);

  useEffect(() => {
    setDraft(url);
    setLoading(Boolean(url));
    setError("");
    setLoadConsole(false);
  }, [url]);

  const submit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const next = normalizeBrowserUrl(draft);
      if (next === null) {
        setError(
          t(
            "apps.desktop.browser.invalidUrl",
            "Enter a valid http(s) address.",
          ),
        );
        return;
      }
      setError("");
      setLoading(Boolean(next));
      onNavigate(windowId, next);
    },
    [draft, onNavigate, t, windowId],
  );

  const reload = useCallback(() => {
    if (!url) return;
    setLoading(true);
    setReloadKey((value) => value + 1);
  }, [url]);

  const openExternal = useCallback(() => {
    if (url) window.open(url, "_blank", "noopener,noreferrer");
  }, [url]);

  return (
    <div className={styles.desktopBrowser} data-testid="desktop-browser">
      <form className={styles.desktopBrowserBar} onSubmit={submit}>
        <Globe size={15} className={styles.desktopBrowserGlobe} aria-hidden />
        <input
          ref={inputRef}
          className={styles.desktopBrowserAddress}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={t(
            "apps.desktop.browser.placeholder",
            "Enter a URL, e.g. example.com",
          )}
          aria-label={t("apps.desktop.browser.address", "Address")}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          autoFocus={!url}
        />
        <button
          type="submit"
          className={styles.desktopBrowserButton}
          disabled={!draft.trim() && !url}
        >
          {t("apps.desktop.browser.go", "Open")}
        </button>
        {url && (
          <>
            <button
              type="button"
              className={styles.desktopBrowserIconButton}
              aria-label={t("apps.desktop.browser.reload", "Reload")}
              title={t("apps.desktop.browser.reload", "Reload")}
              onClick={reload}
            >
              <RefreshCw size={14} />
            </button>
            <button
              type="button"
              className={styles.desktopBrowserIconButton}
              aria-label={t(
                "apps.desktop.browser.openExternal",
                "Open in system browser",
              )}
              title={t(
                "apps.desktop.browser.openExternal",
                "Open in system browser",
              )}
              onClick={openExternal}
            >
              <ExternalLink size={14} />
            </button>
          </>
        )}
      </form>

      {error && (
        <div className={styles.desktopBrowserError} role="alert">
          <AlertTriangle size={13} />
          <span>{error}</span>
        </div>
      )}

      {url && consoleUrl ? (
        <div className={styles.desktopBrowserBody}>
          <DesktopConsoleNotice
            onOpenExternal={openExternal}
            onLoadAnyway={() => {
              setLoadConsole(true);
              setLoading(true);
            }}
          />
        </div>
      ) : url ? (
        <div className={styles.desktopBrowserBody}>
          {loading && (
            <div className={styles.desktopBrowserLoading}>
              <span>{t("apps.webview.loading", "Loading page…")}</span>
            </div>
          )}
          <iframe
            key={`${url}:${reloadKey}`}
            data-testid="desktop-browser-frame"
            className={styles.desktopBrowserFrame}
            src={url}
            title={url}
            /* A user-entered site needs its own cookies/storage and same-origin
               ES modules. Without allow-same-origin the frame is an opaque
               origin: module scripts fail CORS and localStorage throws, so
               sites (including Octop itself) stay blank. */
            sandbox="allow-scripts allow-same-origin allow-forms allow-modals"
            referrerPolicy="no-referrer"
            onLoad={() => setLoading(false)}
          />
        </div>
      ) : (
        <div className={styles.desktopBrowserBlank}>
          <Globe size={38} strokeWidth={1.4} aria-hidden />
          <p className={styles.desktopBrowserBlankTitle}>
            {t("apps.desktop.browser.blank", "Enter a URL to start browsing")}
          </p>
          <span className={styles.desktopBrowserBlankHint}>
            {t(
              "apps.desktop.browser.blankHint",
              "Some sites block embedding; use the external-open button for those.",
            )}
          </span>
          {agentName && (
            <span className={styles.desktopBrowserContext}>
              {agentName}
              {agentId ? ` · ${agentId}` : ""}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default memo(DesktopBrowserWindow);
