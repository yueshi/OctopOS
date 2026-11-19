import { memo, useCallback, useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";

import type { AppGameSession } from "../../api/modules/apps";
import { appsApi } from "../../api/modules/apps";
import MiniAppFrame from "../../components/MiniAppFrame";
import GameFrame from "../../components/MiniAppFrame/GameFrame";
import {
  parseOctopToolOutput,
  resolvePluginUiData,
} from "../../plugins/toolRenderers/parseToolOutput";
import { ToolDetailsInline } from "../Chat/components/MessageBubble";
import type { ChatMessage } from "../Chat/hooks/useChat";
import styles from "./index.module.less";

/** What the in-window side panel of a desktop chat currently shows. */
export type DesktopChatPanelState =
  | { kind: "tool"; callId: string; title?: string; toolName?: string }
  | { kind: "game"; appId: string; session: AppGameSession };

interface DesktopChatToolPanelProps {
  state: DesktopChatPanelState;
  messages: ChatMessage[];
  agentId: string;
  isStreaming: boolean;
  /**
   * False while the hosting desktop window is hidden: MiniApps stop polling
   * exactly like they do in a hidden app window.
   */
  active: boolean;
  onClose: () => void;
  onComposeMessage: (text: string) => void;
  onGameSession: (appId: string, session: AppGameSession) => void;
  onOpenExternal?: (url: string) => void;
  /** Render only the body; the side panel supplies its own tab chrome. */
  embedded?: boolean;
}

interface AppCardPanelData {
  appId: string;
  name: string;
  capabilities?: string[];
}

function asAppCardPanelData(value: unknown): AppCardPanelData | null {
  if (!value || typeof value !== "object") return null;
  const obj = value as Record<string, unknown>;
  if (typeof obj.app_id !== "string" || !obj.app_id.trim()) return null;
  return {
    appId: obj.app_id,
    name: typeof obj.name === "string" && obj.name ? obj.name : obj.app_id,
    capabilities: Array.isArray(obj.capabilities)
      ? obj.capabilities.filter(
          (item): item is string => typeof item === "string",
        )
      : undefined,
  };
}

/**
 * Tool UI inside the desktop chat side panel.
 *
 * App cards render `MiniAppFrame` directly instead of AppCard's docked branch:
 * that branch navigates to `/chat/:agent` when the app composes a message,
 * which would leave the Agent OS desktop. Here the message goes to this
 * window's own composer, and external links stay in desktop web windows.
 */
function DesktopToolPanelContent({
  callId,
  messages,
  agentId,
  isStreaming,
  active,
  onComposeMessage,
  onGameSession,
  onOpenExternal,
}: {
  callId: string;
  messages: ChatMessage[];
  agentId: string;
  isStreaming: boolean;
  active: boolean;
  onComposeMessage: (text: string) => void;
  onGameSession: (appId: string, session: AppGameSession) => void;
  onOpenExternal?: (url: string) => void;
}) {
  const { t } = useTranslation();
  const message = messages.find(
    (item) => item.toolData?.callId === callId && item.toolData != null,
  );
  const parsed = useMemo(
    () => parseOctopToolOutput(message?.toolData?.output),
    [message?.toolData?.output],
  );
  const data = useMemo(
    () =>
      message?.toolData
        ? resolvePluginUiData(
            parsed,
            message.toolData.artifact,
            message.toolData.output,
          )
        : undefined,
    [message?.toolData, parsed],
  );
  const appData = useMemo(
    () =>
      parsed.octopUi?.renderer === "app_card" ? asAppCardPanelData(data) : null,
    [data, parsed.octopUi?.renderer],
  );

  const launchGame = useCallback(
    async (gameId: string) => {
      if (!appData) throw new Error("app is required");
      const session = await appsApi.createGameSession(appData.appId, gameId);
      onGameSession(appData.appId, session);
      return session.session_id;
    },
    [appData, onGameSession],
  );

  if (!message?.toolData) {
    return (
      <div className={styles.desktopChatToolPanelMissing}>
        {t(
          "chat.dockToolUiMissing",
          "Tool result is no longer available in this conversation.",
        )}
      </div>
    );
  }

  if (appData) {
    return (
      <div className={styles.desktopChatToolPanelFrame}>
        <MiniAppFrame
          appId={appData.appId}
          title={appData.name}
          capabilities={appData.capabilities}
          fill
          active={active}
          foreground={active}
          onComposeMessage={onComposeMessage}
          onLaunchGame={launchGame}
          onOpenExternal={onOpenExternal}
        />
      </div>
    );
  }

  return (
    <div className={styles.desktopChatToolPanelBody}>
      <ToolDetailsInline
        toolData={message.toolData}
        isStreaming={message.status === "streaming" && isStreaming}
        agentId={agentId}
        forceInline
      />
    </div>
  );
}

/** Right-hand, in-window panel for tool UIs and MiniApp games. */
function DesktopChatToolPanel({
  state,
  messages,
  agentId,
  isStreaming,
  active,
  onClose,
  onComposeMessage,
  onGameSession,
  onOpenExternal,
  embedded = false,
}: DesktopChatToolPanelProps) {
  const { t } = useTranslation();
  const title =
    state.kind === "game"
      ? state.session.name
      : state.title ||
        state.toolName ||
        t("chat.dockToolUiTitle", "Plugin tool");

  let content: ReactNode;
  if (state.kind === "game") {
    content = (
      <GameFrame
        appId={state.appId}
        session={state.session}
        onFinished={onClose}
      />
    );
  } else {
    content = (
      <DesktopToolPanelContent
        callId={state.callId}
        messages={messages}
        agentId={agentId}
        isStreaming={isStreaming}
        active={active}
        onComposeMessage={onComposeMessage}
        onGameSession={onGameSession}
        onOpenExternal={onOpenExternal}
      />
    );
  }

  if (embedded) {
    return <div className={styles.desktopChatToolPanelContent}>{content}</div>;
  }

  return (
    <aside
      className={styles.desktopChatToolPanel}
      data-testid="desktop-chat-tool-panel"
      aria-label={title}
    >
      <header className={styles.desktopChatToolPanelHeader}>
        <span className={styles.desktopChatToolPanelTitle} title={title}>
          {title}
        </span>
        <button
          type="button"
          className={styles.desktopChatToolPanelClose}
          data-testid="desktop-chat-tool-panel-close"
          aria-label={t("common.close", "Close")}
          title={t("common.close", "Close")}
          onClick={onClose}
        >
          <X size={13} />
        </button>
      </header>
      <div className={styles.desktopChatToolPanelContent}>{content}</div>
    </aside>
  );
}

export default memo(DesktopChatToolPanel);
