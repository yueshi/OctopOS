import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Key,
} from "react";
import { Empty, Segmented, Spin, Tooltip, Tree } from "antd";
import type { TreeDataNode } from "antd";
import {
  ArrowDownToLine,
  Folder,
  PanelsTopLeft,
  RefreshCw,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { fillTemplate } from "./appActions";
import {
  workspaceApi,
  type WorkspaceArtifact,
  type WorkspaceTreeEntry,
} from "../../api/modules/workspace";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { message } from "../../utils/antdMessage";
import {
  dedupeFileTreeInfos,
  nodeKey,
  pathFromKey,
} from "../../utils/fileTreeNodes";
import { fileTreeIcon } from "../../utils/fileTreeIcon";
import { formatMessageTime } from "../../utils/formatMessageTime";
import { saveBlobAsFile } from "../../utils/saveBlobAsFile";
import { isHostAbsolutePath } from "../../utils/workspaceIoPath";
import { workspaceEntryPath } from "../../utils/workspacePath";
import DesktopHtmlView from "./DesktopHtmlView";
import {
  desktopFileBasename,
  isHtmlPath,
  type DesktopFileRef,
} from "./desktopFiles";
import FileViewer from "../Agent/Workspace/components/FileViewer";
import { getPreviewKind } from "../Agent/Workspace/components/FilePreview";
import { getDocKind } from "../Agent/Workspace/utils/docKind";
import { isProbablyText } from "../Agent/Workspace/utils/fileKind";
import { getMediaKind } from "../Agent/Workspace/utils/mediaKind";
import {
  DESKTOP_ARTIFACTS_EVENT,
  type DesktopArtifactsEventDetail,
  type DesktopFilesTab,
} from "./desktopWindows";
import styles from "./index.module.less";

const WORKSPACE_ROOT_LABEL = "workspace.root";

interface DesktopFilesWindowProps {
  agentId: string;
  agentName: string;
  threadId?: string | null;
  initialPath?: string | null;
  initialFromWorkspace?: boolean;
  initialPathSeq?: number;
  initialTab?: DesktopFilesTab;
  /** Owner / admin can browse the full tree; shared viewers only see artifacts. */
  canBrowseAll?: boolean;
  /** Open a generated HTML file in its own desktop window. */
  onOpenHtml?: (file: DesktopFileRef) => void;
}

interface ArtifactGroup {
  threadId: string;
  title: string;
  lastActive: number;
  current: boolean;
  items: WorkspaceArtifact[];
}

function toTreeNodes(
  entries: WorkspaceTreeEntry[],
  listedPath: string,
): TreeDataNode[] {
  // The desktop viewer is a generated-file browser, not a system browser:
  // hide dot entries (.octop auth tokens, .env, .git) and the builtin skill
  // tree. The full WorkspaceDrawer keeps its existing behaviour.
  return dedupeFileTreeInfos(entries, listedPath)
    .filter((entry) => {
      const name = desktopFileBasename(workspaceEntryPath(entry.path));
      return !name.startsWith(".") && name !== "_builtin_skills";
    })
    .map((entry) => {
      const fullPath = workspaceEntryPath(entry.path);
      const name = desktopFileBasename(fullPath);
      return {
        key: nodeKey(fullPath, Boolean(entry.is_dir)),
        isLeaf: !entry.is_dir,
        title: (
          <span className={styles.desktopFilesTreeNode}>
            {entry.is_dir ? (
              <Folder
                size={13}
                className={styles.desktopFilesTreeIcon}
                aria-hidden
              />
            ) : (
              <span className={styles.desktopFilesTreeIcon}>
                {fileTreeIcon(fullPath)}
              </span>
            )}
            <span className={styles.desktopFilesTreeName}>{name}</span>
          </span>
        ),
      };
    });
}

function groupArtifacts(
  artifacts: WorkspaceArtifact[],
  threadId?: string | null,
): ArtifactGroup[] {
  const groups = new Map<string, ArtifactGroup>();
  for (const item of artifacts) {
    const key = item.thread_id || "__none__";
    const group = groups.get(key);
    if (group) {
      group.items.push(item);
      group.lastActive = Math.max(group.lastActive, item.last_active);
      continue;
    }
    groups.set(key, {
      threadId: key,
      title: item.thread_title,
      lastActive: item.last_active,
      current: Boolean(threadId && item.thread_id === threadId),
      items: [item],
    });
  }
  const rows = [...groups.values()].sort(
    (left, right) => right.lastActive - left.lastActive,
  );
  const currentIndex = threadId
    ? rows.findIndex((row) => row.threadId === threadId)
    : -1;
  if (currentIndex > 0) {
    const [current] = rows.splice(currentIndex, 1);
    rows.unshift(current);
  }
  return rows;
}

function TextFilePreview({
  selected,
  refreshToken,
}: {
  selected: DesktopFileRef;
  refreshToken: number;
}) {
  const { t } = useTranslation();
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(false);
    void workspaceApi
      .readAgentFile(selected.agentId, selected.path, selected.fromWorkspace)
      .then((row) => {
        if (alive) setContent(row.content);
      })
      .catch(() => {
        if (alive) setError(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [refreshToken, selected]);

  if (loading) {
    return (
      <div className={styles.desktopFilesCenter}>
        <Spin />
      </div>
    );
  }
  if (error) {
    return (
      <Empty
        className={styles.desktopFilesCenter}
        description={t(
          "apps.desktop.files.readFailed",
          "Could not read this file.",
        )}
      />
    );
  }
  return (
    <FileViewer
      agentId={selected.agentId}
      path={selected.path}
      fromWorkspace={selected.fromWorkspace}
      editMode={false}
      value={content}
      onChange={() => {}}
      previewMode={getPreviewKind(selected.path) === "markdown"}
      refreshToken={refreshToken}
    />
  );
}

function SelectedFileView({
  selected,
  refreshToken,
}: {
  selected: DesktopFileRef;
  refreshToken: number;
}) {
  const { t } = useTranslation();
  const mediaKind = getMediaKind(selected.path);
  const docKind = getDocKind(selected.path);
  if (mediaKind || docKind) {
    return (
      <FileViewer
        agentId={selected.agentId}
        path={selected.path}
        fromWorkspace={selected.fromWorkspace}
        editMode={false}
        value=""
        onChange={() => {}}
        refreshToken={refreshToken}
      />
    );
  }
  if (isHtmlPath(selected.path)) {
    return (
      <DesktopHtmlView
        agentId={selected.agentId}
        path={selected.path}
        fromWorkspace={selected.fromWorkspace}
        refreshToken={refreshToken}
        frameTestId="desktop-files-html-frame"
      />
    );
  }
  if (isProbablyText(selected.path)) {
    return <TextFilePreview selected={selected} refreshToken={refreshToken} />;
  }
  return (
    <Empty
      className={styles.desktopFilesCenter}
      description={t(
        "workspace.binaryHint",
        "This file may be binary. Download it to inspect it.",
      )}
    />
  );
}

/**
 * Read-only workspace browser that lives inside an Agent OS desktop window.
 *
 * The generated-files tab is backed by the conversation artifact ledger; the
 * all-files tab lazily lists the workspace tree. Mutations stay in the full
 * ``WorkspaceDrawer`` (opened from the toolbar).
 */
function DesktopFilesWindow({
  agentId,
  agentName,
  threadId = null,
  initialPath = null,
  initialFromWorkspace,
  initialPathSeq = 0,
  initialTab = "generated",
  canBrowseAll = true,
  onOpenHtml,
}: DesktopFilesWindowProps) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const tr = useCallback(
    (key: string, fallback: string) => t(key, fallback),
    [t],
  );
  const [tab, setTab] = useState<DesktopFilesTab>(() =>
    initialTab === "all" && canBrowseAll ? "all" : "generated",
  );
  const [artifacts, setArtifacts] = useState<WorkspaceArtifact[]>([]);
  const [artifactsLoading, setArtifactsLoading] = useState(true);
  const [artifactsError, setArtifactsError] = useState(false);
  const [treeData, setTreeData] = useState<TreeDataNode[]>([]);
  const [treeLoading, setTreeLoading] = useState(true);
  const [treeError, setTreeError] = useState(false);
  const [selected, setSelected] = useState<DesktopFileRef | null>(null);
  const [htmlExternalized, setHtmlExternalized] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const appliedInitialRef = useRef<string | null>(null);
  const artifactsRefreshTimer = useRef<number | null>(null);
  const tabRef = useRef<DesktopFilesTab>(tab);
  useEffect(() => {
    tabRef.current = tab;
  }, [tab]);
  useEffect(
    () => () => {
      if (artifactsRefreshTimer.current !== null) {
        window.clearTimeout(artifactsRefreshTimer.current);
      }
    },
    [],
  );

  useEffect(() => {
    if (!canBrowseAll) setTab("generated");
  }, [canBrowseAll]);

  const loadArtifacts = useCallback(async () => {
    setArtifactsLoading(true);
    try {
      const data = await workspaceApi.listAgentArtifacts(agentId, 100);
      setArtifacts(Array.isArray(data.items) ? data.items : []);
      setArtifactsError(false);
    } catch {
      setArtifactsError(true);
    } finally {
      setArtifactsLoading(false);
    }
  }, [agentId]);

  // A chat turn can complete dozens of tool calls. Coalesce those events into
  // one artifacts request, and do not refresh while the generated tab is hidden.
  const scheduleArtifactsReload = useCallback(() => {
    if (document.visibilityState !== "visible") return;
    if (tabRef.current !== "generated") return;
    if (artifactsRefreshTimer.current !== null) {
      window.clearTimeout(artifactsRefreshTimer.current);
    }
    artifactsRefreshTimer.current = window.setTimeout(() => {
      artifactsRefreshTimer.current = null;
      void loadArtifacts();
    }, 800);
  }, [loadArtifacts]);

  const loadTreeRoot = useCallback(async () => {
    setTreeLoading(true);
    try {
      const entries = await workspaceApi.listAgentTree(agentId, "/");
      setTreeData([
        {
          key: nodeKey("/", true),
          isLeaf: false,
          title: (
            <span className={styles.desktopFilesTreeNode}>
              <Folder
                size={13}
                className={styles.desktopFilesTreeIcon}
                aria-hidden
              />
              <span className={styles.desktopFilesTreeName}>
                {tr(WORKSPACE_ROOT_LABEL, "Workspace")}
              </span>
            </span>
          ),
          children: toTreeNodes(entries, "/"),
        },
      ]);
      setTreeError(false);
    } catch {
      setTreeError(true);
    } finally {
      setTreeLoading(false);
    }
  }, [agentId, tr]);

  useEffect(() => {
    void loadArtifacts();
  }, [loadArtifacts]);

  useEffect(() => {
    if (canBrowseAll) void loadTreeRoot();
  }, [canBrowseAll, loadTreeRoot]);

  useEffect(() => {
    const onArtifacts = (event: Event) => {
      const detail = (event as CustomEvent<DesktopArtifactsEventDetail>).detail;
      if (!detail || detail.agentId !== agentId) return;
      scheduleArtifactsReload();
    };
    window.addEventListener(DESKTOP_ARTIFACTS_EVENT, onArtifacts);
    return () =>
      window.removeEventListener(DESKTOP_ARTIFACTS_EVENT, onArtifacts);
  }, [agentId, scheduleArtifactsReload]);

  useEffect(() => {
    const onFocus = () => {
      if (document.visibilityState === "visible") scheduleArtifactsReload();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [scheduleArtifactsReload]);

  useEffect(() => {
    const path = initialPath?.trim();
    const token = `${path ?? ""}:${initialPathSeq}`;
    if (!path || appliedInitialRef.current === token) return;
    appliedInitialRef.current = token;
    setHtmlExternalized(false);
    setSelected({
      agentId,
      path,
      name: desktopFileBasename(path),
      fromWorkspace:
        initialFromWorkspace ??
        !isHostAbsolutePath(path.replace(/^file:\/\//i, "")),
    });
  }, [agentId, initialFromWorkspace, initialPath, initialPathSeq]);

  const groups = useMemo(
    () => groupArtifacts(artifacts, threadId),
    [artifacts, threadId],
  );

  const onLoadData = useCallback(
    async (node: TreeDataNode): Promise<void> => {
      if (node.isLeaf) return;
      const { path, is_dir } = pathFromKey(String(node.key));
      if (!is_dir) return;
      try {
        const entries = await workspaceApi.listAgentTree(agentId, path);
        const children = toTreeNodes(entries, path);
        const replace = (nodes: TreeDataNode[]): TreeDataNode[] =>
          nodes.map((item) =>
            item.key === node.key
              ? { ...item, children }
              : item.children
              ? { ...item, children: replace(item.children) }
              : item,
          );
        setTreeData((current) => replace(current));
      } catch {
        message.error(
          tr("workspace.loadChildrenFailed", "Failed to load folder"),
        );
      }
    },
    [agentId, tr],
  );

  const selectFile = useCallback(
    (file: DesktopFileRef) => {
      setSelected(file);
      const external = Boolean(onOpenHtml && isHtmlPath(file.path));
      setHtmlExternalized(external);
      if (external) onOpenHtml?.(file);
    },
    [onOpenHtml],
  );

  const selectArtifact = useCallback(
    (item: WorkspaceArtifact) => {
      selectFile({
        agentId: item.agent_id || agentId,
        path: item.path,
        name: item.name || desktopFileBasename(item.path),
        fromWorkspace: item.in_workspace,
      });
    },
    [agentId, selectFile],
  );

  const selectTreeFile = useCallback(
    (path: string) => {
      selectFile({
        agentId,
        path,
        name: desktopFileBasename(path),
        fromWorkspace: true,
      });
    },
    [agentId, selectFile],
  );

  const refresh = useCallback(() => {
    if (tab === "generated") void loadArtifacts();
    else if (canBrowseAll) void loadTreeRoot();
    setRefreshToken((value) => value + 1);
  }, [canBrowseAll, loadArtifacts, loadTreeRoot, tab]);

  const downloadSelected = useCallback(async () => {
    if (!selected) return;
    try {
      const blob = await workspaceApi.downloadAgentFile(
        selected.agentId,
        selected.path,
        selected.fromWorkspace,
      );
      await saveBlobAsFile(blob, selected.name);
    } catch {
      message.error(tr("apps.desktop.files.downloadFailed", "Download failed"));
    }
  }, [selected, tr]);

  const isSelectedArtifact = (item: WorkspaceArtifact): boolean =>
    Boolean(
      selected &&
        selected.path === item.path &&
        selected.agentId === (item.agent_id || agentId),
    );

  return (
    <div className={styles.desktopFiles} data-testid="desktop-files">
      <div className={styles.desktopFilesToolbar}>
        <Segmented
          size="small"
          value={tab}
          options={[
            {
              label: tr("apps.desktop.files.generated", "Generated"),
              value: "generated",
            },
            ...(canBrowseAll
              ? [
                  {
                    label: tr("apps.desktop.files.all", "All files"),
                    value: "all",
                  },
                ]
              : []),
          ]}
          onChange={(value) => setTab(value as DesktopFilesTab)}
        />
        <span className={styles.desktopFilesSpacer} />
        <Tooltip title={tr("apps.desktop.files.refresh", "Refresh")}>
          <button
            type="button"
            className={styles.desktopFilesIconButton}
            aria-label={tr("apps.desktop.files.refresh", "Refresh")}
            onClick={refresh}
          >
            <RefreshCw size={14} />
          </button>
        </Tooltip>
        <Tooltip title={tr("apps.desktop.files.download", "Download")}>
          <button
            type="button"
            className={styles.desktopFilesIconButton}
            aria-label={tr("apps.desktop.files.download", "Download")}
            disabled={!selected}
            onClick={() => void downloadSelected()}
          >
            <ArrowDownToLine size={14} />
          </button>
        </Tooltip>
        {selected && isHtmlPath(selected.path) && onOpenHtml && (
          <Tooltip
            title={tr("apps.desktop.files.openInWindow", "Open in window")}
          >
            <button
              type="button"
              className={styles.desktopFilesIconButton}
              aria-label={tr(
                "apps.desktop.files.openInWindow",
                "Open in window",
              )}
              onClick={() => {
                setHtmlExternalized(true);
                onOpenHtml(selected);
              }}
            >
              <PanelsTopLeft size={14} />
            </button>
          </Tooltip>
        )}
      </div>
      <div className={styles.desktopFilesBody}>
        <div className={styles.desktopFilesList}>
          {tab === "generated" ? (
            artifactsLoading ? (
              <div className={styles.desktopFilesCenter}>
                <Spin />
              </div>
            ) : artifactsError ? (
              <Empty
                className={styles.desktopFilesCenter}
                description={
                  <span>
                    {tr(
                      "apps.desktop.files.loadFailed",
                      "Could not load generated files.",
                    )}
                    <span className={styles.desktopFilesErrorHint}>
                      {tr(
                        "apps.desktop.files.loadFailedHint",
                        "The service may be out of date or unavailable. Browse the workspace instead.",
                      )}
                    </span>
                    {canBrowseAll && (
                      <button
                        type="button"
                        className={styles.desktopFilesLinkButton}
                        onClick={() => setTab("all")}
                      >
                        {tr("apps.desktop.files.browseAll", "Browse all files")}
                      </button>
                    )}
                  </span>
                }
              />
            ) : groups.length === 0 ? (
              <Empty
                className={styles.desktopFilesCenter}
                description={
                  <span>
                    {tr(
                      "apps.desktop.files.emptyGenerated",
                      "This expert has not generated any files yet.",
                    )}
                    {canBrowseAll && (
                      <button
                        type="button"
                        className={styles.desktopFilesLinkButton}
                        onClick={() => setTab("all")}
                      >
                        {tr("apps.desktop.files.browseAll", "Browse all files")}
                      </button>
                    )}
                  </span>
                }
              />
            ) : (
              groups.map((group) => (
                <div key={group.threadId} className={styles.desktopFilesGroup}>
                  <div className={styles.desktopFilesGroupHeader}>
                    <span>
                      {group.current
                        ? tr(
                            "apps.desktop.files.currentThread",
                            "This conversation",
                          )
                        : group.title ||
                          tr("apps.desktop.untitledThread", "New conversation")}
                    </span>
                    {group.lastActive > 0 && (
                      <span className={styles.desktopFilesGroupTime}>
                        {formatMessageTime(group.lastActive * 1000, timeZone)}
                      </span>
                    )}
                  </div>
                  {group.items.map((item) => (
                    <button
                      key={`${item.agent_id}:${item.path}`}
                      type="button"
                      className={[
                        styles.desktopFilesItem,
                        isSelectedArtifact(item)
                          ? styles.desktopFilesItemActive
                          : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                      onClick={() => selectArtifact(item)}
                    >
                      <span className={styles.desktopFilesItemIcon}>
                        {fileTreeIcon(item.path)}
                      </span>
                      <span className={styles.desktopFilesItemBody}>
                        <span className={styles.desktopFilesItemName}>
                          {item.name || desktopFileBasename(item.path)}
                        </span>
                        <span className={styles.desktopFilesItemMeta}>
                          {item.agent_id && item.agent_id !== agentId
                            ? fillTemplate(
                                tr(
                                  "apps.desktop.files.producer",
                                  "Produced by {{name}}",
                                ),
                                { name: item.agent_id },
                              )
                            : item.path}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              ))
            )
          ) : treeLoading ? (
            <div className={styles.desktopFilesCenter}>
              <Spin />
            </div>
          ) : treeError ? (
            <Empty
              className={styles.desktopFilesCenter}
              description={tr(
                "apps.desktop.files.loadFailed",
                "Could not load generated files.",
              )}
            />
          ) : (
            <Tree
              className={styles.desktopFilesTree}
              treeData={treeData}
              loadData={onLoadData}
              onSelect={(keys: Key[]) => {
                if (!keys.length) return;
                const { path, is_dir } = pathFromKey(String(keys[0]));
                if (!is_dir) selectTreeFile(path);
              }}
            />
          )}
        </div>
        <div className={styles.desktopFilesViewer}>
          {selected ? (
            isHtmlPath(selected.path) && htmlExternalized && onOpenHtml ? (
              <div
                className={styles.desktopFilesCenter}
                data-testid="desktop-files-html-external"
              >
                <Empty
                  description={
                    <span>
                      {tr(
                        "apps.desktop.files.openedInWindow",
                        "Opened in an independent window",
                      )}
                      <button
                        type="button"
                        className={styles.desktopFilesLinkButton}
                        onClick={() => setHtmlExternalized(false)}
                      >
                        {tr("apps.desktop.files.openInline", "Preview here")}
                      </button>
                    </span>
                  }
                />
              </div>
            ) : (
              <SelectedFileView
                selected={selected}
                refreshToken={refreshToken}
              />
            )
          ) : (
            <Empty
              className={styles.desktopFilesCenter}
              description={
                tab === "generated"
                  ? tr(
                      "apps.desktop.files.pickGenerated",
                      "Select a generated file to preview it.",
                    )
                  : tr(
                      "apps.desktop.files.pickFile",
                      "Select a file to preview it.",
                    )
              }
            />
          )}
        </div>
      </div>
      <div className={styles.desktopFilesStatus}>
        <span>{agentName}</span>
        <span>{tr("apps.desktop.files.readOnly", "Read-only")}</span>
      </div>
    </div>
  );
}

export default memo(DesktopFilesWindow);
