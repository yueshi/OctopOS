import { App as AntdApp, ConfigProvider, Drawer } from "antd";
import { useTranslation } from "react-i18next";

import { fullscreenContainer } from "../../../utils/overlayContainer";
import ChannelsPanel from "../../Agent/Channels/ChannelsPanel";
import SkillsTabs from "../../Agent/Skills/components/SkillsTabs";
import ToolsTabs from "../../Agent/Tools/ToolsTabs";
import type { SystemManagerKey } from "../system/types";
import DesktopConnectorsPanel from "./DesktopConnectorsPanel";
import DesktopTasksPanel from "./DesktopTasksPanel";
import styles from "./DesktopManagerDrawer.module.less";

interface Props {
  open: boolean;
  agentId: string;
  agentName: string;
  managerKey: SystemManagerKey;
  onClose: () => void;
}

/**
 * Native desktop management surface for a system app's "Manage" action.
 *
 * System apps are sandboxed summaries; the management UI runs in the desktop
 * React tree instead of navigating to (or embedding) the console SPA. It
 * reuses the feature panels where they already support embedding.
 */
export default function DesktopManagerDrawer({
  open,
  agentId,
  agentName,
  managerKey,
  onClose,
}: Props) {
  const { t } = useTranslation();
  const label = t(`apps.system.manager.${managerKey}`, managerKey);

  return (
    <ConfigProvider getPopupContainer={() => fullscreenContainer()}>
      <Drawer
        open={open}
        onClose={onClose}
        title={`${label} · ${agentName}`}
        placement="right"
        width="min(1120px, 96vw)"
        getContainer={fullscreenContainer}
        destroyOnHidden
        styles={{ body: { padding: 0 } }}
        data-testid="desktop-manager-drawer"
        data-manager-key={managerKey}
      >
        <AntdApp
          key={`${agentId}:${managerKey}`}
          className={styles.managerBody}
        >
          {managerKey === "skills" ? (
            <SkillsTabs agentId={agentId} />
          ) : managerKey === "tools" ? (
            <ToolsTabs agentId={agentId} />
          ) : managerKey === "channels" ? (
            <ChannelsPanel agentId={agentId} />
          ) : managerKey === "connectors" ? (
            <DesktopConnectorsPanel />
          ) : (
            <DesktopTasksPanel />
          )}
        </AntdApp>
      </Drawer>
    </ConfigProvider>
  );
}
