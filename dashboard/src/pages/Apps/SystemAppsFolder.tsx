import { useState } from "react";
import { Popover, Typography } from "antd";
import { FolderOpen } from "lucide-react";
import { useTranslation } from "react-i18next";

import { fullscreenContainer } from "../../utils/overlayContainer";
import type { SystemAppDefinition } from "./system/types";
import styles from "./index.module.less";

const { Text } = Typography;

interface Props {
  apps: readonly SystemAppDefinition[];
  onOpen: (app: SystemAppDefinition) => void;
}

/**
 * One desktop icon for all platform system apps on this expert desktop.
 * Keeping them in a folder avoids five permanent icons per expert while the
 * command palette still makes each app directly searchable.
 */
export default function SystemAppsFolder({ apps, onOpen }: Props) {
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
          data-testid="desktop-system-folder-panel"
        >
          <div className={styles.desktopSystemFolderTitle}>
            {t("apps.desktop.systemApps", "System apps")}
          </div>
          <div className={styles.desktopSystemFolderGrid}>
            {apps.map((definition) => {
              const Icon = definition.icon;
              return (
                <button
                  key={definition.key}
                  type="button"
                  className={styles.desktopSystemFolderItem}
                  data-testid="desktop-system-folder-item"
                  data-system-key={definition.key}
                  onClick={() => {
                    setOpen(false);
                    onOpen(definition);
                  }}
                >
                  <span className={styles.desktopSystemFolderItemArt}>
                    <Icon size={18} strokeWidth={1.9} />
                  </span>
                  <span className={styles.desktopSystemFolderItemLabel}>
                    {t(definition.titleKey, definition.key)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      }
    >
      <button
        type="button"
        className={styles.desktopIcon}
        data-testid="desktop-system-folder"
        aria-label={t("apps.desktop.systemApps", "System apps")}
      >
        <span
          className={[styles.desktopIconArt, styles.desktopIconArtFolder].join(
            " ",
          )}
          style={{ width: 72, height: 72, borderRadius: 18 }}
        >
          <FolderOpen
            size={30}
            strokeWidth={1.9}
            className={styles.desktopIconGlyph}
          />
          <span className={styles.desktopIconBadge}>{apps.length}</span>
        </span>
        <Text ellipsis className={styles.desktopIconName}>
          {t("apps.desktop.systemApps", "System apps")}
        </Text>
      </button>
    </Popover>
  );
}
