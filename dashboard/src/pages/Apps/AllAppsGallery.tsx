import { useMemo, useState, type CSSProperties } from "react";
import { Segmented } from "antd";
import { AppWindow, ArrowUpRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { appPreviewPath, type AppSummary } from "../../api/modules/apps";
import { usePreviewImage } from "../../hooks/usePreviewImage";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import type { SystemAppDefinition, SystemAppKey } from "./system/types";
import styles from "./index.module.less";

export interface SystemAppGalleryItem {
  agentId: string;
  agentName: string;
  definition: SystemAppDefinition;
}

interface Props {
  apps: AppSummary[];
  /** Platform apps from experts whose desktop the viewer can open. */
  systemApps?: readonly SystemAppGalleryItem[];
  timeZone: string;
  onOpen: (appId: string) => void;
  onOpenSystemApp?: (agentId: string, key: SystemAppKey) => void;
}

type GalleryFilter = "all" | "apps" | "system";

function hashText(text: string): number {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function coverTint(appId: string): string {
  const hue = hashText(appId) % 360;
  return `linear-gradient(145deg, hsl(${hue} 76% 60%), hsl(${
    (hue + 42) % 360
  } 70% 46%))`;
}

function AllAppCover({ app }: { app: AppSummary }) {
  const url = usePreviewImage(
    app.has_preview ? appPreviewPath(app.app_id) : null,
  );
  if (url) {
    return (
      <img
        className={styles.allAppsCoverImage}
        src={url}
        alt=""
        draggable={false}
      />
    );
  }
  return (
    <span
      className={styles.allAppsCoverFallback}
      style={{ "--app-tint": coverTint(app.app_id) } as CSSProperties}
    >
      <span className={styles.allAppsCoverMark}>
        {Array.from(app.name.trim())[0]?.toUpperCase() ?? "?"}
      </span>
      <span className={styles.allAppsCoverAgent}>
        {app.agent_name || app.agent_id}
      </span>
    </span>
  );
}

function SystemAppCover({
  definition,
  agentName,
}: {
  definition: SystemAppDefinition;
  agentName: string;
}) {
  const Icon = definition.icon;
  return (
    <span
      className={styles.allAppsCoverFallback}
      style={
        {
          "--app-tint": "linear-gradient(145deg, #0f766e, #0e7490)",
        } as CSSProperties
      }
    >
      <Icon size={32} strokeWidth={1.8} />
      <span className={styles.allAppsCoverAgent}>{agentName}</span>
    </span>
  );
}

/** Board-style gallery of every MiniApp and system app the caller can open. */
export default function AllAppsGallery({
  apps,
  systemApps = [],
  timeZone,
  onOpen,
  onOpenSystemApp,
}: Props) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<GalleryFilter>("all");
  const hasSystem = systemApps.length > 0;
  const activeFilter = hasSystem ? filter : "apps";
  const showApps = activeFilter !== "system";
  const showSystem = hasSystem && activeFilter !== "apps";

  const countText = useMemo(() => {
    if (activeFilter === "system") {
      return t("apps.all.systemCount", "{{count}} system apps", {
        count: systemApps.length,
      });
    }
    if (activeFilter === "all" && hasSystem) {
      return t(
        "apps.all.countAll",
        "{{apps}} MiniApps · {{systems}} system apps",
        { apps: apps.length, systems: systemApps.length },
      );
    }
    return t("apps.all.count", "{{count}} MiniApps", { count: apps.length });
  }, [activeFilter, apps.length, hasSystem, systemApps.length, t]);

  return (
    <div className={styles.boardGallery} data-testid="all-apps-gallery">
      <div className={styles.boardGalleryHeader}>
        <div>
          <h2>{t("apps.all.galleryTitle", "All MiniApps")}</h2>
          <p>
            {t("apps.all.galleryHint", "Open any MiniApp to view or edit it.")}
          </p>
        </div>
        <div
          className={styles.boardGalleryHeaderTools}
          data-testid="all-apps-filter"
        >
          {hasSystem ? (
            <Segmented
              size="small"
              value={activeFilter}
              onChange={(value) => setFilter(value as GalleryFilter)}
              options={[
                { value: "all", label: t("apps.all.filterAll", "All") },
                {
                  value: "apps",
                  label: t("apps.all.filterApps", "MiniApps"),
                },
                {
                  value: "system",
                  label: t("apps.all.filterSystem", "System apps"),
                },
              ]}
            />
          ) : null}
          <span className={styles.boardGalleryCount}>{countText}</span>
        </div>
      </div>

      {showSystem ? (
        <section className={styles.boardGallerySection}>
          {activeFilter === "all" ? (
            <h3 className={styles.boardGallerySectionTitle}>
              {t("apps.desktop.systemApps", "System apps")}
            </h3>
          ) : null}
          <div className={styles.boardGalleryGrid}>
            {systemApps.map(({ agentId, agentName, definition }) => (
              <button
                key={`${agentId}:${definition.key}`}
                type="button"
                className={`${styles.boardGalleryCard} ${styles.allAppsCard}`}
                data-testid="all-apps-system-card"
                data-agent-id={agentId}
                data-system-key={definition.key}
                onClick={() => onOpenSystemApp?.(agentId, definition.key)}
              >
                <span className={styles.boardGalleryCover}>
                  <SystemAppCover
                    definition={definition}
                    agentName={agentName}
                  />
                  <span className={styles.boardGalleryOpen} aria-hidden>
                    <ArrowUpRight size={14} />
                  </span>
                </span>
                <span className={styles.boardGalleryBody}>
                  <span className={styles.boardGalleryTitleRow}>
                    <span className={styles.boardGalleryTitle}>
                      {t(definition.titleKey, definition.key)}
                    </span>
                    <span className={styles.boardGalleryItemCount}>
                      {t("apps.all.systemTag", "System")}
                    </span>
                  </span>
                  <span className={styles.boardGalleryDescription}>
                    {t(definition.descriptionKey, "")}
                  </span>
                  <span
                    className={`${styles.boardGalleryMeta} ${styles.allAppsMeta}`}
                  >
                    <span className={styles.allAppsMetaAgent}>
                      <AppWindow size={11} />
                      {agentName}
                    </span>
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {showApps ? (
        <section className={styles.boardGallerySection}>
          {activeFilter === "all" && hasSystem ? (
            <h3 className={styles.boardGallerySectionTitle}>
              {t("apps.all.miniAppsSection", "MiniApps")}
            </h3>
          ) : null}
          <div className={styles.boardGalleryGrid}>
            {apps.map((app) => (
              <button
                key={app.app_id}
                type="button"
                className={`${styles.boardGalleryCard} ${styles.allAppsCard}`}
                data-testid="all-apps-card"
                onClick={() => onOpen(app.app_id)}
              >
                <span className={styles.boardGalleryCover}>
                  <AllAppCover app={app} />
                  <span className={styles.boardGalleryOpen} aria-hidden>
                    <ArrowUpRight size={14} />
                  </span>
                </span>
                <span className={styles.boardGalleryBody}>
                  <span className={styles.boardGalleryTitleRow}>
                    <span className={styles.boardGalleryTitle}>{app.name}</span>
                    {app.permission !== "edit_structure" && (
                      <span className={styles.boardGalleryItemCount}>
                        {app.permission === "edit_data"
                          ? t("apps.editData", "Can append rows")
                          : t("apps.viewOnly", "View only")}
                      </span>
                    )}
                  </span>
                  <span className={styles.boardGalleryDescription}>
                    {app.description || "—"}
                  </span>
                  <span
                    className={`${styles.boardGalleryMeta} ${styles.allAppsMeta}`}
                  >
                    <span className={styles.allAppsMetaAgent}>
                      <AppWindow size={11} />
                      {app.agent_name || app.agent_id}
                    </span>
                    <span>
                      {t("apps.rows", "{{count}} rows", {
                        count: app.row_count,
                      })}
                    </span>
                    <span>
                      {formatServerDateTime(app.updated_at, timeZone)}
                    </span>
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
