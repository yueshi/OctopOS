import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ForwardedRef } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { appsApi, type AppSummary } from "../../api/modules/apps";
import type { OctopThread } from "../../api/modules/octopThreads";
import type { OctopAgent } from "../../context/AgentContext";
import DesktopChatWindow from "./DesktopChatWindow";
import {
  clearDesktopNotifications,
  getDesktopNotifications,
} from "./desktopNotifications";

const { mockList, mockCreate, mockUseChat, mockUsePluginToolUis, currentUser } =
  vi.hoisted(() => ({
    mockList: vi.fn(),
    mockCreate: vi.fn(),
    mockUseChat: vi.fn(),
    mockUsePluginToolUis: vi.fn(),
    currentUser: {
      value: { id: 1, username: "alice", role: "admin" } as {
        id: number;
        username: string;
        role: string;
      },
    },
  }));

vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => currentUser.value,
}));

vi.mock("../../api/modules/octopThreads", () => ({
  octopThreadsApi: {
    list: mockList,
    create: mockCreate,
  },
}));

vi.mock("../Chat/hooks/useChat", () => ({
  useChat: mockUseChat,
}));

vi.mock("../../plugins/toolRenderers", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../plugins/toolRenderers")
  >();
  return { ...actual, usePluginToolUis: mockUsePluginToolUis };
});

vi.mock("../Chat/components/MessageList", async () => {
  const { useChatFilePreview } = await import("../Chat/ChatFilePreviewContext");
  const { useToolHostOverrides } = await import(
    "../../plugins/toolRenderers/ToolHostScope"
  );
  return {
    default: function MockMessageList({
      messages,
      onEditFile,
      compactProcess,
    }: {
      messages: unknown[];
      onEditFile?: () => void;
      compactProcess?: boolean;
    }) {
      const preview = useChatFilePreview();
      const hostOverrides = useToolHostOverrides();
      return (
        <div
          data-testid="message-list"
          data-count={messages.length}
          data-compact-process={compactProcess ? "true" : "false"}
        >
          <button
            type="button"
            data-testid="mock-file-preview"
            onClick={() =>
              preview?.openFilePreview("outbound/report.html", "agent_1")
            }
          />
          <button
            type="button"
            data-testid="mock-edit-file"
            onClick={() => onEditFile?.()}
          />
          <button
            type="button"
            data-testid="mock-open-side-panel"
            onClick={() =>
              hostOverrides?.openSidePanel({
                callId: "call_panel",
                title: "客户台账",
              })
            }
          />
        </div>
      );
    },
  };
});

vi.mock("../Chat/components/ChatInput", async () => {
  const React = await import("react");
  return {
    default: React.forwardRef(function MockChatInput(
      props: { onNewChat: () => void },
      ref: ForwardedRef<{
        setPrefillText: (text: string) => void;
        setPrefillComposer: (text: string) => void;
        focusComposer: () => void;
      }>,
    ) {
      const [text, setText] = React.useState("");
      React.useImperativeHandle(ref, () => ({
        setPrefillText: (value: string) => setText(value),
        setPrefillComposer: (value: string) => setText(value),
        focusComposer: () => undefined,
      }));
      return (
        <div data-testid="desktop-chat-composer">
          <textarea
            aria-label="composer"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          <button
            type="button"
            aria-label="Mock new conversation"
            onClick={props.onNewChat}
          />
        </div>
      );
    }),
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
  }),
}));

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("../../components/MiniAppFrame", () => ({
  default: ({ appId }: { appId: string }) => (
    <div data-testid="mini-app-frame" data-app-id={appId} />
  ),
}));

vi.mock("../../hooks/usePreviewImage", () => ({
  usePreviewImage: () => null,
}));

const agent: OctopAgent = {
  id: 1,
  agent_id: "agent_1",
  name: "销售助理",
  description: "跟进客户",
  user_id: 1,
  owner_username: "alice",
  is_owner: true,
  persona_mbti: null,
  default_model: "openai/gpt-5",
  system_prompt: null,
  template_name: null,
  state: "running",
  last_error: null,
  icon: null,
  icon_name: null,
  icon_url: null,
  color: "#6366f1",
  config: {},
  welcome_message: "今天想先处理哪个小程序？",
};

const dashboardThread: OctopThread = {
  thread_id: "thr_dash",
  title: "客户跟进",
  channel_type: "dashboard",
  session_key: "session",
  last_active: 1_700_000_000,
  created_at: 1_700_000_000,
  has_messages: true,
};

const imThread: OctopThread = {
  ...dashboardThread,
  thread_id: "thr_im",
  title: "微信里的客户",
  channel_type: "wecom",
};

const app: AppSummary = {
  app_id: "app_1",
  name: "客户跟进台账",
  description: "按阶段管理客户",
  agent_id: "agent_1",
  agent_name: "销售助理",
  owner_user_id: 1,
  owner_username: "boss",
  permission: "edit_structure",
  row_count: 12,
  html_bytes: 100,
  data_bytes: 100,
  has_html: true,
  has_preview: false,
  created_at: 0,
  updated_at: 0,
};

const appCardMessage = {
  id: "m_app",
  role: "tool",
  content: "",
  toolData: {
    name: "app_open",
    output: JSON.stringify({
      octop_ui: { renderer: "app_card", version: 1 },
      data: {
        app_id: "app_1",
        name: "客户跟进台账",
        row_count: 12,
        has_preview: false,
        capabilities: [],
      },
    }),
  },
};

function mockChat(options?: { messages?: unknown[]; isStreaming?: boolean }) {
  mockUseChat.mockReturnValue({
    messages: options?.messages ?? [],
    isStreaming: options?.isStreaming ?? false,
    thinkingStartedAt: null,
    historyLoading: false,
    historyError: false,
    historyHasMore: false,
    historyLoadingMore: false,
    historyRefreshing: false,
    historyHydrated: true,
    runUsage: null,
    contextUsage: null,
    liveSpeakers: [],
    sendMessage: vi.fn(),
    editAndResend: vi.fn(),
    cancelStream: vi.fn(),
    loadHistory: vi.fn(),
    loadMoreHistory: vi.fn(),
    refreshHistory: vi.fn(),
    retryHistory: vi.fn(),
    resumeHitl: vi.fn(),
  });
}

describe("DesktopChatWindow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(appsApi, "inThread").mockResolvedValue({ apps: [] });
    localStorage.clear();
    clearDesktopNotifications();
    currentUser.value = { id: 1, username: "alice", role: "admin" };
    mockList.mockResolvedValue([imThread, dashboardThread]);
    mockCreate.mockResolvedValue({
      thread_id: "thr_new",
      session_key: "new-session",
    });
    mockChat();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("resumes the latest dashboard conversation and shows the message stream", async () => {
    mockChat({ messages: [{ id: "m1" }, { id: "m2" }] });
    render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
      />,
    );

    await waitFor(() =>
      expect(mockUseChat).toHaveBeenLastCalledWith(
        "thr_dash",
        "agent_1",
        false,
      ),
    );
    expect(screen.getByText("销售助理")).toBeInTheDocument();
    expect(screen.getByText("客户跟进")).toBeInTheDocument();
    expect(screen.getByTestId("message-list")).toHaveAttribute(
      "data-count",
      "2",
    );
    // The desktop chat must not suppress thinking/tool process content.
    expect(screen.getByTestId("message-list")).toHaveAttribute(
      "data-compact-process",
      "false",
    );
    expect(mockUsePluginToolUis).toHaveBeenCalledWith(
      expect.objectContaining({ agentId: "agent_1" }),
    );
  });

  it("lists conversation MiniApps in the sidebar and runs one in place", async () => {
    mockChat({ messages: [appCardMessage] });
    render(
      <MemoryRouter>
        <DesktopChatWindow
          agentId="agent_1"
          agentName="销售助理"
          agent={agent}
          apps={[app]}
        />
      </MemoryRouter>,
    );

    await screen.findByText("客户跟进");
    fireEvent.click(screen.getByTestId("desktop-chat-apps-toggle"));

    expect(screen.getByTestId("desktop-chat-sidebar")).toHaveTextContent(
      "客户跟进台账",
    );
    fireEvent.click(screen.getByText("客户跟进台账"));

    expect(screen.getByTestId("mini-app-frame")).toHaveAttribute(
      "data-app-id",
      "app_1",
    );
  });

  it("opens and closes the MiniApp sidebar from the header toggle", async () => {
    render(
      <MemoryRouter>
        <DesktopChatWindow
          agentId="agent_1"
          agentName="销售助理"
          agent={agent}
          apps={[app]}
        />
      </MemoryRouter>,
    );

    await screen.findByText("客户跟进");
    fireEvent.click(screen.getByTestId("desktop-chat-apps-toggle"));
    expect(screen.getByTestId("desktop-chat-sidebar")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("desktop-chat-sidebar-close"));
    expect(
      screen.queryByTestId("desktop-chat-sidebar"),
    ).not.toBeInTheDocument();
  });

  it("resizes the side panel by dragging its edge", async () => {
    mockChat({ messages: [{ id: "m1" }] });
    render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
      />,
    );

    await screen.findByText("客户跟进");
    fireEvent.click(screen.getByTestId("mock-open-side-panel"));

    const panel = screen.getByTestId("desktop-chat-sidebar") as HTMLElement;
    const container = panel.parentElement as HTMLElement;
    // jsdom has no layout: fake the measured widths the drag reads.
    Object.defineProperty(panel, "getBoundingClientRect", {
      configurable: true,
      value: () => ({
        width: Number.parseFloat(panel.style.width) || 300,
        height: 500,
        left: 600,
        top: 0,
        right: 900,
        bottom: 500,
        x: 600,
        y: 0,
        toJSON: () => ({}),
      }),
    });
    Object.defineProperty(container, "getBoundingClientRect", {
      configurable: true,
      value: () => ({
        width: 900,
        height: 500,
        left: 0,
        top: 0,
        right: 900,
        bottom: 500,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }),
    });

    const handle = screen.getByTestId("desktop-chat-sidebar-resizer");
    fireEvent(
      handle,
      new MouseEvent("pointerdown", {
        bubbles: true,
        button: 0,
        clientX: 500,
      }),
    );
    fireEvent(
      document,
      new MouseEvent("pointermove", { bubbles: true, clientX: 400 }),
    );
    fireEvent(document, new MouseEvent("pointerup", { bubbles: true }));

    // Dragging left by 100px widens the sidebar from 300px to 400px.
    expect(panel.style.width).toBe("400px");
  });

  it("lists conversations and switches to the selected thread", async () => {
    render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
      />,
    );

    await screen.findByText("客户跟进");
    fireEvent.click(screen.getByTestId("desktop-chat-thread-trigger"));

    const threads = screen.getAllByTestId("desktop-chat-thread");
    expect(threads).toHaveLength(2);
    fireEvent.click(threads[0]);

    await waitFor(() =>
      expect(mockUseChat).toHaveBeenLastCalledWith("thr_im", "agent_1", false),
    );
  });

  it("creates a new conversation from the conversations panel", async () => {
    render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
      />,
    );

    await screen.findByText("客户跟进");
    fireEvent.click(screen.getByTestId("desktop-chat-thread-trigger"));
    fireEvent.click(screen.getByTestId("desktop-chat-panel-new"));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith("agent_1"));
  });

  it("shows an admin proxy banner when using another user's expert", async () => {
    const sharedAgent: OctopAgent = {
      ...agent,
      user_id: 3,
      owner_username: "jk",
      is_owner: false,
    };
    render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={sharedAgent}
      />,
    );

    await screen.findByText("客户跟进");
    const banner = screen.getByTestId("desktop-chat-admin-proxy");
    expect(banner).toHaveTextContent("Admin proxy");
    expect(banner).toHaveTextContent("jk");
  });

  it("notifies the desktop when a permission card arrives", async () => {
    const view = render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
        apps={[app]}
      />,
    );
    await screen.findByText("客户跟进");

    mockChat({
      messages: [
        {
          id: "perm_1",
          role: "tool",
          content: "",
          toolData: {
            name: "app_create",
            output: JSON.stringify({
              octop_ui: { renderer: "app_permission_card", version: 1 },
              data: {
                app_id: "app_1",
                agent_owner: "alice",
                app_owner: "jk",
              },
            }),
          },
        },
      ],
    });
    view.rerender(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
        apps={[app]}
      />,
    );

    await waitFor(() =>
      expect(
        getDesktopNotifications().some((item) => item.kind === "security"),
      ).toBe(true),
    );
  });

  it("opens the files window from the header", async () => {
    const onOpenFiles = vi.fn();
    render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
        onOpenFiles={onOpenFiles}
      />,
    );

    await screen.findByText("客户跟进");
    fireEvent.click(screen.getByTestId("desktop-chat-files"));

    expect(onOpenFiles).toHaveBeenCalledWith("agent_1", "thr_dash");
  });

  it("opens a chat file card in the desktop files window", async () => {
    mockChat({ messages: [{ id: "m1" }] });
    const onOpenFile = vi.fn();
    render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
        onOpenFile={onOpenFile}
      />,
    );

    await screen.findByText("客户跟进");
    fireEvent.click(screen.getByTestId("mock-file-preview"));

    expect(onOpenFile).toHaveBeenCalledWith(
      "outbound/report.html",
      "agent_1",
      "thr_dash",
    );
  });

  it("opens plugin tool UIs as a tab in the side panel", async () => {
    mockChat({ messages: [{ id: "m1" }] });
    render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
      />,
    );

    await screen.findByText("客户跟进");
    expect(
      screen.queryByTestId("desktop-chat-sidebar"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("mock-open-side-panel"));

    const tab = screen.getByTestId("desktop-chat-side-tab");
    expect(tab).toHaveAttribute("data-tab-kind", "tool");
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(tab).toHaveTextContent("客户台账");
    expect(screen.getByTestId("desktop-chat-sidebar")).toHaveTextContent(
      "no longer available",
    );

    fireEvent.click(screen.getByTestId("desktop-chat-side-tab-close"));
    expect(
      screen.queryByTestId("desktop-chat-sidebar"),
    ).not.toBeInTheDocument();
  });

  it("keeps the MiniApps tab mounted while a tool tab is active", async () => {
    mockChat({ messages: [{ id: "m1" }] });
    render(
      <MemoryRouter>
        <DesktopChatWindow
          agentId="agent_1"
          agentName="销售助理"
          agent={agent}
          apps={[app]}
        />
      </MemoryRouter>,
    );

    await screen.findByText("客户跟进");
    fireEvent.click(screen.getByTestId("desktop-chat-apps-toggle"));
    expect(screen.getAllByTestId("desktop-chat-side-tab")).toHaveLength(1);

    fireEvent.click(screen.getByTestId("mock-open-side-panel"));
    const tabs = screen.getAllByTestId("desktop-chat-side-tab");
    expect(tabs).toHaveLength(2);
    const appsTab = tabs.find(
      (tab) => tab.getAttribute("data-tab-kind") === "apps",
    )!;
    const toolTab = tabs.find(
      (tab) => tab.getAttribute("data-tab-kind") === "tool",
    )!;
    expect(toolTab).toHaveAttribute("aria-selected", "true");
    expect(appsTab).toHaveAttribute("aria-selected", "false");
    expect(screen.getByText(/no longer available/)).toBeVisible();

    fireEvent.click(screen.getByText("MiniApps"));

    expect(appsTab).toHaveAttribute("aria-selected", "true");
    expect(toolTab).toHaveAttribute("aria-selected", "false");
    expect(screen.getByText(/no longer available/)).not.toBeVisible();
    expect(
      screen.getByText("No MiniApp in this conversation yet."),
    ).toBeVisible();
  });

  it("switches to MiniApps before collapsing from the header toggle", async () => {
    mockChat({ messages: [{ id: "m1" }] });
    render(
      <MemoryRouter>
        <DesktopChatWindow
          agentId="agent_1"
          agentName="销售助理"
          agent={agent}
          apps={[app]}
        />
      </MemoryRouter>,
    );

    await screen.findByText("客户跟进");
    fireEvent.click(screen.getByTestId("desktop-chat-apps-toggle"));
    fireEvent.click(screen.getByTestId("mock-open-side-panel"));

    const appsTab = screen
      .getAllByTestId("desktop-chat-side-tab")
      .find((tab) => tab.getAttribute("data-tab-kind") === "apps")!;
    expect(appsTab).toHaveAttribute("aria-selected", "false");

    // The header control is the MiniApps toggle: it switches tabs first, and
    // collapses only when MiniApps is already the active tab.
    fireEvent.click(screen.getByTestId("desktop-chat-apps-toggle"));
    expect(screen.getByTestId("desktop-chat-sidebar")).toBeInTheDocument();
    expect(appsTab).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByTestId("desktop-chat-apps-toggle"));
    expect(
      screen.queryByTestId("desktop-chat-sidebar"),
    ).not.toBeInTheDocument();
  });

  it("opens generated files in the desktop files window", async () => {
    mockChat({ messages: [{ id: "m1" }] });
    const onOpenFiles = vi.fn();
    render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
        onOpenFiles={onOpenFiles}
      />,
    );

    await screen.findByText("客户跟进");
    fireEvent.click(screen.getByTestId("mock-edit-file"));

    expect(onOpenFiles).toHaveBeenCalledWith("agent_1", "thr_dash");
  });

  it("hides the admin proxy banner for owners and non-admins", async () => {
    const { unmount } = render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={agent}
      />,
    );
    await screen.findByText("客户跟进");
    expect(
      screen.queryByTestId("desktop-chat-admin-proxy"),
    ).not.toBeInTheDocument();
    unmount();

    currentUser.value = { id: 1, username: "alice", role: "user" };
    render(
      <DesktopChatWindow
        agentId="agent_1"
        agentName="销售助理"
        agent={{ ...agent, user_id: 3, owner_username: "jk", is_owner: false }}
      />,
    );
    await screen.findByText("客户跟进");
    expect(
      screen.queryByTestId("desktop-chat-admin-proxy"),
    ).not.toBeInTheDocument();
  });
});
