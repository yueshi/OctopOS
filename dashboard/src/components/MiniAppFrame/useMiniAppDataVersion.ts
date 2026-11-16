import { useEffect, useState } from "react";
import type { RefObject } from "react";

import { appsApi, type AppTableVersion } from "../../api/modules/apps";

/** How often the host checks table data versions while the app is visible. */
export const MINIAPP_DATA_VERSION_POLL_MS = 5_000;

/**
 * Poll the app's per-table ``data_version`` and forward changes to the app
 * iframe as a ``data-changed`` event. The page can subscribe with
 * ``octopApp.onDataChange`` or the ``octop:data-changed`` DOM event.
 *
 * This is deliberately a host concern: the iframe never sees a JWT, and apps
 * that do not opt into live refresh keep working unchanged.
 */
export function useMiniAppDataVersion(
  appId: string,
  frameRef: RefObject<HTMLIFrameElement | null>,
  enabled = true,
): number {
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!enabled || !appId) return;
    let disposed = false;
    let inflight = false;
    let baseline: Record<string, number> | null = null;

    const poll = async () => {
      if (disposed || inflight) return;
      if (document.visibilityState !== "visible") return;
      inflight = true;
      try {
        const data = await appsApi.dataVersion(appId);
        if (disposed) return;
        const next: Record<string, number> = {};
        for (const table of data.tables) {
          next[table.table_key] = table.data_version;
        }
        if (baseline === null) {
          baseline = next;
          return;
        }
        const changed: AppTableVersion[] = data.tables.filter(
          (table) => baseline?.[table.table_key] !== table.data_version,
        );
        baseline = next;
        if (changed.length === 0) return;
        setVersion((current) => current + 1);
        frameRef.current?.contentWindow?.postMessage(
          {
            source: "octop-app-host",
            type: "data-changed",
            data: { tables: changed },
          },
          "*",
        );
      } catch {
        // Best effort: the next tick retries.
      } finally {
        inflight = false;
      }
    };

    void poll();
    const timer = window.setInterval(() => {
      void poll();
    }, MINIAPP_DATA_VERSION_POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [appId, enabled, frameRef]);

  return version;
}
