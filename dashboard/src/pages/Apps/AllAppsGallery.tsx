import type { CSSProperties } from "react";
import { AppWindow, ArrowUpRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { appPreviewPath, type AppSummary } from "../../api/modules/apps";
import { usePreviewImage } from "../../hooks/usePreviewImage";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import styles from "./index.module.less";

interface Props {
  apps: AppSummary[];
  timeZone: string;
  onOpen: (appId: string) => void;
}

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

/** Board-style gallery of every MiniApp the caller can access. */
export default function AllAppsGallery({ apps, timeZone, onOpen }: Props) {
  const { t } = useTranslation();

  return (
    <div className={styles.boardGallery} data-testid="all-apps-gallery">
      <div className={styles.boardGalleryHeader}>
        <div>
          <h2>{t("apps.all.galleryTitle", "All MiniApps")}</h2>
          <p>
            {t("apps.all.galleryHint", "Open any MiniApp to view or edit it.")}
          </p>
        </div>
        <span className={styles.boardGalleryCount}>
          {t("apps.all.count", "{{count}} MiniApps", { count: apps.length })}
        </span>
      </div>
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
                <span>{formatServerDateTime(app.updated_at, timeZone)}</span>
              </span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
