import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Checkbox,
  Dropdown,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import type { MenuProps, UploadFile } from "antd";
import {
  ArrowLeft,
  ChevronDown,
  Edit3,
  History,
  Info,
  Link2,
  MoreHorizontal,
  Share2,
  Sparkles,
  Trash2,
  Upload as UploadIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { message } from "../../utils/antdMessage";

import {
  appsApi,
  type AppAccessRequest,
  type AppColumn,
  type AppDetail,
  type AppFile,
  type AppGameSession,
  type AppImportMode,
  type AppPublication,
  type AppRowMeta,
  type AppSchedule,
  type AppSource,
  type AppSourceKind,
  type AppSourceTool,
} from "../../api/modules/apps";
import {
  connectorsApi,
  type ConnectorInstance,
} from "../../api/modules/connectors";
import PageShell, { pageShellStyles } from "../../layouts/PageShell";
import { fullscreenContainer } from "../../utils/overlayContainer";
import { apiErrorMessage, isNotFoundApiError } from "../../utils/apiError";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { useIsMobile } from "../../hooks/useIsMobile";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import GameFrame, {
  MINIAPP_GAME_FINISHED_EVENT,
  type MiniAppGameFinishedDetail,
} from "../../components/MiniAppFrame/GameFrame";
import {
  buildSrcDoc,
  hasEmbeddedFrame,
} from "../../components/MiniAppFrame/sdk";
import { buildWrapperDoc } from "../../components/MiniAppFrame/wrapper";
import {
  attachBridge,
  type AppViolation,
  type BridgeParams,
} from "../../components/MiniAppFrame/bridge";
import { effectiveCapabilities } from "../../components/MiniAppFrame/capabilities";
import { useMiniAppDataVersion } from "../../components/MiniAppFrame/useMiniAppDataVersion";
import AppAccessRequestsModal from "./detail/AppAccessRequestsModal";
import AppAssistantDrawer from "./detail/AppAssistantDrawer";
import AppDataTab, {
  ACTIONS_COLUMN_WIDTH,
  SOURCE_COLUMN_WIDTH,
  dataColumnWidth,
} from "./detail/AppDataTab";
import AppInfoDrawer from "./detail/AppInfoDrawer";
import AppInterfaceTab from "./detail/AppInterfaceTab";
import AppPublishModal from "./detail/AppPublishModal";
import AppShareModal from "./detail/AppShareModal";
import RowHistoryDrawer from "./detail/RowHistoryDrawer";
import { permissionLabel } from "./detail/shared";
import type { ShareDraft } from "./detail/types";

const { Text } = Typography;

const SOURCE_SCHEDULE_OPTIONS: { value: number; labelKey: string }[] = [
  { value: 0, labelKey: "apps.sourceScheduleManual" },
  { value: 300, labelKey: "apps.sourceSchedule5min" },
  { value: 1800, labelKey: "apps.sourceSchedule30min" },
  { value: 3600, labelKey: "apps.sourceSchedule1h" },
  { value: 21600, labelKey: "apps.sourceSchedule6h" },
  { value: 86400, labelKey: "apps.sourceSchedule1d" },
];

export default function AppDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { appId = "" } = useParams<{ appId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const { modal } = App.useApp();
  const isMobile = useIsMobile();
  const currentUser = useCurrentUser();
  const timeZone = useServerTimezone();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const capabilitiesRef = useRef<readonly string[] | undefined>(undefined);

  const [detail, setDetail] = useState<AppDetail | null>(null);
  // Administrative actions are owner/admin only; edit_structure collaborators
  // keep schema/data editing but cannot delete, publish, share or elevate.
  const isOwner =
    currentUser && detail
      ? detail.owner_username === currentUser.username
      : undefined;
  const isAdmin = currentUser?.role === "admin";
  const canManage =
    detail?.permission === "edit_structure" &&
    (isOwner === true || isAdmin === true);
  const [capabilityDraft, setCapabilityDraft] = useState<string[]>([]);
  const [savingCapabilities, setSavingCapabilities] = useState(false);
  const [accessRequests, setAccessRequests] = useState<AppAccessRequest[]>([]);
  const [accessOpen, setAccessOpen] = useState(false);
  const [accessBusy, setAccessBusy] = useState(false);
  const shareDeepLinkRef = useRef(false);
  useEffect(() => {
    capabilitiesRef.current = detail?.capabilities;
    setCapabilityDraft(detail?.capabilities ?? []);
  }, [detail?.capabilities]);

  const saveCapabilities = useCallback(async () => {
    if (!detail) return;
    setSavingCapabilities(true);
    try {
      const updated = await appsApi.setCapabilities(
        detail.app_id,
        capabilityDraft,
      );
      setDetail(updated);
      message.success(t("apps.capabilities.saved", "Capabilities saved"));
    } catch (err) {
      message.error(
        apiErrorMessage(
          err,
          t("apps.capabilities.saveFailed", "Could not save capabilities"),
          t,
        ),
      );
    } finally {
      setSavingCapabilities(false);
    }
  }, [capabilityDraft, detail, t]);

  const [html, setHtml] = useState("");
  const [loading, setLoading] = useState(true);
  const [htmlLoading, setHtmlLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [gameSession, setGameSession] = useState<AppGameSession | null>(null);
  const [htmlReloadKey, setHtmlReloadKey] = useState(0);
  const [appEscaped, setAppEscaped] = useState(false);
  const [htmlBlocked, setHtmlBlocked] = useState(false);

  // A remount starts a fresh document; drop the previous escape marker.
  useEffect(() => {
    setAppEscaped(false);
  }, [htmlReloadKey, appId]);

  const [renameOpen, setRenameOpen] = useState(false);
  const [renameForm] = Form.useForm<{ name: string; description: string }>();
  const [shareOpen, setShareOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [publication, setPublication] = useState<AppPublication | null>(null);
  const [publicationBusy, setPublicationBusy] = useState(false);
  const [schedules, setSchedules] = useState<AppSchedule[]>([]);
  const [scheduleBusy, setScheduleBusy] = useState(false);
  const snapshotSchedule =
    schedules.find((item) => item.action === "snapshot_publication") ?? null;
  // The API returns the public origin (scheme/host/port) the server is reached
  // at; fall back to the browser origin when the server has no public_base_url.
  const publishUrl =
    publication?.url ??
    (publication?.token
      ? `${window.location.origin}/p/${publication.token}`
      : "");
  const [shareLoading, setShareLoading] = useState(false);
  const [savingShare, setSavingShare] = useState(false);
  const [shares, setShares] = useState<ShareDraft[]>([]);
  const [infoOpen, setInfoOpen] = useState(false);
  const [files, setFiles] = useState<AppFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);

  const [assistantOpen, setAssistantOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importTable, setImportTable] = useState("main");
  const [importMode, setImportMode] = useState<AppImportMode>("append");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importIncludePii, setImportIncludePii] = useState(false);

  const [activeView, setActiveView] = useState<"ui" | "data">("ui");
  const [dataTable, setDataTable] = useState("main");
  const [dataSearch, setDataSearch] = useState("");
  const [dataRows, setDataRows] = useState<Record<string, string>[]>([]);
  const [dataRefs, setDataRefs] = useState<
    Record<string, Record<string, string>>
  >({});
  const [dataRowMeta, setDataRowMeta] = useState<AppRowMeta[]>([]);
  const [refOptions, setRefOptions] = useState<
    Record<string, { value: string; label: string }[]>
  >({});
  const [dataTotal, setDataTotal] = useState(0);
  const [dataPage, setDataPage] = useState(1);
  const [dataLoading, setDataLoading] = useState(false);
  const [rowModalOpen, setRowModalOpen] = useState(false);
  const [rowEdit, setRowEdit] = useState<{
    row_id: string;
    version: number;
  } | null>(null);
  const [rowSaving, setRowSaving] = useState(false);
  const [historyRowId, setHistoryRowId] = useState<string | null>(null);
  const [rowForm] = Form.useForm<Record<string, unknown>>();
  const dataPageSize = 50;

  const [sources, setSources] = useState<AppSource[]>([]);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [sourceTable, setSourceTable] = useState("main");
  const [sourceKind, setSourceKind] = useState<AppSourceKind>("workspace_file");
  const [sourceMode, setSourceMode] = useState<AppImportMode>("append");
  const [sourcePath, setSourcePath] = useState("");
  const [sourceInstance, setSourceInstance] = useState("");
  const [sourceTool, setSourceTool] = useState("");
  const [sourceArgs, setSourceArgs] = useState("");
  const [sourceResultPath, setSourceResultPath] = useState("");
  const [sourceCursorArg, setSourceCursorArg] = useState("");
  const [sourceCursorPath, setSourceCursorPath] = useState("");
  const [sourceIncludePii, setSourceIncludePii] = useState(false);
  const [sourceMapping, setSourceMapping] = useState("");
  const [sourceSchedule, setSourceSchedule] = useState(0);
  const [sourceTools, setSourceTools] = useState<AppSourceTool[]>([]);
  const [sourceToolsLoading, setSourceToolsLoading] = useState(false);
  const [sourceInstances, setSourceInstances] = useState<ConnectorInstance[]>(
    [],
  );
  const [sourceEditId, setSourceEditId] = useState<string | null>(null);
  const [sourceSaving, setSourceSaving] = useState(false);

  const loadDetail = useCallback(async () => {
    setLoading(true);
    try {
      const data = await appsApi.get(appId);
      setDetail(data);
      setNotFound(false);
    } catch (err) {
      if (isNotFoundApiError(err)) {
        setNotFound(true);
      } else {
        message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
      }
    } finally {
      setLoading(false);
    }
  }, [appId, t]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  useEffect(() => {
    if (!detail?.has_html) {
      setHtml("");
      return;
    }
    let alive = true;
    setHtmlLoading(true);
    setHtmlBlocked(false);
    appsApi
      .getHtml(appId)
      .then((text) => {
        if (!alive) return;
        if (hasEmbeddedFrame(text)) {
          setHtml("");
          setHtmlBlocked(true);
          return;
        }
        setHtml(text);
      })
      .catch((err) => {
        if (alive)
          message.error(apiErrorMessage(err, t("apps.loadHtmlFailed"), t));
      })
      .finally(() => {
        if (alive) setHtmlLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [appId, detail?.has_html, t]);

  // A game wrote rows in its host: remount the app so it re-reads them.
  useEffect(() => {
    const onGameEvent = (event: Event) => {
      const detail = (event as CustomEvent<MiniAppGameFinishedDetail>).detail;
      if (!detail || detail.appId !== appId) return;
      setHtmlReloadKey((value) => value + 1);
    };
    window.addEventListener(MINIAPP_GAME_FINISHED_EVENT, onGameEvent);
    return () =>
      window.removeEventListener(MINIAPP_GAME_FINISHED_EVENT, onGameEvent);
  }, [appId]);

  const reportViolation = useCallback(
    (violation: AppViolation) => {
      // Telemetry only: a failed report must not disturb the page.
      void appsApi.reportViolation(appId, violation).catch(() => {});
    },
    [appId],
  );

  // The wrapper reports that the app frame navigated away. Stop there: the
  // escaping page would otherwise inherit the bridge and could read every row
  // through the host.
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const frame = iframeRef.current;
      if (!frame || event.source !== frame.contentWindow) return;
      const data = event.data as { source?: string; type?: string } | null;
      if (!data || data.source !== "octop-app-host") return;
      if (data.type === "escaped") setAppEscaped(true);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    const detach = attachBridge(
      {
        // Read the ref on every request: the owner can edit the manifest from
        // this page without remounting the frame.
        capabilities: () => capabilitiesRef.current,
        onViolation: reportViolation,
        handle: async (method: string, params: BridgeParams) => {
          if (method === "me") {
            const current = await appsApi.get(appId);
            return {
              permission: current.permission,
              capabilities: effectiveCapabilities(
                current.capabilities ?? capabilitiesRef.current,
              ),
            };
          }
          if (method === "schema") {
            const current = await appsApi.get(appId);
            const tableKey = params.table || "main";
            if (tableKey === "main") {
              return { table: tableKey, columns: current.columns };
            }
            const table = current.tables.find(
              (item) => item.table_key === tableKey,
            );
            if (!table) throw new Error(`unknown table: ${tableKey}`);
            return { table: tableKey, columns: table.columns };
          }
          if (method === "tables") {
            const current = await appsApi.get(appId);
            return {
              tables: current.tables.map((item) => ({
                table_key: item.table_key,
                label: item.label,
                row_count: item.row_count,
                is_main: item.is_main,
              })),
            };
          }
          if (method === "rows") {
            return await appsApi.rows(
              appId,
              params.limit,
              params.offset,
              params.table || "main",
              params.filters,
              params.sort,
              params.expandRefs === true,
            );
          }
          if (method === "append") {
            const tableKey = params.table || "main";
            const result = await appsApi.appendRow(
              appId,
              params.row ?? {},
              tableKey,
            );
            setDetail((prev) => {
              if (!prev) return prev;
              if (tableKey === "main") {
                return { ...prev, row_count: prev.row_count + 1 };
              }
              return {
                ...prev,
                tables: prev.tables.map((item) =>
                  item.table_key === tableKey
                    ? { ...item, row_count: item.row_count + 1 }
                    : item,
                ),
              };
            });
            return result;
          }
          if (method === "update") {
            return await appsApi.updateRow(
              appId,
              String(params.rowId ?? ""),
              params.row ?? {},
              params.expectedVersion,
            );
          }
          if (method === "updateMany") {
            // One request per row: each write keeps its own CAS check and its
            // own row scope, so a batch never turns into one big transaction.
            const items = Array.isArray(params.rows) ? params.rows : [];
            const failures: { row_id: string; error: string }[] = [];
            let updated = 0;
            for (const item of items) {
              const rowId = String(item?.row_id ?? "");
              try {
                await appsApi.updateRow(
                  appId,
                  rowId,
                  item?.row ?? {},
                  item?.expected_version,
                );
                updated += 1;
              } catch (err) {
                failures.push({
                  row_id: rowId,
                  error: err instanceof Error ? err.message : String(err),
                });
              }
            }
            return { updated, failed: failures.length, failures };
          }
          if (method === "remove") {
            const result = await appsApi.deleteRow(
              appId,
              String(params.rowId ?? ""),
              params.expectedVersion,
            );
            // The row id alone names the table on the server, so refresh the
            // counts instead of guessing which table just shrank.
            void loadDetail();
            return result;
          }
          if (method === "generate") {
            return await appsApi.generate(appId, {
              prompt: String(params.prompt ?? ""),
              system: String(params.system ?? ""),
            });
          }
          if (method === "composeMessage") {
            const text = String(params.text ?? "").trim();
            if (!text) {
              throw new Error(t("apps.composeEmpty", "Message is empty"));
            }
            if (text.length > 4000) {
              throw new Error(t("apps.composeTooLong", "Message is too long"));
            }
            const current = await appsApi.get(appId);
            navigate(`/chat/${encodeURIComponent(current.agent_id)}`, {
              state: { prefillInput: text },
            });
            return { filled: true };
          }
          if (method === "launchGame") {
            const gameId = String(params.gameId ?? "").trim();
            if (!gameId) throw new Error("gameId is required");
            const session = await appsApi.createGameSession(appId, gameId);
            setGameSession(session);
            return { sessionId: session.session_id, status: "opened" };
          }
          if (method === "openExternal") {
            const url = String(params.url ?? "").trim();
            if (!/^https?:\/\//i.test(url)) {
              throw new Error("openExternal requires an http(s) URL");
            }
            // The app page runs sandboxed without popups, so the parent has
            // to open the link — after the user confirms the target domain.
            Modal.confirm({
              getContainer: fullscreenContainer,
              title: t("apps.openExternalTitle"),
              content: url,
              okText: t("apps.openExternalConfirm"),
              cancelText: t("common.cancel"),
              onOk: () => {
                window.open(url, "_blank", "noopener,noreferrer");
              },
            });
            return { opened: true };
          }
          if (method === "events") {
            const since = Number(params.since) || 0;
            const limit = Number(params.limit) || 100;
            return appsApi.events(appId, since, limit);
          }
          if (method === "ackEvents") {
            return appsApi.ackEvents(appId, params.event_ids || []);
          }
          if (method === "service") {
            const name = String(params.name ?? "").trim();
            const tool = String(params.tool ?? "").trim();
            const args =
              params.args &&
              typeof params.args === "object" &&
              !Array.isArray(params.args)
                ? params.args
                : {};
            const result = await appsApi.callService(appId, name, tool, args);
            return result.value;
          }
          if (method === "serviceImage") {
            const name = String(params.name ?? "").trim();
            const url = String(params.url ?? "").trim();
            const result = await appsApi.serviceImage(appId, name, url);
            return result.asset;
          }
          if (method === "asset") {
            const name = String(params.name ?? "").trim();
            // The parent fetches the asset with the JWT and hands the iframe
            // a data: URL — the sandbox has no same-origin access, so a blob
            // URL from this document would not be readable there.
            const blob = await appsApi.asset(appId, name);
            const mime = blob.type || "image/png";
            const reader = new FileReader();
            const dataUrl = await new Promise<string>((resolve, reject) => {
              reader.onload = () => resolve(String(reader.result));
              reader.onerror = () => reject(reader.error);
              reader.readAsDataURL(blob);
            });
            return { name, mime, data_url: dataUrl };
          }
          throw new Error("unsupported method");
        },
      },
      () => iframeRef.current?.contentWindow ?? null,
    );
    return detach;
  }, [appId, loadDetail, navigate, reportViolation, t]);

  const srcDoc = useMemo(
    () => (html ? buildWrapperDoc(buildSrcDoc(html)) : ""),
    [html],
  );

  const dataColumns: AppColumn[] =
    detail?.tables.find((item) => item.table_key === dataTable)?.columns ??
    detail?.columns ??
    [];
  const hasReferenceColumns = dataColumns.some(
    (column) => column.type === "reference",
  );
  const canEditRows = detail?.permission !== "view";
  const dataTableWidth =
    dataColumns.reduce((sum, column) => sum + dataColumnWidth(column.type), 0) +
    SOURCE_COLUMN_WIDTH +
    ACTIONS_COLUMN_WIDTH;
  const dataQuery = dataSearch.trim().toLowerCase();
  const visibleRows = dataRows
    .map((row, index) => ({ row, meta: dataRowMeta[index] }))
    .filter(
      ({ row }) =>
        !dataQuery ||
        Object.values(row).some((value) =>
          value.toLowerCase().includes(dataQuery),
        ),
    );

  const loadData = useCallback(
    async (page: number) => {
      if (!appId) return;
      setDataLoading(true);
      try {
        const result = await appsApi.rows(
          appId,
          dataPageSize,
          (page - 1) * dataPageSize,
          dataTable,
          undefined,
          undefined,
          hasReferenceColumns,
        );
        setDataRows(result.rows);
        setDataRefs(result.refs ?? {});
        setDataRowMeta(result.row_meta);
        setDataTotal(result.total);
        setDataPage(page);
      } catch (err) {
        message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
      } finally {
        setDataLoading(false);
      }
    },
    [appId, dataTable, hasReferenceColumns, t],
  );

  useEffect(() => {
    if (activeView === "data") void loadData(1);
  }, [activeView, dataTable, loadData]);

  // Keep the dashboard data tab in sync with writes from other windows. The
  // same poll also forwards `data-changed` to the app iframe.
  const dataVersion = useMiniAppDataVersion(
    appId,
    iframeRef,
    Boolean(html) || activeView === "data",
  );
  const seenDataVersion = useRef(0);
  useEffect(() => {
    if (dataVersion === seenDataVersion.current) return;
    seenDataVersion.current = dataVersion;
    if (activeView === "data") void loadData(dataPage);
  }, [dataVersion, activeView, dataPage, loadData]);

  const loadReferenceOptions = async () => {
    if (!appId) return;
    const next: Record<string, { value: string; label: string }[]> = {};
    await Promise.all(
      dataColumns
        .filter((column) => column.type === "reference" && column.ref_table)
        .map(async (column) => {
          try {
            const result = await appsApi.rows(
              appId,
              500,
              0,
              column.ref_table as string,
            );
            const labelColumn =
              column.ref_label ||
              result.columns.find(
                (item) => item.type === "string" || item.type === "text",
              )?.name;
            next[column.name] = result.rows
              .map((row, index) => {
                const rowId = result.row_meta[index]?.row_id ?? "";
                return {
                  value: rowId,
                  label: (labelColumn && row[labelColumn]) || rowId,
                };
              })
              .filter((item) => item.value);
          } catch {
            next[column.name] = [];
          }
        }),
    );
    setRefOptions(next);
  };

  const openAddRow = () => {
    setRowEdit(null);
    rowForm.resetFields();
    setRowModalOpen(true);
    void loadReferenceOptions();
  };

  const openEditRow = (
    meta: AppRowMeta | undefined,
    values: Record<string, string>,
  ) => {
    if (!meta?.row_id) return;
    setRowEdit(meta);
    rowForm.setFieldsValue(values);
    setRowModalOpen(true);
    void loadReferenceOptions();
  };

  const submitRow = async () => {
    if (!appId) return;
    const values = await rowForm.validateFields();
    setRowSaving(true);
    try {
      if (rowEdit) {
        await appsApi.updateRow(appId, rowEdit.row_id, values, rowEdit.version);
      } else {
        await appsApi.appendRow(appId, values, dataTable);
      }
      setRowModalOpen(false);
      message.success(t("apps.rowSaved"));
      await loadData(dataPage);
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.rowConflict"), t));
    } finally {
      setRowSaving(false);
    }
  };

  const removeRow = async (meta: AppRowMeta | undefined) => {
    if (!appId || !meta?.row_id) return;
    try {
      await appsApi.deleteRow(appId, meta.row_id, meta.version);
      message.success(t("apps.rowDeleted"));
      const nextPage =
        dataRows.length === 1 && dataPage > 1 ? dataPage - 1 : dataPage;
      await loadData(nextPage);
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.rowConflict"), t));
    }
  };

  const exportData = async () => {
    if (!appId) return;
    try {
      const blob = await appsApi.exportTable(appId, dataTable);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${dataTable}.csv`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
    }
  };

  const backupApp = async () => {
    if (!appId) return;
    try {
      const blob = await appsApi.backup(appId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${detail?.name || appId}-backup.zip`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
    }
  };

  const setTableVisibility = async (
    tableKey: string,
    publicVisible: boolean,
  ) => {
    try {
      const updated = await appsApi.setTableVisibility(
        appId,
        tableKey,
        publicVisible,
      );
      setDetail((prev) =>
        prev
          ? {
              ...prev,
              tables: prev.tables.map((item) =>
                item.table_key === tableKey ? updated : item,
              ),
            }
          : prev,
      );
      message.success(t("apps.saveSuccess"));
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.saveFailed"), t));
    }
  };

  const openRename = () => {
    if (!detail) return;
    renameForm.setFieldsValue({
      name: detail.name,
      description: detail.description,
    });
    setRenameOpen(true);
  };

  const submitRename = async (values: {
    name: string;
    description: string;
  }) => {
    try {
      const updated = await appsApi.update(appId, {
        name: values.name,
        description: values.description ?? "",
      });
      setDetail(updated);
      setRenameOpen(false);
      message.success(t("apps.saveSuccess"));
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.saveFailed"), t));
    }
  };

  const openShare = useCallback(async () => {
    setShareOpen(true);
    setShareLoading(true);
    try {
      const data = await appsApi.shares(appId);
      setShares(
        data.shares.map((item) => ({
          username: item.username,
          permission: item.permission,
        })),
      );
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
      setShareOpen(false);
    } finally {
      setShareLoading(false);
    }
  }, [appId, t]);

  const refreshAccessRequests = useCallback(async () => {
    try {
      const data = await appsApi.accessRequests({
        scope: "owner",
        app_id: appId,
      });
      setAccessRequests(data.requests);
    } catch {
      // A failed poll must not block the app page.
    }
  }, [appId]);

  useEffect(() => {
    if (!detail || !canManage) return;
    void refreshAccessRequests();
  }, [detail, canManage, refreshAccessRequests]);

  // The permission card links the owner here with ?share=1 to open the
  // collaborator dialog directly.
  useEffect(() => {
    if (shareDeepLinkRef.current || !detail || !canManage) return;
    if (searchParams.get("share") !== "1") return;
    shareDeepLinkRef.current = true;
    void openShare();
    const next = new URLSearchParams(searchParams);
    next.delete("share");
    setSearchParams(next, { replace: true });
  }, [detail, canManage, openShare, searchParams, setSearchParams]);

  const resolveAccess = async (request: AppAccessRequest, approve: boolean) => {
    setAccessBusy(true);
    try {
      const updated = approve
        ? await appsApi.approveAccessRequest(request.request_id)
        : await appsApi.rejectAccessRequest(request.request_id);
      if (approve && updated.status === "pending") {
        message.info(
          t(
            "apps.accessRequests.partiallyApproved",
            "Your part is done; waiting for the other owner.",
          ),
        );
        return;
      }
      message.success(
        t(
          approve
            ? "apps.accessRequests.approved"
            : "apps.accessRequests.rejected",
          approve ? "Access approved" : "Access rejected",
        ),
      );
      setAccessRequests((prev) =>
        prev.filter((item) => item.request_id !== request.request_id),
      );
    } catch (err) {
      message.error(
        apiErrorMessage(
          err,
          t("apps.accessRequests.failed", "Could not resolve the request"),
          t,
        ),
      );
    } finally {
      setAccessBusy(false);
    }
  };

  const openPublish = async () => {
    setPublishOpen(true);
    setPublicationBusy(true);
    try {
      setPublication(await appsApi.publication(appId));
      try {
        setSchedules((await appsApi.listSchedules(appId)).schedules);
      } catch {
        setSchedules([]);
      }
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
      setPublishOpen(false);
    } finally {
      setPublicationBusy(false);
    }
  };

  const toggleSnapshotSchedule = async (enabled: boolean) => {
    setScheduleBusy(true);
    try {
      if (snapshotSchedule) {
        const updated = await appsApi.updateSchedule(
          appId,
          snapshotSchedule.schedule_id,
          {
            interval_seconds: snapshotSchedule.interval_seconds,
            enabled,
          },
        );
        setSchedules((prev) =>
          prev.map((item) =>
            item.schedule_id === updated.schedule_id ? updated : item,
          ),
        );
      } else if (enabled) {
        const created = await appsApi.saveSchedule(appId, {
          action: "snapshot_publication",
          interval_seconds: 3600,
          enabled: true,
        });
        setSchedules((prev) => [...prev, created]);
      }
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
    } finally {
      setScheduleBusy(false);
    }
  };

  const changeSnapshotInterval = async (seconds: number) => {
    if (!snapshotSchedule) return;
    setScheduleBusy(true);
    try {
      const updated = await appsApi.updateSchedule(
        appId,
        snapshotSchedule.schedule_id,
        { interval_seconds: seconds, enabled: true },
      );
      setSchedules((prev) =>
        prev.map((item) =>
          item.schedule_id === updated.schedule_id ? updated : item,
        ),
      );
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
    } finally {
      setScheduleBusy(false);
    }
  };

  const createPublish = async () => {
    setPublicationBusy(true);
    try {
      setPublication(await appsApi.createPublication(appId));
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
    } finally {
      setPublicationBusy(false);
    }
  };

  const publishNow = async () => {
    setPublicationBusy(true);
    try {
      setPublication(await appsApi.publish(appId));
      message.success(t("apps.publishUpdated", "Public version updated"));
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
    } finally {
      setPublicationBusy(false);
    }
  };

  const revokePublish = async () => {
    setPublicationBusy(true);
    try {
      setPublication(await appsApi.revokePublication(appId));
      setSchedules((prev) =>
        prev.filter((item) => item.action !== "snapshot_publication"),
      );
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
    } finally {
      setPublicationBusy(false);
    }
  };

  const restoreHtml = async () => {
    try {
      const updated = await appsApi.restoreHtml(appId);
      setDetail(updated);
      setHtml(await appsApi.getHtml(appId));
      setHtmlReloadKey((value) => value + 1);
      message.success(t("apps.restoreHtmlDone", "Previous version restored"));
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
    }
  };

  const openInfo = async () => {
    setInfoOpen(true);
    setFilesLoading(true);
    try {
      const [filesData] = await Promise.all([
        appsApi.files(appId),
        appsApi
          .listSources(appId)
          .then((data) => setSources(data.sources))
          .catch(() => setSources([])),
      ]);
      setFiles(filesData.files);
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.loadFailed"), t));
      setFiles([]);
    } finally {
      setFilesLoading(false);
    }
  };

  const openImport = (tableKey: string) => {
    setImportTable(tableKey);
    setImportMode("append");
    setImportFile(null);
    setImportOpen(true);
  };

  const submitImport = async () => {
    if (!importFile || importBusy) return;
    setImportBusy(true);
    try {
      const stored = await appsApi.uploadImportFile(appId, importFile);
      const result = await appsApi.importTable(appId, importTable, {
        kind: "upload",
        path: stored.path,
        mode: importMode,
        include_pii: importIncludePii,
      });
      message.success(
        t("apps.importSuccess", {
          imported: result.imported,
          skipped: result.skipped,
        }),
      );
      if (result.blocked_pii.length > 0) {
        message.warning(
          t("apps.piiBlocked", { columns: result.blocked_pii.join(", ") }),
        );
      }
      if (result.failed_rows.length > 0) {
        const blob = new Blob(
          [
            JSON.stringify(
              result.failed_rows.map((item) => item.values),
              null,
              2,
            ),
          ],
          { type: "application/json" },
        );
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "failed-rows.json";
        link.click();
        URL.revokeObjectURL(url);
        message.warning(
          t("apps.importFailedRows", { count: result.failed_rows.length }),
        );
      }
      setImportOpen(false);
      setImportFile(null);
      setImportIncludePii(false);
      void loadDetail();
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.importFailed"), t));
    } finally {
      setImportBusy(false);
    }
  };

  const loadSourceTools = useCallback(
    async (instanceId: string) => {
      if (!instanceId) {
        setSourceTools([]);
        return;
      }
      setSourceToolsLoading(true);
      try {
        const data = await appsApi.sourceTools(appId, instanceId);
        setSourceTools(data.tools);
      } catch {
        setSourceTools([]);
      } finally {
        setSourceToolsLoading(false);
      }
    },
    [appId],
  );

  const openSourceModal = (tableKey: string) => {
    const existing =
      sources.find((item) => item.table_key === tableKey) ?? null;
    setSourceTable(tableKey);
    setSourceEditId(existing?.source_id ?? null);
    setSourceKind(existing?.kind ?? "workspace_file");
    setSourceMode(existing?.mode ?? "append");
    setSourcePath(
      existing?.kind === "workspace_file"
        ? String(existing.params.path || "")
        : "",
    );
    setSourceInstance(
      existing?.kind === "external"
        ? String(existing.params.instance_id || "")
        : "",
    );
    setSourceTool(
      existing?.kind === "external" ? String(existing.params.tool || "") : "",
    );
    setSourceArgs(
      existing?.kind === "external" && existing.params.args
        ? JSON.stringify(existing.params.args, null, 2)
        : "",
    );
    setSourceResultPath(
      existing?.kind === "external"
        ? String(existing.params.result_path || "")
        : "",
    );
    setSourceCursorArg(
      existing?.kind === "external"
        ? String(existing.params.cursor_arg || "")
        : "",
    );
    setSourceCursorPath(
      existing?.kind === "external"
        ? String(existing.params.cursor_path || "")
        : "",
    );
    setSourceIncludePii(existing?.params.include_pii === true);
    setSourceMapping(
      existing && Object.keys(existing.mapping).length
        ? JSON.stringify(existing.mapping, null, 2)
        : "",
    );
    setSourceSchedule(existing?.schedule_seconds ?? 0);
    setSourceTools([]);
    setSourceInstances([]);
    setSourceOpen(true);
    void connectorsApi
      .listInstances()
      .then((data) => setSourceInstances(data))
      .catch(() => setSourceInstances([]));
    if (existing?.kind === "external") {
      void loadSourceTools(String(existing.params.instance_id || ""));
    }
  };

  const submitSource = async () => {
    if (sourceSaving) return;
    let args: Record<string, unknown> = {};
    if (sourceKind === "external" && sourceArgs.trim()) {
      try {
        args = JSON.parse(sourceArgs) as Record<string, unknown>;
      } catch {
        message.error(t("apps.sourceArgsInvalid"));
        return;
      }
    }
    let mapping: Record<string, string> = {};
    if (sourceMapping.trim()) {
      try {
        mapping = JSON.parse(sourceMapping) as Record<string, string>;
      } catch {
        message.error(t("apps.sourceArgsInvalid"));
        return;
      }
    }
    const params: Record<string, unknown> =
      sourceKind === "workspace_file"
        ? { path: sourcePath.trim() }
        : {
            instance_id: sourceInstance,
            tool: sourceTool,
            ...(Object.keys(args).length ? { args } : {}),
            ...(sourceResultPath.trim()
              ? { result_path: sourceResultPath.trim() }
              : {}),
            ...(sourceCursorArg.trim()
              ? { cursor_arg: sourceCursorArg.trim() }
              : {}),
            ...(sourceCursorPath.trim()
              ? { cursor_path: sourceCursorPath.trim() }
              : {}),
            ...(sourceIncludePii ? { include_pii: true } : {}),
          };
    const sourceId =
      sourceEditId ?? `src_${Math.random().toString(36).slice(2, 10)}`;
    setSourceSaving(true);
    try {
      await appsApi.saveSource(appId, sourceId, {
        table_key: sourceTable,
        kind: sourceKind,
        mode: sourceMode,
        schedule_seconds: sourceSchedule,
        params,
        mapping,
      });
      message.success(t("apps.sourceSaveSuccess"));
      setSourceOpen(false);
      const data = await appsApi.listSources(appId);
      setSources(data.sources);
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.sourceSaveFailed"), t));
    } finally {
      setSourceSaving(false);
    }
  };

  const runSourceNow = async (source: AppSource) => {
    try {
      const result = await appsApi.runSource(appId, source.source_id);
      message.success(
        t("apps.sourceRunSuccess", {
          imported: result.imported,
          skipped: result.skipped,
        }),
      );
      const data = await appsApi.listSources(appId);
      setSources(data.sources);
      void loadDetail();
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.sourceRunFailed"), t));
    }
  };

  const removeSource = async (source: AppSource) => {
    try {
      await appsApi.deleteSource(appId, source.source_id);
      message.success(t("apps.sourceDeleteSuccess"));
      setSources(sources.filter((item) => item.source_id !== source.source_id));
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.sourceDeleteFailed"), t));
    }
  };

  const submitShare = async () => {
    const payload = shares
      .filter((item) => item.username.trim())
      .map((item) => ({
        username: item.username.trim(),
        permission: item.permission,
      }));
    setSavingShare(true);
    try {
      await appsApi.setShares(appId, payload);
      message.success(t("apps.saveSuccess"));
      setShareOpen(false);
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.saveFailed"), t));
    } finally {
      setSavingShare(false);
    }
  };

  const removeApp = async () => {
    try {
      await appsApi.remove(appId);
      message.success(t("apps.deleteSuccess"));
      navigate("/apps", { replace: true });
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.deleteFailed"), t));
    }
  };

  const confirmRestoreHtml = () => {
    modal.confirm({
      title: t("apps.restoreHtmlTitle", "Restore the previous version?"),
      content: t(
        "apps.restoreHtmlConfirm",
        "The current HTML will become the previous version.",
      ),
      okText: t("apps.restoreHtml", "Restore previous"),
      cancelText: t("common.cancel"),
      onOk: () => restoreHtml(),
    });
  };

  const confirmDeleteApp = () => {
    modal.confirm({
      title: t("apps.deleteTitle"),
      content: t("apps.deleteConfirm"),
      okText: t("apps.delete"),
      cancelText: t("common.cancel"),
      okButtonProps: { danger: true },
      onOk: () => removeApp(),
    });
  };

  if (loading) {
    return (
      <PageShell title={t("apps.title")}>
        <div style={{ padding: 48, textAlign: "center" }}>
          <Spin />
        </div>
      </PageShell>
    );
  }

  if (notFound || !detail) {
    return (
      <PageShell title={t("apps.title")}>
        <Empty description={t("apps.notFound")} style={{ padding: "64px 0" }}>
          <Button onClick={() => navigate("/apps")}>{t("apps.back")}</Button>
        </Empty>
      </PageShell>
    );
  }

  const canEditStructure = detail.permission === "edit_structure";
  const canEditData = detail.permission !== "view";
  // `edit_structure` covers both the owner and admins; only the owner should
  // see the permissions tag, while admins get an explicit label.
  const permissionText = permissionLabel(detail.permission, t, isOwner);
  const showPermissionTag =
    detail.permission !== "edit_structure" || isOwner === false;

  const sharePublishMenu: MenuProps = {
    items: [
      { key: "share", icon: <Share2 size={14} />, label: t("apps.share") },
      {
        key: "publish",
        icon: <Link2 size={14} />,
        label: t("apps.publishLink"),
      },
    ],
    onClick: ({ key }) => {
      if (key === "share") void openShare();
      else if (key === "publish") void openPublish();
    },
  };

  const moreMenu: MenuProps = {
    items: [
      { key: "rename", icon: <Edit3 size={14} />, label: t("apps.rename") },
      ...(detail.previous_html_at
        ? [
            {
              key: "restore",
              icon: <History size={14} />,
              label: t("apps.restoreHtml"),
            },
          ]
        : []),
      ...(canManage
        ? [
            { type: "divider" as const },
            {
              key: "delete",
              icon: <Trash2 size={14} />,
              label: t("apps.delete"),
              danger: true,
            },
          ]
        : []),
    ],
    onClick: ({ key }) => {
      if (key === "rename") openRename();
      else if (key === "restore") confirmRestoreHtml();
      else if (key === "delete") confirmDeleteApp();
    },
  };

  return (
    <PageShell
      title={detail.name}
      subtitle={detail.description || undefined}
      leading={
        <Tooltip title={t("apps.back")}>
          <Button
            type="text"
            icon={<ArrowLeft size={18} />}
            aria-label={t("apps.back")}
            onClick={() => navigate("/apps")}
          />
        </Tooltip>
      }
      titleSuffix={
        showPermissionTag ? (
          <Tag style={{ marginInlineEnd: 0 }}>{permissionText}</Tag>
        ) : undefined
      }
      fill
      actions={
        <Space size={isMobile ? 4 : 8}>
          <Tooltip title={t("apps.assistantTitle")}>
            <Button
              type="text"
              icon={<Sparkles size={16} />}
              aria-label={t("apps.assistant")}
              onClick={() => setAssistantOpen(true)}
            >
              {isMobile ? null : t("apps.assistant")}
            </Button>
          </Tooltip>
          {canManage && (
            <Dropdown menu={sharePublishMenu} trigger={["click"]}>
              <Button
                icon={<Share2 size={16} />}
                aria-label={t("apps.shareAndPublish")}
              >
                {isMobile ? null : (
                  <>
                    {t("apps.shareAndPublish")}
                    <ChevronDown size={14} />
                  </>
                )}
              </Button>
            </Dropdown>
          )}
          <Tooltip title={t("apps.infoTitle")}>
            <Button
              type="text"
              icon={<Info size={16} />}
              aria-label={t("apps.info")}
              onClick={() => void openInfo()}
            >
              {isMobile ? null : t("apps.info")}
            </Button>
          </Tooltip>
          {canEditStructure && (
            <Dropdown menu={moreMenu} trigger={["click"]}>
              <Button
                type="text"
                icon={<MoreHorizontal size={16} />}
                aria-label={t("common.more")}
              />
            </Dropdown>
          )}
        </Space>
      }
    >
      <div className={pageShellStyles.fillTabs}>
        {canManage && accessRequests.length > 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12, flex: "none" }}
            message={
              <Space size={8} wrap>
                <Text>
                  {t("apps.accessRequests.pending", "Access requests waiting")}
                </Text>
                <Tag style={{ marginInlineEnd: 0 }}>
                  {accessRequests.length}
                </Tag>
                <Button size="small" onClick={() => setAccessOpen(true)}>
                  {t("apps.accessRequests.review", "Review")}
                </Button>
              </Space>
            }
          />
        )}
        <Tabs
          activeKey={activeView}
          onChange={(key) => setActiveView(key as "ui" | "data")}
          items={[
            {
              key: "ui",
              label: t("apps.uiTab"),
              children: (
                <AppInterfaceTab
                  loading={htmlLoading}
                  escaped={appEscaped}
                  blocked={htmlBlocked}
                  hasHtml={detail.has_html}
                  html={html}
                  reloadKey={htmlReloadKey}
                  frameRef={iframeRef}
                  srcDoc={srcDoc}
                  onOpenChat={() =>
                    navigate(`/chat/${encodeURIComponent(detail.agent_id)}`)
                  }
                />
              ),
            },
            {
              key: "data",
              label: t("apps.dataTab"),
              children: (
                <AppDataTab
                  tables={detail.tables}
                  dataTable={dataTable}
                  onTableChange={(value) => {
                    setDataTable(value);
                    setDataPage(1);
                  }}
                  canEditRows={canEditRows}
                  columns={dataColumns}
                  rows={visibleRows}
                  refs={dataRefs}
                  loading={dataLoading}
                  page={dataPage}
                  pageSize={dataPageSize}
                  total={dataTotal}
                  tableWidth={dataTableWidth}
                  search={dataSearch}
                  onSearchChange={setDataSearch}
                  onPageChange={(page) => void loadData(page)}
                  onAddRow={openAddRow}
                  onExport={() => void exportData()}
                  onBackup={() => void backupApp()}
                  onEditRow={openEditRow}
                  onDeleteRow={(meta) => void removeRow(meta)}
                  onViewHistory={(meta) =>
                    meta?.row_id ? setHistoryRowId(meta.row_id) : undefined
                  }
                />
              ),
            },
          ]}
        />
      </div>

      <Modal
        title={rowEdit ? t("apps.rowEditTitle") : t("apps.rowAddTitle")}
        open={rowModalOpen}
        onCancel={() => setRowModalOpen(false)}
        onOk={() => void submitRow()}
        okText={t("apps.save")}
        cancelText={t("common.cancel")}
        confirmLoading={rowSaving}
        destroyOnHidden
      >
        <Form form={rowForm} layout="vertical">
          {dataColumns.map((column) => (
            <Form.Item
              key={column.name}
              name={column.name}
              label={column.label || column.name}
              extra={
                column.type === "reference"
                  ? t("apps.referenceOptionsHint")
                  : undefined
              }
              rules={
                column.required
                  ? [{ required: true, message: t("apps.requiredField") }]
                  : []
              }
            >
              {column.type === "enum" ? (
                <Select
                  allowClear={!column.required}
                  options={column.values.map((value) => ({
                    value,
                    label: value,
                  }))}
                />
              ) : column.type === "reference" ? (
                <Select
                  allowClear={!column.required}
                  showSearch
                  optionFilterProp="label"
                  options={refOptions[column.name] ?? []}
                  placeholder={t("apps.referenceSelect")}
                />
              ) : (
                <Input
                  placeholder={
                    column.type === "date"
                      ? "YYYY-MM-DD"
                      : column.type === "boolean"
                      ? "true / false"
                      : undefined
                  }
                />
              )}
            </Form.Item>
          ))}
        </Form>
      </Modal>

      <AppPublishModal
        open={publishOpen}
        tables={detail.tables}
        publication={publication}
        publishUrl={publishUrl}
        busy={publicationBusy}
        timeZone={timeZone}
        snapshotSchedule={snapshotSchedule}
        scheduleBusy={scheduleBusy}
        onToggleSnapshotSchedule={(enabled) =>
          void toggleSnapshotSchedule(enabled)
        }
        onSnapshotIntervalChange={(seconds) =>
          void changeSnapshotInterval(seconds)
        }
        onClose={() => setPublishOpen(false)}
        onCreate={() => void createPublish()}
        onPublishNow={() => void publishNow()}
        onRevoke={() => void revokePublish()}
      />

      <Modal
        title={t("apps.renameTitle")}
        open={renameOpen}
        onCancel={() => setRenameOpen(false)}
        onOk={() => renameForm.submit()}
        okText={t("apps.save")}
        cancelText={t("common.cancel")}
        destroyOnHidden
      >
        <Form form={renameForm} layout="vertical" onFinish={submitRename}>
          <Form.Item
            name="name"
            label={t("apps.name")}
            rules={[{ required: true, message: t("apps.nameRequired") }]}
          >
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="description" label={t("apps.description")}>
            <Input.TextArea
              maxLength={2000}
              autoSize={{ minRows: 2, maxRows: 6 }}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={t("apps.importTitle")}
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        onOk={() => void submitImport()}
        okText={t("apps.importSubmit")}
        cancelText={t("common.cancel")}
        okButtonProps={{ disabled: !importFile, loading: importBusy }}
        destroyOnHidden
      >
        <Form layout="vertical">
          <Form.Item label={t("apps.importTableLabel")}>
            <Select
              value={importTable}
              onChange={(value) => setImportTable(value)}
              options={detail.tables.map((item) => ({
                value: item.table_key,
                label: item.label || item.table_key,
              }))}
            />
          </Form.Item>
          <Form.Item
            label={t("apps.importFileLabel")}
            extra={t("apps.importFileHint")}
          >
            <Upload
              maxCount={1}
              beforeUpload={(file) => {
                setImportFile(file);
                return false;
              }}
              onRemove={() => setImportFile(null)}
              fileList={
                importFile
                  ? [
                      {
                        uid: "-1",
                        name: importFile.name,
                        status: "done",
                      } as UploadFile,
                    ]
                  : []
              }
            >
              <Button icon={<UploadIcon size={14} />}>
                {t("apps.importChooseFile")}
              </Button>
            </Upload>
          </Form.Item>
          <Form.Item label={t("apps.importMode")}>
            <Select
              value={importMode}
              onChange={(value) => setImportMode(value)}
              options={[
                { value: "append", label: t("apps.importModeAppend") },
                {
                  value: "replace",
                  label: t("apps.importModeReplace"),
                  disabled: !canEditStructure,
                },
              ]}
            />
          </Form.Item>
          <Form.Item>
            <Checkbox
              checked={importIncludePii}
              onChange={(event) => setImportIncludePii(event.target.checked)}
            >
              {t("apps.importIncludePii")}
            </Checkbox>
            <Text type="secondary" style={{ display: "block", fontSize: 12 }}>
              {t("apps.importIncludePiiHint")}
            </Text>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={t("apps.sourceTitle")}
        open={sourceOpen}
        onCancel={() => setSourceOpen(false)}
        onOk={() => void submitSource()}
        okText={t("apps.sourceSave")}
        cancelText={t("common.cancel")}
        confirmLoading={sourceSaving}
        destroyOnHidden
        width={520}
      >
        <Form layout="vertical">
          <Form.Item label={t("apps.importTableLabel")}>
            <Select
              value={sourceTable}
              onChange={(value) => setSourceTable(value)}
              options={detail.tables.map((item) => ({
                value: item.table_key,
                label: item.label || item.table_key,
              }))}
            />
          </Form.Item>
          <Form.Item label={t("apps.sourceKind")}>
            <Select
              value={sourceKind}
              onChange={(value) => setSourceKind(value)}
              options={[
                {
                  value: "workspace_file",
                  label: t("apps.sourceKindWorkspaceFile"),
                },
                { value: "external", label: t("apps.sourceKindExternal") },
              ]}
            />
          </Form.Item>
          {sourceKind === "workspace_file" ? (
            <Form.Item
              label={t("apps.sourcePathLabel")}
              extra={t("apps.sourcePathHint")}
            >
              <Input
                value={sourcePath}
                onChange={(event) => setSourcePath(event.target.value)}
                placeholder="inbound/exports/data.csv"
                maxLength={500}
              />
            </Form.Item>
          ) : (
            <>
              <Form.Item label={t("apps.sourceInstanceLabel")}>
                <Select
                  value={sourceInstance || undefined}
                  onChange={(value) => {
                    setSourceInstance(value);
                    setSourceTool("");
                    void loadSourceTools(value);
                  }}
                  options={sourceInstances.map((item) => ({
                    value: item.instance_id,
                    label: item.display_name,
                  }))}
                  showSearch
                  optionFilterProp="label"
                  allowClear
                />
              </Form.Item>
              <Form.Item
                label={t("apps.sourceToolLabel")}
                extra={t("apps.sourceToolHint")}
              >
                <Select
                  value={sourceTool || undefined}
                  onChange={(value) => setSourceTool(value)}
                  options={sourceTools
                    .filter((tool) => tool.read_only)
                    .map((tool) => ({
                      value: tool.name,
                      label: tool.description || tool.name,
                    }))}
                  loading={sourceToolsLoading}
                  disabled={!sourceInstance}
                  notFoundContent={
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t("apps.sourceToolsEmpty")}
                    </Text>
                  }
                />
              </Form.Item>
              <Form.Item label={t("apps.sourceArgsLabel")}>
                <Input.TextArea
                  value={sourceArgs}
                  onChange={(event) => setSourceArgs(event.target.value)}
                  placeholder='{"q": "lake"}'
                  autoSize={{ minRows: 1, maxRows: 4 }}
                />
              </Form.Item>
              <Form.Item
                label={t("apps.sourceResultPathLabel")}
                extra={t("apps.sourceResultPathHint")}
              >
                <Input
                  value={sourceResultPath}
                  onChange={(event) => setSourceResultPath(event.target.value)}
                  placeholder="data.items"
                  maxLength={200}
                />
              </Form.Item>
              <Form.Item
                label={t("apps.sourceCursorArgLabel")}
                extra={t("apps.sourceCursorArgHint")}
              >
                <Input
                  value={sourceCursorArg}
                  onChange={(event) => setSourceCursorArg(event.target.value)}
                  placeholder="since"
                  maxLength={100}
                />
              </Form.Item>
              <Form.Item
                label={t("apps.sourceCursorPathLabel")}
                extra={t("apps.sourceCursorPathHint")}
              >
                <Input
                  value={sourceCursorPath}
                  onChange={(event) => setSourceCursorPath(event.target.value)}
                  placeholder="data.next_cursor"
                  maxLength={100}
                />
              </Form.Item>
            </>
          )}
          <Form.Item>
            <Checkbox
              checked={sourceIncludePii}
              onChange={(event) => setSourceIncludePii(event.target.checked)}
            >
              {t("apps.importIncludePii")}
            </Checkbox>
          </Form.Item>
          <Form.Item
            label={t("apps.sourceMappingLabel")}
            extra={t("apps.sourceMappingHint")}
          >
            <Input.TextArea
              value={sourceMapping}
              onChange={(event) => setSourceMapping(event.target.value)}
              placeholder='{"name": "title"}'
              autoSize={{ minRows: 1, maxRows: 4 }}
            />
          </Form.Item>
          <Form.Item label={t("apps.importMode")}>
            <Select
              value={sourceMode}
              onChange={(value) => setSourceMode(value)}
              options={[
                { value: "append", label: t("apps.importModeAppend") },
                {
                  value: "replace",
                  label: t("apps.importModeReplace"),
                  disabled: !canEditStructure,
                },
              ]}
            />
          </Form.Item>
          <Form.Item label={t("apps.sourceScheduleLabel")}>
            <Select
              value={sourceSchedule}
              onChange={(value) => setSourceSchedule(value)}
              options={SOURCE_SCHEDULE_OPTIONS.map((option) => ({
                value: option.value,
                label: t(option.labelKey),
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      <AppShareModal
        open={shareOpen}
        appName={detail.name}
        columns={detail.columns}
        shares={shares}
        loading={shareLoading}
        saving={savingShare}
        onChange={setShares}
        onClose={() => setShareOpen(false)}
        onSave={() => void submitShare()}
      />

      <AppAccessRequestsModal
        open={accessOpen}
        requests={accessRequests}
        busy={accessBusy}
        onClose={() => setAccessOpen(false)}
        onResolve={(request, approve) => void resolveAccess(request, approve)}
      />

      <AppInfoDrawer
        open={infoOpen}
        onClose={() => setInfoOpen(false)}
        detail={detail}
        permissionText={permissionText}
        sources={sources}
        files={files}
        filesLoading={filesLoading}
        canManage={canManage}
        canEditData={canEditData}
        canEditStructure={canEditStructure}
        capabilityDraft={capabilityDraft}
        savingCapabilities={savingCapabilities}
        onCapabilityDraftChange={setCapabilityDraft}
        onSaveCapabilities={() => void saveCapabilities()}
        onSetTableVisibility={(tableKey, checked) =>
          void setTableVisibility(tableKey, checked)
        }
        onOpenImport={openImport}
        onOpenSourceModal={openSourceModal}
        onRunSource={(source) => void runSourceNow(source)}
        onRemoveSource={(source) => void removeSource(source)}
      />

      <RowHistoryDrawer
        open={historyRowId !== null}
        onClose={() => setHistoryRowId(null)}
        appId={appId}
        rowId={historyRowId ?? ""}
        columns={dataColumns}
        canSeePii={detail?.permission !== "view"}
      />

      <AppAssistantDrawer
        appId={appId}
        open={assistantOpen}
        onClose={() => setAssistantOpen(false)}
        onOpenCitation={(table) => {
          setDataTable(table);
          setActiveView("data");
          setAssistantOpen(false);
        }}
      />

      <Modal
        title={gameSession?.name}
        open={!!gameSession}
        onCancel={() => setGameSession(null)}
        footer={null}
        width="92vw"
        destroyOnHidden
        styles={{ body: { height: "80vh", padding: 0 } }}
      >
        {gameSession && (
          <GameFrame
            appId={appId}
            session={gameSession}
            onFinished={() => {
              setGameSession(null);
              void loadDetail();
            }}
          />
        )}
      </Modal>
    </PageShell>
  );
}
