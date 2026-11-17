import { useState } from "react";
import {
  Button,
  Divider,
  Drawer,
  Input,
  Modal,
  Select,
  Switch,
  Typography,
} from "antd";
import { FolderKanban, FolderPlus, Pencil, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AppSummary } from "../../api/modules/apps";
import { fullscreenContainer } from "../../utils/overlayContainer";
import type { SystemAppDefinition } from "./system/types";
import {
  createDesktopFolder,
  deleteDesktopFolder,
  resetDesktopOrg,
  setAppHidden,
  setAppPinned,
  setSystemAppHidden,
  updateDesktopFolder,
  visibleAppsFor,
  type DesktopFolder,
  type DesktopOrgPrefs,
} from "./desktopOrg";
import styles from "./index.module.less";

const { Text } = Typography;

interface Props {
  open: boolean;
  onClose: () => void;
  agentId: string | null;
  agentName: string;
  userApps: readonly AppSummary[];
  systemApps: readonly SystemAppDefinition[];
  prefs: DesktopOrgPrefs;
  defaultPinnedIds: readonly string[];
  /** Current desktop entry order, used as the base for the first folder. */
  knownEntryIds: readonly string[];
  onChange: (prefs: DesktopOrgPrefs) => void;
}

/**
 * Per-expert desktop organization: choose which icons stay on the desktop and
 * which MiniApps are pinned to the Dock. Preferences live in localStorage for
 * this phase; the model is intentionally small so it can move to the server
 * without changing the UI.
 */
export default function DesktopManageDrawer({
  open,
  onClose,
  agentId,
  agentName,
  userApps,
  systemApps,
  prefs,
  defaultPinnedIds,
  knownEntryIds,
  onChange,
}: Props) {
  const { t } = useTranslation();
  const hiddenSystem = new Set(
    agentId ? prefs.hiddenSystem[agentId] ?? [] : [],
  );
  const hiddenApps = new Set(prefs.hiddenApps);
  // Folders are stored globally but only make sense on the desktop that owns
  // their apps. Hide folders that belong to another expert's desktop.
  const scopedAppIds = new Set(userApps.map((app) => app.app_id));
  const folders = (prefs.folders ?? []).filter((folder) =>
    folder.appIds.some((appId) => scopedAppIds.has(appId)),
  );
  const folderAppOptions = visibleAppsFor(prefs, userApps).map((app) => ({
    value: app.app_id,
    label: app.name,
  }));
  const [folderOpen, setFolderOpen] = useState(false);
  const [editingFolderId, setEditingFolderId] = useState<string | null>(null);
  const [folderName, setFolderName] = useState("");
  const [folderApps, setFolderApps] = useState<string[]>([]);
  const pinned = (appId: string) =>
    prefs.pinnedApps === undefined
      ? defaultPinnedIds.includes(appId)
      : prefs.pinnedApps.includes(appId);
  const openFolderModal = (folder?: DesktopFolder) => {
    const available = new Set(folderAppOptions.map((option) => option.value));
    setEditingFolderId(folder?.id ?? null);
    setFolderName(folder?.name ?? "");
    setFolderApps(
      folder ? folder.appIds.filter((appId) => available.has(appId)) : [],
    );
    setFolderOpen(true);
  };
  const closeFolderModal = () => {
    setFolderOpen(false);
    setEditingFolderId(null);
    setFolderName("");
    setFolderApps([]);
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={t("apps.desktop.manage.title", "Manage desktop")}
      placement="right"
      width={380}
      getContainer={fullscreenContainer}
      data-testid="desktop-manage-drawer"
    >
      <div className={styles.desktopManageIntro}>
        <Text type="secondary">
          {t(
            "apps.desktop.manage.hint",
            "Choose what appears on {{name}}'s desktop and in the Dock.",
            { name: agentName },
          )}
        </Text>
      </div>

      {agentId && systemApps.length > 0 ? (
        <>
          <Divider orientation="left" plain>
            {t("apps.desktop.systemApps", "System apps")}
          </Divider>
          <div className={styles.desktopManageList}>
            {systemApps.map((definition) => {
              const Icon = definition.icon;
              return (
                <div
                  key={definition.key}
                  className={styles.desktopManageRow}
                  data-testid={`desktop-manage-system-${definition.key}`}
                >
                  <span className={styles.desktopManageIcon}>
                    <Icon size={16} strokeWidth={1.9} />
                  </span>
                  <span className={styles.desktopManageText}>
                    <span className={styles.desktopManageName}>
                      {t(definition.titleKey, definition.key)}
                    </span>
                    <span className={styles.desktopManageMeta}>
                      {t(definition.descriptionKey, "")}
                    </span>
                  </span>
                  <Switch
                    size="small"
                    checked={!hiddenSystem.has(definition.key)}
                    onChange={(checked) =>
                      onChange(
                        setSystemAppHidden(
                          prefs,
                          agentId,
                          definition.key,
                          !checked,
                        ),
                      )
                    }
                    aria-label={t(
                      "apps.desktop.manage.showOnDesktop",
                      "Show on desktop",
                    )}
                  />
                </div>
              );
            })}
          </div>
        </>
      ) : null}

      <Divider orientation="left" plain>
        {t("apps.viewMode.all", "MiniApps")}
      </Divider>
      <div className={styles.desktopManageList}>
        {userApps.length === 0 ? (
          <Text type="secondary">
            {t(
              "apps.desktop.noAppsForExpert",
              "This expert has not created a MiniApp yet.",
            )}
          </Text>
        ) : (
          userApps.map((app) => (
            <div
              key={app.app_id}
              className={styles.desktopManageRow}
              data-testid={`desktop-manage-app-${app.app_id}`}
            >
              <span className={styles.desktopManageText}>
                <span className={styles.desktopManageName}>{app.name}</span>
                <span className={styles.desktopManageMeta}>
                  {t("apps.rows", "{{count}} rows", { count: app.row_count })}
                </span>
              </span>
              <label className={styles.desktopManageToggle}>
                <span>
                  {t("apps.desktop.manage.showOnDesktop", "Show on desktop")}
                </span>
                <Switch
                  size="small"
                  checked={!hiddenApps.has(app.app_id)}
                  onChange={(checked) =>
                    onChange(setAppHidden(prefs, app.app_id, !checked))
                  }
                  aria-label={t(
                    "apps.desktop.manage.showOnDesktop",
                    "Show on desktop",
                  )}
                />
              </label>
              <label className={styles.desktopManageToggle}>
                <span>{t("apps.desktop.manage.pinToDock", "Pin to Dock")}</span>
                <Switch
                  size="small"
                  disabled={hiddenApps.has(app.app_id)}
                  checked={!hiddenApps.has(app.app_id) && pinned(app.app_id)}
                  onChange={(checked) =>
                    onChange(
                      setAppPinned(
                        prefs,
                        app.app_id,
                        checked,
                        defaultPinnedIds,
                      ),
                    )
                  }
                  aria-label={t("apps.desktop.manage.pinToDock", "Pin to Dock")}
                />
              </label>
            </div>
          ))
        )}
      </div>

      <Divider orientation="left" plain>
        {t("apps.desktop.manage.folders", "Folders")}
      </Divider>
      <div className={styles.desktopManageList}>
        {folders.length === 0 ? (
          <Text type="secondary">
            {t(
              "apps.desktop.manage.noFolders",
              "Group apps into a folder to keep the desktop tidy.",
            )}
          </Text>
        ) : (
          folders.map((folder) => (
            <div
              key={folder.id}
              className={styles.desktopManageRow}
              data-testid={`desktop-manage-folder-${folder.id}`}
            >
              <span className={styles.desktopManageIcon}>
                <FolderKanban size={16} strokeWidth={1.9} />
              </span>
              <span className={styles.desktopManageText}>
                <span className={styles.desktopManageName}>{folder.name}</span>
                <span className={styles.desktopManageMeta}>
                  {t("apps.desktop.manage.folderCount", "{{count}} apps", {
                    count: folder.appIds.filter((appId) =>
                      scopedAppIds.has(appId),
                    ).length,
                  })}
                </span>
              </span>
              <Button
                type="text"
                size="small"
                icon={<Pencil size={14} />}
                aria-label={t("apps.desktop.manage.editFolder", "Edit folder")}
                onClick={() => openFolderModal(folder)}
              />
              <Button
                type="text"
                danger
                size="small"
                icon={<Trash2 size={14} />}
                aria-label={t(
                  "apps.desktop.manage.deleteFolder",
                  "Delete folder",
                )}
                onClick={() => onChange(deleteDesktopFolder(prefs, folder.id))}
              />
            </div>
          ))
        )}
      </div>
      <Button
        block
        className={styles.desktopManageFolderNew}
        icon={<FolderPlus size={15} />}
        onClick={() => openFolderModal()}
      >
        {t("apps.desktop.manage.newFolder", "New folder")}
      </Button>

      <Modal
        open={folderOpen}
        title={
          editingFolderId
            ? t("apps.desktop.manage.editFolder", "Edit folder")
            : t("apps.desktop.manage.newFolder", "New folder")
        }
        okText={
          editingFolderId
            ? t("common.save", "Save")
            : t("common.create", "Create")
        }
        cancelText={t("common.cancel", "Cancel")}
        okButtonProps={{
          disabled: !folderName.trim() || folderApps.length === 0,
        }}
        onCancel={closeFolderModal}
        onOk={() => {
          onChange(
            editingFolderId
              ? updateDesktopFolder(
                  prefs,
                  editingFolderId,
                  folderName,
                  folderApps,
                  knownEntryIds,
                )
              : createDesktopFolder(
                  prefs,
                  folderName,
                  folderApps,
                  knownEntryIds,
                ),
          );
          closeFolderModal();
        }}
        getContainer={fullscreenContainer}
        destroyOnHidden
      >
        <div className={styles.desktopManageFolderForm}>
          <Input
            value={folderName}
            maxLength={40}
            placeholder={t(
              "apps.desktop.manage.folderNamePlaceholder",
              "Folder name",
            )}
            onChange={(event) => setFolderName(event.target.value)}
            autoFocus
          />
          <Select
            mode="multiple"
            value={folderApps}
            options={folderAppOptions}
            placeholder={t(
              "apps.desktop.manage.folderAppsPlaceholder",
              "Choose MiniApps",
            )}
            onChange={setFolderApps}
            optionFilterProp="label"
          />
        </div>
      </Modal>

      <Divider />
      <Button onClick={() => onChange(resetDesktopOrg())} block>
        {t("apps.desktop.manage.reset", "Reset desktop organization")}
      </Button>
    </Drawer>
  );
}
