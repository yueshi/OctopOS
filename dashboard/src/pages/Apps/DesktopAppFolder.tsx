import { useState, type DragEvent } from "react";
import { Popover, Typography } from "antd";
import { FolderKanban } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AppSummary } from "../../api/modules/apps";
import { fullscreenContainer } from "../../utils/overlayContainer";
import type { DesktopFolder } from "./desktopOrg";
import styles from "./index.module.less";

const { Text } = Typography;

/** Shared drag props for desktop entry icons (apps and folders). */
export interface DesktopEntryDragProps {
  draggable?: boolean;
  dragging?: boolean;
  dropTarget?: boolean;
  onDragStart?: (event: DragEvent<HTMLButtonElement>) => void;
  onDragEnd?: (event: DragEvent<HTMLButtonElement>) => void;
  onDragOver?: (event: DragEvent<HTMLButtonElement>) => void;
  onDragLeave?: (event: DragEvent<HTMLButtonElement>) => void;
  onDrop?: (event: DragEvent<HTMLButtonElement>) => void;
}

interface Props extends DesktopEntryDragProps {
  folder: DesktopFolder;
  apps: readonly AppSummary[];
  onOpen: (app: AppSummary) => void;
}

/** A user-created desktop folder holding selected MiniApps. */
export default function DesktopAppFolder({
  folder,
  apps,
  onOpen,
  draggable,
  dragging,
  dropTarget,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDragLeave,
  onDrop,
}: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="rightTop"
      getPopupContainer={fullscreenContainer}
      content={
        <div
          className={styles.desktopSystemFolderPanel}
          data-testid="desktop-app-folder-panel"
        >
          <div className={styles.desktopSystemFolderTitle}>{folder.name}</div>
          <div className={styles.desktopSystemFolderGrid}>
            {apps.map((app) => (
              <button
                key={app.app_id}
                type="button"
                className={styles.desktopSystemFolderItem}
                data-testid="desktop-app-folder-item"
                data-app-id={app.app_id}
                onClick={() => {
                  setOpen(false);
                  onOpen(app);
                }}
              >
                <span className={styles.desktopSystemFolderItemArt}>
                  {Array.from(app.name.trim())[0]?.toUpperCase() ?? "?"}
                </span>
                <span className={styles.desktopSystemFolderItemLabel}>
                  {app.name}
                </span>
              </button>
            ))}
          </div>
        </div>
      }
    >
      <button
        type="button"
        className={[
          styles.desktopIcon,
          dragging ? styles.desktopIconDragging : "",
          dropTarget ? styles.desktopIconDropTarget : "",
        ]
          .filter(Boolean)
          .join(" ")}
        data-testid="desktop-app-folder"
        data-folder-id={folder.id}
        aria-label={t("apps.desktop.openFolder", "Open folder {{name}}", {
          name: folder.name,
        })}
        draggable={draggable}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <span
          className={[styles.desktopIconArt, styles.desktopIconArtFolder].join(
            " ",
          )}
          style={{ width: 72, height: 72, borderRadius: 18 }}
        >
          <FolderKanban
            size={30}
            strokeWidth={1.9}
            className={styles.desktopIconGlyph}
          />
          <span className={styles.desktopIconBadge}>{apps.length}</span>
        </span>
        <Text ellipsis className={styles.desktopIconName}>
          {folder.name}
        </Text>
      </button>
    </Popover>
  );
}
