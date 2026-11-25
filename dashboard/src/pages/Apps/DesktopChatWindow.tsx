import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Spin, Tooltip } from "antd";
import {
  AppWindow,
  Check,
  ChevronDown,
  FolderOpen,
  Gamepad2,
  MessageSquare,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Puzzle,
  RefreshCw,
  ShieldAlert,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  appsApi,
  type AppGameSession,
  type AppSummary,
} from "../../api/modules/apps";
import {
  octopThreadsApi,
  type OctopThread,
} from "../../api/modules/octopThreads";
import type { HitlDecisionHandler } from "../../api/types/hitl";
import type { OctopAgent } from "../../context/AgentContext";
import { useAgent } from "../../context/AgentContext";
import { usePluginToolUis } from "../../plugins/toolRenderers";
import { parseOctopToolOutput } from "../../plugins/toolRenderers/parseToolOutput";
import { ToolHostScopeProvider } from "../../plugins/toolRenderers/ToolHostScope";
import type {
  OpenGamePanelOptions,
  OpenSidePanelOptions,
} from "../../plugins/toolRenderers/types";
import GameFrame from "../../components/MiniAppFrame/GameFrame";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { beginPointerDragSession } from "../../hooks/usePointerDragSession";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { message } from "../../utils/antdMessage";
import { isAgentChatReady } from "../../utils/agentError";
import { formatMessageTime } from "../../utils/formatMessageTime";
import { isTeamAgent } from "../../utils/teamAgent";
import ChatInput, { type ChatInputHandle } from "../Chat/components/ChatInput";
import ChatDockSessionApps from "../Chat/components/ChatDockSessionApps";
import MessageList from "../Chat/components/MessageList";
import { ChatFilePreviewProvider } from "../Chat/ChatFilePreviewContext";
import * as chatStore from "../Chat/hooks/chatStore";
import { useChat, type ChatAttachment } from "../Chat/hooks/useChat";
import { parseConversationMode } from "../Chat/utils/conversationMode";
import { mergeAllowTools } from "../Chat/utils/hitlSessionPolicy";
import {
  collectSessionApps,
  mergeSessionApps,
  sessionAppsFromSummaries,
  type SessionApp,
} from "../Chat/utils/sessionApps";
import {
  findPendingApproval,
  findPendingAsk,
  hasPendingHitl,
} from "../Chat/utils/pendingHitl";
import { formatThreadTitle } from "../Chat/utils/threadTitle";
import { ExpertIcon } from "../Experts/components/iconForName";
import DesktopChatToolPanel from "./DesktopChatToolPanel";
import { addDesktopNotification } from "./desktopNotifications";
import { isArtifactToolName } from "./desktopFiles";
import {
  DESKTOP_ARTIFACTS_EVENT,
  type DesktopArtifactsEventDetail,
  type DesktopChatSeed,
} from "./desktopWindows";
import { APP_ACTIONS, actionPromptFor, fillTemplate } from "./appActions";
import styles from "./index.module.less";

interface DesktopChatWindowProps {
  agentId: string;
  agentName: string;
  agent?: OctopAgent | null;
  /** The expert's MiniApps; feeds the in-window apps sidebar. */
  apps?: AppSummary[];
  /** The MiniApp this chat was opened for (desktop action / app compose). */
  initialAppId?: string | null;
  /** One-shot composer prefill from the desktop pet or an action button. */
  seed?: DesktopChatSeed | null;
  onSeedConsumed?: (windowId: string) => void;
  /** Open a chat file card in the desktop Files window. */
  onOpenFile?: (
    path: string,
    agentId: string | null,
    threadId: string | null,
  ) => void;
  /** Open the Files window at the current conversation. */
  onOpenFiles?: (agentId: string, threadId: string | null) => void;
  /** Desktop window id, used to focus this chat from a notification. */
  windowId?: string;
  /** Space owning this window; external links open as web windows in it. */
  spaceId?: string;
  /** Open an in-desktop web window instead of a browser tab. */
  onOpenExternal?: (url: string, spaceId: string) => void;
  /** True when this chat window is focused and on the current desktop. */
  active?: boolean;
}

type ChatPanel = "threads" | null;

/** One tab in the in-window side panel (apps / plugin tool / MiniApp game). */
type DesktopSideTab =
  | { id: "apps"; kind: "apps" }
  | {
      id: string;
      kind: "tool";
      callId: string;
      title?: string;
      toolName?: string;
    }
  | { id: string; kind: "game"; appId: string; session: AppGameSession };

const THREAD_STORE_PREFIX = "octop:apps-desktop-thread:";
const SIDEBAR_WIDTH_KEY = "octop:apps-desktop-sidebar:width";

/** In-window side panel: default width and drag limits. */
const SIDEBAR_DEFAULT_WIDTH = 300;
const SIDEBAR_MIN_WIDTH = 220;
const CHAT_PANEL_MIN_WIDTH = 260;

function toolTabId(callId: string): string {
  return `tool:${callId}`;
}

function gameTabId(appId: string, gameId: string): string {
  return `game:${appId}:${gameId}`;
}

/** Keep the current tab when it survives, else prefer apps, else the last. */
function fallbackSideTabId(
  tabs: readonly DesktopSideTab[],
  current: string | null,
): string | null {
  if (current && tabs.some((tab) => tab.id === current)) return current;
  return (
    tabs.find((tab) => tab.kind === "apps")?.id ??
    tabs[tabs.length - 1]?.id ??
    null
  );
}

function loadSidebarWidth(): number {
  try {
    const saved = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
    return Number.isFinite(saved) && saved >= SIDEBAR_MIN_WIDTH
      ? saved
      : SIDEBAR_DEFAULT_WIDTH;
  } catch {
    return SIDEBAR_DEFAULT_WIDTH;
  }
}

function rememberSidebarWidth(width: number): void {
  try {
    localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width));
  } catch {
    // Private windows can disable storage; the width still applies for now.
  }
}

function threadTitle(thread: OctopThread | null, fallback: string): string {
  return formatThreadTitle(thread?.title) || fallback;
}

function rememberThread(agentId: string, threadId: string): void {
  try {
    localStorage.setItem(`${THREAD_STORE_PREFIX}${agentId}`, threadId);
  } catch {
    // Private windows can disable storage; the window still works for now.
  }
}

function recallThread(agentId: string): string | null {
  try {
    return localStorage.getItem(`${THREAD_STORE_PREFIX}${agentId}`);
  } catch {
    return null;
  }
}

/**
 * A complete conversation surface that lives inside a desktop window.
 *
 * It reuses the production chat transport (`useChat` → chatStore/WebSocket),
 * message renderer and composer, then adds desktop-app chrome: expert identity,
 * thread history, a tabbed side panel for MiniApps / plugin tools / games, and a
 * status footer. The side panel has no separate app context bar: selecting an
 * app only runs it inside the panel.
 */
function DesktopChatWindow({
  agentId,
  agentName,
  agent = null,
  apps = [],
  initialAppId = null,
  seed = null,
  onSeedConsumed,
  onOpenFile,
  onOpenFiles,
  windowId,
  spaceId,
  onOpenExternal,
  active = true,
}: DesktopChatWindowProps) {
  const { t } = useTranslation();
  const { agents } = useAgent();
  const currentUser = useCurrentUser();
  const timeZone = useServerTimezone();
  const tr = useCallback(
    (key: string, fallback: string) => t(key, fallback),
    [t],
  );

  const [threads, setThreads] = useState<OctopThread[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [threadsError, setThreadsError] = useState(false);
  const [bootKey, setBootKey] = useState(0);
  const [panel, setPanel] = useState<ChatPanel>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sideTabs, setSideTabs] = useState<DesktopSideTab[]>([]);
  const [activeSideTabId, setActiveSideTabId] = useState<string | null>(null);
  const [sidebarWidth, setSidebarWidth] = useState(loadSidebarWidth);
  const [sidebarResizing, setSidebarResizing] = useState(false);
  const [activeAppId, setActiveAppId] = useState<string | null>(initialAppId);
  const creatingRef = useRef(false);
  const sidebarPanelRef = useRef<HTMLElement | null>(null);
  const sidebarResizeRef = useRef<{
    startX: number;
    startWidth: number;
    maxWidth: number;
  } | null>(null);
  const inputRef = useRef<ChatInputHandle | null>(null);
  const seedAppliedRef = useRef<number | null>(null);
  const wasStreamingRef = useRef(false);

  const activeApp = apps.find((app) => app.app_id === activeAppId) ?? null;
  const appsTabOpen = sidebarOpen && activeSideTabId === "apps";

  // An admin may chat through another user's expert; make the proxy explicit
  // and show both owners so write actions are never silently attributed.
  const adminProxyAgentOwner =
    currentUser?.role === "admin" &&
    agent &&
    agent.user_id != null &&
    agent.user_id !== currentUser.id
      ? agent.owner_username || ""
      : null;
  const adminProxyAppOwner =
    adminProxyAgentOwner !== null &&
    activeApp?.owner_username &&
    activeApp.owner_username !== currentUser?.username
      ? activeApp.owner_username
      : null;

  useEffect(() => {
    if (initialAppId) setActiveAppId(initialAppId);
  }, [initialAppId]);

  useEffect(() => {
    if (seed?.appId) setActiveAppId(seed.appId);
  }, [seed]);

  useEffect(() => {
    if (activeAppId && !apps.some((app) => app.app_id === activeAppId)) {
      setActiveAppId(null);
    }
  }, [activeAppId, apps]);

  useEffect(() => {
    if (!panel) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      setPanel(null);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [panel]);

  const createThread = useCallback(async (): Promise<string | null> => {
    if (creatingRef.current) return null;
    creatingRef.current = true;
    try {
      const created = await octopThreadsApi.create(agentId);
      const now = Math.floor(Date.now() / 1000);
      rememberThread(agentId, created.thread_id);
      setThreadsError(false);
      setThreads((prev) => [
        {
          thread_id: created.thread_id,
          title: null,
          channel_type: "dashboard",
          session_key: created.session_key,
          last_active: 0,
          created_at: now,
          has_messages: false,
        },
        ...prev.filter((row) => row.thread_id !== created.thread_id),
      ]);
      setThreadId(created.thread_id);
      return created.thread_id;
    } catch {
      setThreadsError(true);
      message.error(
        tr(
          "apps.desktop.chatCreateFailed",
          "Could not create a conversation. Try again.",
        ),
      );
      return null;
    } finally {
      creatingRef.current = false;
    }
  }, [agentId, tr]);

  useEffect(() => {
    let alive = true;
    setThreadsLoading(true);
    setThreadsError(false);
    octopThreadsApi
      .list(agentId, 30)
      .then((rows) => {
        if (!alive) return;
        setThreads(rows);
        const rememberedId = recallThread(agentId);
        const preferred =
          (rememberedId
            ? rows.find((row) => row.thread_id === rememberedId)
            : undefined) ??
          rows.find(
            (row) => row.channel_type === "dashboard" && row.has_messages,
          ) ??
          rows.find((row) => row.channel_type === "dashboard") ??
          null;
        if (preferred) {
          setThreadId(preferred.thread_id);
          rememberThread(agentId, preferred.thread_id);
          setThreadsLoading(false);
          return;
        }
        void createThread().finally(() => {
          if (alive) setThreadsLoading(false);
        });
      })
      .catch(() => {
        if (!alive) return;
        setThreadsError(true);
        setThreadsLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [agentId, bootKey, createThread]);

  const {
    messages,
    isStreaming,
    thinkingStartedAt,
    historyLoading,
    historyError,
    historyHasMore,
    historyLoadingMore,
    historyRefreshing,
    historyHydrated,
    runUsage,
    contextUsage,
    liveSpeakers,
    sendMessage,
    editAndResend,
    cancelStream,
    loadHistory,
    loadMoreHistory,
    refreshHistory,
    retryHistory,
    resumeHitl,
  } = useChat(threadId, agentId, isTeamAgent(agent));

  // Desktop chat windows are separate React trees from `/chat`; without this
  // the builtin `app_card` renderer (and plugin UIs) never register here and
  // MiniApp cards fall back to a generic key/value block.
  usePluginToolUis({ agentId, threadId });

  useEffect(() => {
    if (!threadId) return;
    void loadHistory(threadId);
  }, [loadHistory, threadId]);

  // MiniApps this conversation created or opened — the side panel's apps tab.
  // History only holds the latest page, so the tab also asks the server for the
  // whole thread; the transcript keeps live cards visible meanwhile.
  const transcriptSessionApps = useMemo(
    () => collectSessionApps(messages),
    [messages],
  );
  const transcriptAppIds = transcriptSessionApps
    .map((app) => app.app_id)
    .join("\u0000");
  const sessionAppsThread = `${agentId}\u0000${threadId ?? ""}`;
  const [threadSessionApps, setThreadSessionApps] = useState<{
    thread: string;
    apps: SessionApp[];
  } | null>(null);
  useEffect(() => {
    if (!appsTabOpen || !threadId) {
      setThreadSessionApps(null);
      return;
    }
    let alive = true;
    appsApi
      .inThread(agentId, threadId)
      .then((data) => {
        if (alive) {
          setThreadSessionApps({
            thread: sessionAppsThread,
            apps: sessionAppsFromSummaries(data.apps),
          });
        }
      })
      .catch(() => {
        if (alive) setThreadSessionApps(null);
      });
    return () => {
      alive = false;
    };
  }, [agentId, appsTabOpen, sessionAppsThread, threadId, transcriptAppIds]);
  const sessionApps = useMemo(
    () =>
      mergeSessionApps(
        threadSessionApps?.thread === sessionAppsThread
          ? threadSessionApps.apps
          : null,
        transcriptSessionApps,
      ),
    [threadSessionApps, sessionAppsThread, transcriptSessionApps],
  );

  // Surface permission blocks in the desktop notification center. Only cards
  // that arrive after history hydration notify; opening an old conversation
  // must not replay notifications for already-recorded errors.
  const permissionNotifyReadyRef = useRef(false);
  const notifiedPermissionRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!historyHydrated) return;
    const blocked = messages.filter((item) => {
      const output = item.toolData?.output;
      return (
        !!output &&
        parseOctopToolOutput(output).octopUi?.renderer === "app_permission_card"
      );
    });
    if (!permissionNotifyReadyRef.current) {
      for (const item of blocked) notifiedPermissionRef.current.add(item.id);
      permissionNotifyReadyRef.current = true;
      return;
    }
    for (const item of blocked) {
      if (notifiedPermissionRef.current.has(item.id)) continue;
      notifiedPermissionRef.current.add(item.id);
      const parsed = parseOctopToolOutput(item.toolData?.output);
      const data = (parsed.data ?? {}) as {
        app_id?: string;
        app_owner?: string;
      };
      addDesktopNotification({
        kind: "security",
        title: tr(
          "apps.desktop.permissionNotificationTitle",
          "Permission required",
        ),
        body: tr(
          "apps.desktop.permissionNotificationBody",
          "The expert could not modify this MiniApp. Open the conversation to review access.",
        ),
        windowId,
        dedupeKey: `permission:${agentId}:${data.app_id ?? item.id}`,
      });
    }
  }, [messages, historyHydrated, agentId, windowId, tr]);

  const refreshThreads = useCallback(async () => {
    try {
      const rows = await octopThreadsApi.list(agentId, 30);
      setThreads(rows);
    } catch {
      // Keep the locally known list; the next turn or panel open retries.
    }
  }, [agentId]);

  const notifyArtifacts = useCallback(() => {
    if (!threadId) return;
    const detail: DesktopArtifactsEventDetail = { agentId, threadId };
    window.dispatchEvent(new CustomEvent(DESKTOP_ARTIFACTS_EVENT, { detail }));
  }, [agentId, threadId]);

  useEffect(() => {
    if (wasStreamingRef.current && !isStreaming) {
      void refreshThreads();
      notifyArtifacts();
    }
    wasStreamingRef.current = isStreaming;
  }, [isStreaming, notifyArtifacts, refreshThreads]);

  useEffect(() => {
    if (!threadId) return;
    return chatStore.onToolEvent((event) => {
      if (
        event.kind === "toolDone" &&
        event.sessionId === threadId &&
        isArtifactToolName(event.toolName)
      ) {
        notifyArtifacts();
      }
    });
  }, [notifyArtifacts, threadId]);

  const activeThread =
    threads.find((row) => row.thread_id === threadId) ?? null;
  const title = threadTitle(
    activeThread,
    tr("apps.desktop.untitledThread", "New conversation"),
  );
  const conversationMode =
    activeThread?.conversation_mode ??
    parseConversationMode(agent?.config?.conversation_mode);
  const hitlPolicy = activeThread?.hitl_policy ?? null;

  const chatReady = agent ? isAgentChatReady(agent.state, agent) : true;
  const stateKey = isStreaming
    ? "streaming"
    : chatReady
    ? "running"
    : agent?.state || "offline";
  const statusLabel = isStreaming
    ? tr("apps.desktop.generating", "Replying…")
    : chatReady
    ? tr("apps.desktop.online", "Online")
    : tr(
        `common.agentState.${agent?.state ?? "stopped"}`,
        agent?.state || tr("apps.desktop.offline", "Offline"),
      );

  useEffect(() => {
    if (!seed || !threadId) return;
    if (seedAppliedRef.current === seed.id) return;
    const frame = requestAnimationFrame(() => {
      if (!inputRef.current) return;
      seedAppliedRef.current = seed.id;
      inputRef.current.setPrefillText(seed.text);
      inputRef.current.focusComposer();
      onSeedConsumed?.(windowId ?? "");
    });
    return () => cancelAnimationFrame(frame);
  }, [onSeedConsumed, seed, threadId, windowId]);

  const prefill = useCallback((text: string) => {
    inputRef.current?.setPrefillText(text);
    inputRef.current?.focusComposer();
  }, []);

  // Plugin renderers call ``host.openSidePanel``; inside the desktop that
  // opens a tab in this window's side panel instead of the chat page's dock.
  // Apps, plugin tools and games share one tabbed panel, like the chat dock.
  const addSideTab = useCallback((tab: DesktopSideTab) => {
    setSideTabs((prev) => {
      const existing = prev.find((item) => item.id === tab.id);
      if (!existing) return [...prev, tab];
      if (existing.kind === "game" && tab.kind === "game") {
        // Relaunching the same game replaces the session (stale tokens).
        return prev.map((item) => (item.id === tab.id ? tab : item));
      }
      return prev;
    });
    setActiveSideTabId(tab.id);
    setSidebarOpen(true);
  }, []);
  const showAppsTab = useCallback(() => {
    addSideTab({ id: "apps", kind: "apps" });
  }, [addSideTab]);
  const closeSideTab = useCallback((id: string) => {
    setSideTabs((prev) => {
      const next = prev.filter((tab) => tab.id !== id);
      setActiveSideTabId((current) =>
        fallbackSideTabId(next, current === id ? null : current),
      );
      if (next.length === 0) setSidebarOpen(false);
      return next;
    });
  }, []);
  const closeToolPanelForCall = useCallback(
    (callId: string) => {
      closeSideTab(toolTabId(callId));
    },
    [closeSideTab],
  );
  const openToolPanel = useCallback(
    (opts: OpenSidePanelOptions) => {
      addSideTab({
        id: toolTabId(opts.callId),
        kind: "tool",
        callId: opts.callId,
        title: opts.title,
        toolName: opts.toolName,
      });
    },
    [addSideTab],
  );
  const openGameTab = useCallback(
    (appId: string, session: AppGameSession) => {
      addSideTab({
        id: gameTabId(appId, session.game_id),
        kind: "game",
        appId,
        session,
      });
    },
    [addSideTab],
  );
  const openGamePanel = useCallback(
    (opts: OpenGamePanelOptions) => {
      openGameTab(opts.appId, opts.session);
    },
    [openGameTab],
  );
  const toggleAppsSidebar = useCallback(() => {
    if (sidebarOpen && activeSideTabId === "apps") {
      setSidebarOpen(false);
      return;
    }
    showAppsTab();
  }, [activeSideTabId, showAppsTab, sidebarOpen]);
  // The dock's close control drops plugin tool tabs (they return to the
  // message stream) and keeps the apps / game tabs.
  const closeSidebar = useCallback(() => {
    setSidebarOpen(false);
    setSideTabs((prev) => {
      const next = prev.filter((tab) => tab.kind !== "tool");
      setActiveSideTabId((current) => fallbackSideTabId(next, current));
      return next;
    });
  }, []);
  /** Drop plugin tool tabs when the conversation changes under them. */
  const dropToolTabs = useCallback(() => {
    setSideTabs((prev) => {
      const next = prev.filter((tab) => tab.kind !== "tool");
      if (next.length === prev.length) return prev;
      setActiveSideTabId((current) => fallbackSideTabId(next, current));
      if (next.length === 0) setSidebarOpen(false);
      return next;
    });
  }, []);
  const resetSideTabs = useCallback(() => {
    setSideTabs([]);
    setActiveSideTabId(null);
    setSidebarOpen(false);
  }, []);
  const handleSelectSessionApp = useCallback((app: SessionApp) => {
    setActiveAppId(app.app_id);
  }, []);
  // Drag the sidebar's left edge. Width is written to the DOM during the drag
  // (no chat re-render / iframe churn) and committed to state on pointerup.
  const handleSidebarResizeStart = useCallback((event: React.PointerEvent) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const panel = sidebarPanelRef.current;
    const container = panel?.parentElement;
    if (!panel || !container) return;
    sidebarResizeRef.current = {
      startX: event.clientX,
      startWidth: panel.getBoundingClientRect().width,
      maxWidth: Math.max(
        SIDEBAR_MIN_WIDTH,
        container.getBoundingClientRect().width - CHAT_PANEL_MIN_WIDTH,
      ),
    };
    setSidebarResizing(true);
    beginPointerDragSession({
      pointerId: event.pointerId,
      target: event.currentTarget as HTMLElement,
      cursor: "col-resize",
      onMove: (clientX) => {
        const drag = sidebarResizeRef.current;
        const el = sidebarPanelRef.current;
        if (!drag || !el) return;
        const width = Math.min(
          drag.maxWidth,
          Math.max(
            SIDEBAR_MIN_WIDTH,
            drag.startWidth + (drag.startX - clientX),
          ),
        );
        el.style.width = `${width}px`;
      },
      onEnd: () => {
        const el = sidebarPanelRef.current;
        const drag = sidebarResizeRef.current;
        sidebarResizeRef.current = null;
        setSidebarResizing(false);
        if (!el || !drag) return;
        const width = Math.round(el.getBoundingClientRect().width);
        setSidebarWidth(width);
        rememberSidebarWidth(width);
      },
    });
  }, []);
  const toolHostOverrides = useMemo(
    () => ({
      openSidePanel: openToolPanel,
      closeSidePanel: closeToolPanelForCall,
      openGamePanel,
    }),
    [closeToolPanelForCall, openGamePanel, openToolPanel],
  );
  const handleGameSession = useCallback(
    (appId: string, session: AppGameSession) => {
      openGameTab(appId, session);
    },
    [openGameTab],
  );
  const handleOpenExternal = useCallback(
    (url: string) => {
      if (onOpenExternal && spaceId) onOpenExternal(url, spaceId);
    },
    [onOpenExternal, spaceId],
  );

  const openFilePreview = useCallback(
    (path: string, sourceAgentId?: string | null) => {
      onOpenFile?.(path, sourceAgentId ?? agentId, threadId);
    },
    [agentId, onOpenFile, threadId],
  );
  const openKnowledgeCitation = useCallback(() => {}, []);

  const pendingApproval = useMemo(
    () => hasPendingHitl(messages) || Boolean(findPendingAsk(messages)),
    [messages],
  );
  const pendingApprovalId = useMemo(
    () =>
      findPendingApproval(messages)?.pendingId ??
      findPendingAsk(messages)?.pendingId,
    [messages],
  );
  const hadPendingApprovalRef = useRef(false);
  useEffect(() => {
    if (pendingApproval && !hadPendingApprovalRef.current) {
      addDesktopNotification({
        kind: "approval",
        title: tr(
          "apps.desktop.notifications.approvalTitle",
          "Expert needs your confirmation",
        ),
        body: fillTemplate(
          tr(
            "apps.desktop.notifications.approvalBody",
            "{{name}} is waiting for your confirmation.",
          ),
          { name: agentName },
        ),
        windowId,
        dedupeKey: `approval:${pendingApprovalId ?? threadId ?? agentId}`,
      });
    }
    hadPendingApprovalRef.current = pendingApproval;
  }, [
    agentId,
    agentName,
    pendingApproval,
    pendingApprovalId,
    threadId,
    tr,
    windowId,
  ]);

  const replyStreamingRef = useRef(isStreaming);
  useEffect(() => {
    const wasStreaming = replyStreamingRef.current;
    replyStreamingRef.current = isStreaming;
    if (wasStreaming && !isStreaming && !active && messages.length > 0) {
      addDesktopNotification({
        kind: "success",
        title: tr("apps.desktop.notifications.replyTitle", "Expert replied"),
        body: fillTemplate(
          tr(
            "apps.desktop.notifications.replyBody",
            "{{name}} replied in a conversation.",
          ),
          { name: agentName },
        ),
        windowId,
      });
    }
  }, [active, agentName, isStreaming, messages.length, tr, windowId]);

  const handleSend = useCallback(
    (text: string, attachments?: ChatAttachment[]) => {
      if (!threadId) return;
      sendMessage(
        text,
        "",
        agentId,
        attachments,
        undefined,
        activeThread?.model_ref ?? undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        activeThread?.reasoning_mode ?? "auto",
        activeThread?.reasoning_effort ?? null,
        conversationMode,
        hitlPolicy,
        "web",
      );
    },
    [
      activeThread,
      agentId,
      conversationMode,
      hitlPolicy,
      sendMessage,
      threadId,
    ],
  );

  const handleNewChat = useCallback(() => {
    setPanel(null);
    resetSideTabs();
    void createThread();
  }, [createThread, resetSideTabs]);

  const handleEditUserMessage = useCallback(
    (messageId: string, newText: string) => {
      if (!threadId) return;
      editAndResend(messageId, newText, threadId, agentId);
    },
    [agentId, editAndResend, threadId],
  );

  const handleHitlDecision: HitlDecisionHandler = useCallback(
    (decisions, policy, pendingId) => {
      if (policy) {
        const next =
          policy.mode === "allow_tools"
            ? mergeAllowTools(hitlPolicy ?? { mode: "ask" }, policy.tools ?? [])
            : policy;
        resumeHitl(decisions, undefined, undefined, next, pendingId);
        return;
      }
      resumeHitl(decisions, undefined, undefined, undefined, pendingId);
    },
    [hitlPolicy, resumeHitl],
  );

  const handleSelectThread = useCallback(
    (id: string) => {
      setThreadId(id);
      rememberThread(agentId, id);
      setPanel(null);
      // Tool UIs are transcript-bound; the apps tab follows the new thread.
      dropToolTabs();
    },
    [agentId, dropToolTabs],
  );

  const quickPrompts = useMemo(() => {
    if (activeApp) {
      return APP_ACTIONS.map((action) => ({
        key: action.key,
        icon: action.icon,
        label: tr(action.labelKey, action.label),
        prompt: actionPromptFor(action, activeApp, tr),
      }));
    }
    return [
      {
        key: "apps",
        icon: AppWindow,
        label: tr("apps.desktop.quickApps", "Review my MiniApps"),
        prompt: tr(
          "apps.desktop.quickAppsPrompt",
          "Please list the MiniApps I own, then briefly report each one's status and the most useful next improvement.",
        ),
      },
      {
        key: "plan",
        icon: MessageSquare,
        label: tr("apps.desktop.quickPlan", "Suggest next steps"),
        prompt: tr(
          "apps.desktop.quickPlanPrompt",
          "Based on my recent MiniApps and conversations, give me three concrete next steps I can take.",
        ),
      },
    ];
  }, [activeApp, tr]);

  if (!threadId && threadsLoading) {
    return (
      <div className={styles.desktopChatBoot} data-testid="desktop-chat">
        <Spin />
      </div>
    );
  }

  if (!threadId && threadsError) {
    return (
      <div className={styles.desktopChatBoot} data-testid="desktop-chat">
        <span>
          {tr(
            "apps.desktop.chatBootFailed",
            "Could not load this expert's conversations.",
          )}
        </span>
        <button
          type="button"
          className={styles.desktopChatBootRetry}
          onClick={() => setBootKey((key) => key + 1)}
        >
          <RefreshCw size={13} />
          {tr("common.retry", "Retry")}
        </button>
      </div>
    );
  }

  return (
    <div className={styles.desktopChat} data-testid="desktop-chat">
      <header className={styles.desktopChatHeader}>
        <span
          className={styles.desktopChatAvatar}
          style={{ color: agent?.color || "#6366f1" }}
        >
          <ExpertIcon
            iconName={agent?.icon_name}
            iconUrl={agent?.icon_url}
            size={20}
          />
        </span>
        <div className={styles.desktopChatIdentity}>
          <span className={styles.desktopChatName}>{agentName}</span>
          <span className={styles.desktopChatState} data-state={stateKey}>
            <span className={styles.desktopChatStateDot} />
            {statusLabel}
          </span>
        </div>
        <div className={styles.desktopChatHeaderActions}>
          {onOpenFiles && (
            <Tooltip title={tr("apps.desktop.files.label", "Files")}>
              <button
                type="button"
                className={styles.desktopChatIconButton}
                data-testid="desktop-chat-files"
                aria-label={tr("apps.desktop.files.label", "Files")}
                onClick={() => onOpenFiles(agentId, threadId)}
              >
                <FolderOpen size={15} />
              </button>
            </Tooltip>
          )}
          <Tooltip
            title={
              appsTabOpen
                ? tr("chat.sessionAppsClose", "Close MiniApp sidebar")
                : tr("chat.sessionAppsOpen", "Open MiniApp sidebar")
            }
          >
            <button
              type="button"
              className={`${styles.desktopChatIconButton} ${
                appsTabOpen ? styles.desktopChatIconButtonActive : ""
              }`}
              data-testid="desktop-chat-apps-toggle"
              aria-label={
                appsTabOpen
                  ? tr("chat.sessionAppsClose", "Close MiniApp sidebar")
                  : tr("chat.sessionAppsOpen", "Open MiniApp sidebar")
              }
              aria-pressed={appsTabOpen}
              onClick={toggleAppsSidebar}
            >
              {appsTabOpen ? (
                <PanelRightClose size={15} />
              ) : (
                <PanelRightOpen size={15} />
              )}
            </button>
          </Tooltip>
          <button
            type="button"
            className={styles.desktopChatThreadButton}
            data-testid="desktop-chat-thread-trigger"
            aria-haspopup="dialog"
            aria-expanded={panel === "threads"}
            onClick={() =>
              setPanel((current) => (current === "threads" ? null : "threads"))
            }
          >
            <MessageSquare size={13} />
            <span>{title}</span>
            <ChevronDown size={12} />
          </button>
        </div>
      </header>

      {adminProxyAgentOwner !== null && (
        <div
          className={styles.desktopChatProxyBanner}
          data-testid="desktop-chat-admin-proxy"
          role="note"
        >
          <ShieldAlert size={12} />
          <span className={styles.desktopChatProxyTitle}>
            {tr("apps.desktop.adminProxy", "Admin proxy")}
          </span>
          <span>
            {tr("apps.desktop.adminProxyAgent", "Expert owner")}:{" "}
            {adminProxyAgentOwner || "—"}
          </span>
          {adminProxyAppOwner && (
            <span>
              {tr("apps.desktop.adminProxyApp", "MiniApp owner")}:{" "}
              {adminProxyAppOwner}
            </span>
          )}
          <span className={styles.desktopChatProxyAudit}>
            {tr(
              "apps.desktop.adminProxyAudit",
              "Writes are recorded in the audit log",
            )}
          </span>
        </div>
      )}

      {panel === "threads" && (
        <>
          <button
            type="button"
            className={styles.desktopChatPanelBackdrop}
            aria-label={tr("apps.desktop.closePanel", "Close panel")}
            onClick={() => setPanel(null)}
          />
          <div
            className={styles.desktopChatPanel}
            role="dialog"
            aria-label={tr("apps.desktop.sessions", "Conversations")}
            data-testid="desktop-chat-panel-threads"
          >
            <div className={styles.desktopChatPanelHeader}>
              <span>{tr("apps.desktop.sessions", "Conversations")}</span>
              <button
                type="button"
                className={styles.desktopChatPanelAction}
                data-testid="desktop-chat-panel-new"
                onClick={handleNewChat}
              >
                <Plus size={12} />
                {tr("apps.desktop.newChat", "New conversation")}
              </button>
            </div>
            <div className={styles.desktopChatPanelList}>
              {threads.length === 0 ? (
                <div className={styles.desktopChatPanelEmpty}>
                  {tr("apps.desktop.noSessions", "No conversations yet.")}
                </div>
              ) : (
                threads.map((thread) => {
                  const active = thread.thread_id === threadId;
                  const stamp = thread.last_active
                    ? formatMessageTime(thread.last_active * 1000, timeZone)
                    : "";
                  return (
                    <button
                      key={thread.thread_id}
                      type="button"
                      className={[
                        styles.desktopChatThreadItem,
                        active ? styles.desktopChatThreadItemActive : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                      data-testid="desktop-chat-thread"
                      data-thread-id={thread.thread_id}
                      aria-pressed={active}
                      onClick={() => handleSelectThread(thread.thread_id)}
                    >
                      <span className={styles.desktopChatThreadItemMain}>
                        <span className={styles.desktopChatThreadItemName}>
                          {threadTitle(
                            thread,
                            tr(
                              "apps.desktop.untitledThread",
                              "New conversation",
                            ),
                          )}
                        </span>
                        <span className={styles.desktopChatThreadItemMeta}>
                          {stamp || tr("apps.desktop.emptyThread", "Empty")}
                          {thread.channel_type !== "dashboard"
                            ? ` · ${thread.channel_type}`
                            : ""}
                        </span>
                      </span>
                      {active && <Check size={14} />}
                    </button>
                  );
                })
              )}
            </div>
          </div>
        </>
      )}

      <ToolHostScopeProvider overrides={toolHostOverrides}>
        <div className={styles.desktopChatSplit}>
          <div className={styles.desktopChatMain}>
            <div className={styles.desktopChatBody}>
              {historyError && (
                <div className={styles.desktopChatError}>
                  <span>
                    {tr(
                      "apps.desktop.historyError",
                      "Could not load this conversation.",
                    )}
                  </span>
                  <button type="button" onClick={() => void retryHistory()}>
                    <RefreshCw size={12} />
                    {tr("common.retry", "Retry")}
                  </button>
                </div>
              )}
              {!historyLoading && historyHydrated && messages.length === 0 ? (
                <div className={styles.desktopChatWelcome}>
                  <span
                    className={styles.desktopChatWelcomeAvatar}
                    style={{ color: agent?.color || "#6366f1" }}
                  >
                    <ExpertIcon
                      iconName={agent?.icon_name}
                      iconUrl={agent?.icon_url}
                      size={30}
                    />
                  </span>
                  <h3>{agentName}</h3>
                  <p>
                    {agent?.welcome_message ||
                      agent?.description ||
                      tr(
                        "apps.desktop.chatWelcome",
                        "Ask about your MiniApps, data, or anything else on this desktop.",
                      )}
                  </p>
                  <div className={styles.desktopChatQuickActions}>
                    {quickPrompts.map(({ key, icon: Icon, label, prompt }) => (
                      <button
                        key={key}
                        type="button"
                        className={styles.desktopChatQuickAction}
                        onClick={() => prefill(prompt)}
                      >
                        <Icon size={14} />
                        <span>{label}</span>
                      </button>
                    ))}
                  </div>
                  {!activeApp && apps.length > 0 && (
                    <button
                      type="button"
                      className={styles.desktopChatWelcomePick}
                      onClick={showAppsTab}
                    >
                      <AppWindow size={13} />
                      {tr(
                        "apps.desktop.pickAppHint",
                        "Pick a MiniApp to work on it together",
                      )}
                    </button>
                  )}
                </div>
              ) : (
                <ChatFilePreviewProvider
                  openFilePreview={openFilePreview}
                  openKnowledgeCitation={openKnowledgeCitation}
                >
                  <MessageList
                    messages={messages}
                    agentId={agentId}
                    loading={historyLoading}
                    historyHasMore={historyHasMore}
                    historyLoadingMore={historyLoadingMore}
                    historyRefreshing={historyRefreshing}
                    onLoadMoreHistory={loadMoreHistory}
                    onRefreshHistory={refreshHistory}
                    isStreaming={isStreaming}
                    liveSpeakers={liveSpeakers}
                    thinkingStartedAt={thinkingStartedAt}
                    sessionKey={threadId ?? undefined}
                    onEditUserMessage={handleEditUserMessage}
                    onHitlDecision={handleHitlDecision}
                    onEditFile={
                      onOpenFiles
                        ? () => onOpenFiles(agentId, threadId)
                        : undefined
                    }
                  />
                </ChatFilePreviewProvider>
              )}
            </div>

            <div className={styles.desktopChatComposer}>
              {!chatReady && (
                <div className={styles.desktopChatOffline}>
                  {tr(
                    "apps.desktop.expertOffline",
                    "This expert is not running. Start it before sending a message.",
                  )}
                </div>
              )}
              {threadId && (
                <ChatInput
                  ref={inputRef}
                  onSend={handleSend}
                  onCancel={cancelStream}
                  onNewChat={handleNewChat}
                  isStreaming={isStreaming}
                  disabled={!chatReady}
                  agentId={agentId}
                  threadId={threadId}
                  defaultModel={agent?.default_model ?? null}
                  conversationMode={conversationMode}
                  hitlPolicy={hitlPolicy ?? undefined}
                  availableAgents={agents}
                  contextUsedTokens={contextUsage?.input_tokens ?? null}
                  contextMaxTokens={agent?.max_input_length ?? 128_000}
                  decisionSaved={contextUsage?.decision_saved ?? 0}
                />
              )}
            </div>

            <footer className={styles.desktopChatFooter}>
              <span
                className={styles.desktopChatFooterState}
                data-state={stateKey}
              >
                <span className={styles.desktopChatFooterDot} />
                {statusLabel}
              </span>
              <span>
                {fillTemplate(
                  tr("apps.desktop.messageCount", "{{count}} messages"),
                  {
                    count: messages.length,
                  },
                )}
              </span>
              {runUsage?.total_tokens ? (
                <span>{runUsage.total_tokens.toLocaleString()} tokens</span>
              ) : null}
              <span className={styles.desktopChatFooterSpacer} />
              <span className={styles.desktopChatFooterModel}>
                {agent?.default_model ||
                  tr("apps.desktop.defaultModel", "Default model")}
              </span>
            </footer>
          </div>
          {sidebarOpen && sideTabs.length > 0 && (
            <>
              <div
                className={`${styles.desktopChatSidebarResizer} ${
                  sidebarResizing ? styles.desktopChatSidebarResizerActive : ""
                }`}
                role="separator"
                aria-orientation="vertical"
                aria-label={tr("chat.resizeSidebar", "Resize sidebar")}
                data-testid="desktop-chat-sidebar-resizer"
                onPointerDown={handleSidebarResizeStart}
              >
                <span className={styles.desktopChatSidebarResizerHandle} />
              </div>
              <aside
                ref={sidebarPanelRef}
                className={styles.desktopChatSidebar}
                style={{ width: sidebarWidth }}
                data-testid="desktop-chat-sidebar"
              >
                <div className={styles.desktopChatSidebarTabBar}>
                  <div className={styles.desktopChatSidebarTabs} role="tablist">
                    {sideTabs.map((tab) => {
                      const isActive = tab.id === activeSideTabId;
                      const label =
                        tab.kind === "apps"
                          ? tr("chat.dockMiniApps", "MiniApps")
                          : tab.kind === "tool"
                          ? tab.title ||
                            tab.toolName ||
                            tr("chat.dockToolUiTitle", "Plugin tool")
                          : tab.session.name;
                      return (
                        <div
                          key={tab.id}
                          className={`${styles.desktopChatSidebarTab} ${
                            isActive ? styles.desktopChatSidebarTabActive : ""
                          }`}
                          role="tab"
                          aria-selected={isActive}
                          data-testid="desktop-chat-side-tab"
                          data-tab-id={tab.id}
                          data-tab-kind={tab.kind}
                        >
                          <button
                            type="button"
                            className={styles.desktopChatSidebarTabLabel}
                            title={label}
                            onClick={() => setActiveSideTabId(tab.id)}
                          >
                            {tab.kind === "apps" ? (
                              <AppWindow size={13} aria-hidden />
                            ) : tab.kind === "tool" ? (
                              <Puzzle size={13} aria-hidden />
                            ) : (
                              <Gamepad2 size={13} aria-hidden />
                            )}
                            <span>{label}</span>
                          </button>
                          <button
                            type="button"
                            className={styles.desktopChatSidebarTabClose}
                            data-testid="desktop-chat-side-tab-close"
                            data-tab-id={tab.id}
                            aria-label={tr("common.close", "Close")}
                            title={tr("common.close", "Close")}
                            onClick={() => closeSideTab(tab.id)}
                          >
                            <X size={11} />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                  <button
                    type="button"
                    className={styles.desktopChatSidebarClose}
                    data-testid="desktop-chat-sidebar-close"
                    aria-label={tr("apps.desktop.closePanel", "Close panel")}
                    title={tr("apps.desktop.closePanel", "Close panel")}
                    onClick={closeSidebar}
                  >
                    <X size={13} />
                  </button>
                </div>
                <div className={styles.desktopChatSidebarBody}>
                  {sideTabs.map((tab) => {
                    const isActive = tab.id === activeSideTabId;
                    return (
                      <div
                        key={tab.id}
                        className={styles.desktopChatSidebarTabBody}
                        role="tabpanel"
                        hidden={!isActive}
                        style={{ display: isActive ? "flex" : "none" }}
                      >
                        {tab.kind === "apps" ? (
                          <ChatDockSessionApps
                            apps={sessionApps}
                            active={active && isActive}
                            onSelectApp={handleSelectSessionApp}
                            onComposeMessage={prefill}
                            onOpenExternal={
                              onOpenExternal ? handleOpenExternal : undefined
                            }
                          />
                        ) : tab.kind === "tool" ? (
                          <DesktopChatToolPanel
                            embedded
                            state={{
                              kind: "tool",
                              callId: tab.callId,
                              title: tab.title,
                              toolName: tab.toolName,
                            }}
                            messages={messages}
                            agentId={agentId}
                            isStreaming={isStreaming}
                            active={active && isActive}
                            onClose={() => closeSideTab(tab.id)}
                            onComposeMessage={prefill}
                            onGameSession={handleGameSession}
                            onOpenExternal={
                              onOpenExternal ? handleOpenExternal : undefined
                            }
                          />
                        ) : (
                          <GameFrame
                            appId={tab.appId}
                            session={tab.session}
                            onFinished={() => closeSideTab(tab.id)}
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              </aside>
            </>
          )}
        </div>
      </ToolHostScopeProvider>
    </div>
  );
}

export default memo(DesktopChatWindow);
