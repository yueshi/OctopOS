import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

import { appsApi, type AppTableVersion } from "../../api/modules/apps";

/** How often the host checks table data versions while the app is focused. */
export const MINIAPP_DATA_VERSION_POLL_MS = 5_000;

/** Background windows refresh less often: many visible frames share one API. */
export const MINIAPP_DATA_VERSION_BACKGROUND_POLL_MS = 30_000;

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
  intervalMs = MINIAPP_DATA_VERSION_POLL_MS,
): number {
  const [version, setVersion] = useState(0);
  // Survives effect restarts (focus / interval changes) so a frame that was
  // polled less often in the background still reports the changes it missed.
  const baselineRef = useRef<{
    appId: string;
    versions: Record<string, number>;
  } | null>(null);

  useEffect(() => {
    if (!enabled || !appId) return;
    let disposed = false;
    let inflight = false;

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
        const baseline = baselineRef.current;
        if (!baseline || baseline.appId !== appId) {
          baselineRef.current = { appId, versions: next };
          return;
        }
        const changed: AppTableVersion[] = data.tables.filter(
          (table) => baseline.versions[table.table_key] !== table.data_version,
        );
        baselineRef.current = { appId, versions: next };
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
    }, intervalMs);
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
  }, [appId, enabled, frameRef, intervalMs]);

  return version;
}
