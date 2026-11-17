import { useMemo, useState } from "react";
import { Button, Empty, Spin } from "antd";
import { Plus, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import type {
  ConnectorCatalogEntry,
  ConnectorInstance,
} from "../../../api/modules/connectors";
import { ConnectorCard } from "../../Agent/Connectors/ConnectorCard";
import { ConnectorConfigDrawer } from "../../Agent/Connectors";
import { ConnectorInstanceCard } from "../../Agent/Connectors/ConnectorInstanceCard";
import { CustomMcpTab } from "../../Agent/Connectors/CustomMcpTab";
import { notifyConnectorsChanged } from "../../Agent/Connectors/customMcpUtils";
import { useConnectorInstances } from "../../Agent/Connectors/useConnectors";
import styles from "./DesktopManagerDrawer.module.less";

type ConnectorMode = "instances" | "catalog" | "custom";

/**
 * Connector management rendered inside the desktop manager. It reuses the
 * feature components (cards + config drawer) but not the console page shell,
 * so nothing navigates away from the desktop.
 */
export default function DesktopConnectorsPanel() {
  const { t } = useTranslation();
  const { catalog, instances, loading, refresh } = useConnectorInstances();
  const [mode, setMode] = useState<ConnectorMode>("instances");
  const [drawerEntry, setDrawerEntry] = useState<ConnectorCatalogEntry | null>(
    null,
  );
  const [drawerInstance, setDrawerInstance] =
    useState<ConnectorInstance | null>(null);
  const [customFocus, setCustomFocus] = useState<string | null>(null);

  const entryByKind = useMemo(
    () => new Map(catalog.map((entry) => [entry.kind, entry])),
    [catalog],
  );

  const openCreate = (entry: ConnectorCatalogEntry) => {
    setDrawerEntry(entry);
    setDrawerInstance(null);
  };

  const openEdit = (instance: ConnectorInstance) => {
    if (instance.kind === "custom-mcp") {
      setCustomFocus(instance.mcp_server_name);
      setMode("custom");
      return;
    }
    const entry = entryByKind.get(instance.kind);
    if (entry) {
      setDrawerEntry(entry);
      setDrawerInstance(instance);
    }
  };

  const closeDrawer = () => {
    setDrawerEntry(null);
    setDrawerInstance(null);
  };

  return (
    <div>
      <div className={styles.panelToolbar}>
        <div className={styles.panelToolbarMeta}>
          {t("apps.system.manager.connectorsSummary", {
            total: instances.length,
            configured: instances.filter((row) => row.has_credentials).length,
            defaultValue:
              "{{total}} connectors · {{configured}} with credentials",
          })}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Button
            size="small"
            type={mode === "instances" ? "primary" : "default"}
            onClick={() => setMode("instances")}
          >
            {t("apps.system.manager.connectorsInstances", "Connected")}
          </Button>
          <Button
            size="small"
            type={mode === "catalog" ? "primary" : "default"}
            icon={<Plus size={14} />}
            onClick={() => setMode("catalog")}
          >
            {t("apps.system.manager.connectorsAdd", "Add")}
          </Button>
          <Button
            size="small"
            type={mode === "custom" ? "primary" : "default"}
            onClick={() => setMode("custom")}
          >
            {t("apps.system.manager.connectorsCustom", "Custom MCP")}
          </Button>
          <Button
            size="small"
            icon={<RefreshCw size={14} />}
            loading={loading}
            onClick={() => void refresh()}
            aria-label={t("common.refresh", "Refresh")}
          />
        </div>
      </div>

      {mode === "custom" ? (
        <CustomMcpTab focusServerName={customFocus} />
      ) : loading && instances.length === 0 && catalog.length === 0 ? (
        <div className={styles.loading}>
          <Spin />
        </div>
      ) : mode === "instances" ? (
        instances.length === 0 ? (
          <Empty
            className={styles.empty}
            description={t(
              "apps.system.manager.connectorsEmpty",
              "No connectors yet.",
            )}
          >
            <Button type="primary" onClick={() => setMode("catalog")}>
              {t("apps.system.manager.connectorsAdd", "Add")}
            </Button>
          </Empty>
        ) : (
          <div className={styles.panelGrid}>
            {instances.map((instance) => (
              <ConnectorInstanceCard
                key={instance.instance_id}
                instance={instance}
                catalogEntry={entryByKind.get(instance.kind)}
                onEdit={openEdit}
                onChanged={async () => {
                  await refresh();
                  notifyConnectorsChanged();
                }}
              />
            ))}
          </div>
        )
      ) : catalog.length === 0 ? (
        <Empty
          className={styles.empty}
          description={t(
            "apps.system.manager.connectorsCatalogEmpty",
            "No connector catalogue is available.",
          )}
        />
      ) : (
        <div className={styles.panelGrid}>
          {catalog.map((entry) => (
            <ConnectorCard
              key={entry.kind}
              entry={entry}
              onConfigure={(item) => openCreate(item)}
            />
          ))}
        </div>
      )}

      <ConnectorConfigDrawer
        open={drawerEntry !== null}
        entry={drawerEntry}
        instance={drawerInstance}
        onClose={closeDrawer}
        onSaved={(created) => {
          if (created) setDrawerInstance(created);
          void refresh().then(() => notifyConnectorsChanged());
        }}
      />
    </div>
  );
}
