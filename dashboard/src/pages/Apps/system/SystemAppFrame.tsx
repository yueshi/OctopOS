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
  systemAppAllowsManager,
  systemAppAllowsQuery,
} from "./registry";
import { runSystemQuery } from "./queries";
import { SYSTEM_APP_SDK } from "./systemSdk";
import type {
  SystemAppKey,
  SystemManagerKey,
  SystemQueryParams,
} from "./types";

interface Props {
  appKey: SystemAppKey;
  agentId: string;
  title?: string;
  /** False while the desktop window is hidden or minimized. */
  active?: boolean;
  /** True when the hosting window is the focused one. */
  foreground?: boolean;
  /** Open a native desktop manager instead of the console web page. */
  onOpenManager?: (key: SystemManagerKey) => void;
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
  onOpenManager,
}: Props) {
  const { t, i18n } = useTranslation();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const onOpenManagerRef = useRef(onOpenManager);
  const definition = SYSTEM_APP_BY_KEY.get(appKey);
  const hasActions = Boolean(definition?.actions?.length);
  const hasManagers = Boolean(definition?.managers?.length);
  const capabilities = useMemo(() => {
    const next = [...SYSTEM_APP_CAPABILITIES];
    if (hasActions) next.push(SYSTEM_CAP.ACT);
    if (hasManagers) next.push(SYSTEM_CAP.MANAGE);
    return next;
  }, [hasActions, hasManagers]);
  const extraMethods = useMemo(() => {
    const methods = ["systemQuery"];
    if (hasActions) methods.push("systemAction");
    if (hasManagers) methods.push("openManager");
    return methods;
  }, [hasActions, hasManagers]);
  const [html, setHtml] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    onOpenManagerRef.current = onOpenManager;
  }, [onOpenManager]);

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
          if (method === "openManager") {
            const key = String(params.name ?? "").trim();
            if (!systemAppAllowsManager(appKey, key)) {
              throw new Error("manager not declared by this app");
            }
            if (!onOpenManagerRef.current) {
              throw new Error("manager is unavailable");
            }
            onOpenManagerRef.current(key as SystemManagerKey);
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
