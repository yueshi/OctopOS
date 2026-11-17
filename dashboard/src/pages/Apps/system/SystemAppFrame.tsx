import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Modal, Spin } from "antd";
import { useTranslation } from "react-i18next";

import {
  attachBridge,
  type BridgeParams,
} from "../../../components/MiniAppFrame/bridge";
import {
  SYSTEM_APP_CAPABILITIES,
  SYSTEM_CAP,
} from "../../../components/MiniAppFrame/capabilities";
import { buildSrcDoc } from "../../../components/MiniAppFrame/sdk";
import { buildWrapperDoc } from "../../../components/MiniAppFrame/wrapper";
import { fullscreenContainer } from "../../../utils/overlayContainer";
import { runSystemAction, type SystemActionParams } from "./actions";
import {
  SYSTEM_APP_BY_KEY,
  systemAppAllowsAction,
  systemAppAllowsQuery,
} from "./registry";
import { runSystemQuery } from "./queries";
import { SYSTEM_APP_SDK } from "./systemSdk";
import type { SystemAppKey, SystemQueryParams } from "./types";

/** Console pages a system app may deep-link to. */
const CONSOLE_PATH_PREFIXES = [
  "/token-usage",
  "/personalization/channels",
  "/personalization/skills",
  "/personalization/tools",
  "/personalization/memory",
  "/connectors",
  "/tasks",
  "/knowledge-bases",
  "/agent-config",
] as const;

export function isSystemConsolePath(path: string): boolean {
  const clean = path.trim();
  if (!clean.startsWith("/") || clean.startsWith("//")) return false;
  return CONSOLE_PATH_PREFIXES.some(
    (prefix) => clean === prefix || clean.startsWith(`${prefix}?`),
  );
}

interface Props {
  appKey: SystemAppKey;
  agentId: string;
  title?: string;
  /** False while the desktop window is hidden or minimized. */
  active?: boolean;
  /** True when the hosting window is the focused one. */
  foreground?: boolean;
  onOpenConsole?: (path: string) => void;
}

/**
 * Sandboxed host for a platform system app.
 *
 * Reuses the MiniApp wrapper/CSP, but injects a restricted SDK and a fixed
 * capability set. It never touches `light_apps`: data comes from the host
 * bridge, which calls existing authenticated APIs.
 */
export default function SystemAppFrame({
  appKey,
  agentId,
  title,
  active = true,
  foreground = true,
  onOpenConsole,
}: Props) {
  const { t, i18n } = useTranslation();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const onOpenConsoleRef = useRef(onOpenConsole);
  const definition = SYSTEM_APP_BY_KEY.get(appKey);
  const hasActions = Boolean(definition?.actions?.length);
  const capabilities = useMemo(
    () =>
      hasActions
        ? [...SYSTEM_APP_CAPABILITIES, SYSTEM_CAP.ACT]
        : SYSTEM_APP_CAPABILITIES,
    [hasActions],
  );
  const extraMethods = useMemo(
    () =>
      hasActions
        ? ["systemQuery", "systemAction", "openConsole"]
        : ["systemQuery", "openConsole"],
    [hasActions],
  );
  const [html, setHtml] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    onOpenConsoleRef.current = onOpenConsole;
  }, [onOpenConsole]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    if (!definition) {
      setError(t("apps.system.unknown", "Unknown system app"));
      setLoading(false);
      return;
    }
    definition
      .loadHtml()
      .then((text) => {
        if (!alive) return;
        if (!text.trim()) {
          setError(t("apps.system.noHtml", "This system app has no page."));
          return;
        }
        setHtml(text);
      })
      .catch((err) => {
        if (alive) {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [definition, t]);

  useEffect(() => {
    const detach = attachBridge(
      {
        capabilities,
        extraMethods,
        handle: async (method: string, params: BridgeParams) => {
          if (method === "systemQuery") {
            const key = String(params.name ?? "").trim();
            if (!systemAppAllowsQuery(appKey, key)) {
              throw new Error("query not declared by this app");
            }
            const queryParams =
              params.args && typeof params.args === "object"
                ? (params.args as unknown as SystemQueryParams)
                : {};
            return runSystemQuery(key, queryParams, { agentId });
          }
          if (method === "systemAction") {
            const key = String(params.name ?? "").trim();
            if (!systemAppAllowsAction(appKey, key)) {
              throw new Error("action not declared by this app");
            }
            const actionParams =
              params.args && typeof params.args === "object"
                ? (params.args as SystemActionParams)
                : {};
            return await new Promise<unknown>((resolve, reject) => {
              Modal.confirm({
                getContainer: fullscreenContainer,
                title: t("apps.system.confirmActionTitle", "Run this action?"),
                content: t(
                  "apps.system.confirmActionBody",
                  "This changes the expert's configuration.",
                ),
                okText: t("apps.system.confirmActionOk", "Confirm"),
                cancelText: t("common.cancel", "Cancel"),
                onCancel: () => resolve({ cancelled: true }),
                onOk: async () => {
                  try {
                    resolve(
                      await runSystemAction(key, actionParams, { agentId }),
                    );
                  } catch (err) {
                    reject(err);
                    throw err;
                  }
                },
              });
            });
          }
          if (method === "openConsole") {
            const path = String(params.url ?? "").trim();
            if (!isSystemConsolePath(path)) {
              throw new Error("unsupported console path");
            }
            if (!onOpenConsoleRef.current) {
              throw new Error("console navigation is unavailable");
            }
            // Open from the confirmation click, not from this async handler:
            // browsers treat it as part of the user gesture, so popup
            // blockers do not swallow the settings tab.
            Modal.confirm({
              getContainer: fullscreenContainer,
              title: t(
                "apps.system.openConsoleTitle",
                "Open the full settings page?",
              ),
              content: path,
              okText: t("apps.system.openConsoleOk", "Open settings"),
              cancelText: t("common.cancel", "Cancel"),
              onOk: () => onOpenConsoleRef.current?.(path),
            });
            return { opened: true };
          }
          throw new Error("unsupported method");
        },
      },
      () => frameRef.current?.contentWindow ?? null,
    );
    return detach;
  }, [agentId, appKey, capabilities, definition, extraMethods, t]);

  const srcDoc = useMemo(() => {
    if (!html) return "";
    return buildWrapperDoc(
      buildSrcDoc(html, {
        sdk: SYSTEM_APP_SDK,
        locale: i18n.language?.startsWith("zh") ? "zh" : "en",
      }),
    );
  }, [html, i18n.language]);

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
        data-testid="system-app-loading"
      >
        <Spin />
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 16 }} data-testid="system-app-error">
        <Alert
          type="error"
          showIcon
          message={title || t("apps.system.errorTitle", "Could not open app")}
          description={error}
        />
      </div>
    );
  }

  return (
    <iframe
      ref={frameRef}
      title={title || t("apps.system.windowTitle", "System app")}
      data-testid="system-app-frame"
      data-system-key={appKey}
      data-agent-id={agentId}
      data-active={active ? "true" : "false"}
      data-foreground={foreground ? "true" : "false"}
      srcDoc={srcDoc}
      sandbox="allow-scripts"
      style={{ width: "100%", height: "100%", border: 0, display: "block" }}
    />
  );
}
