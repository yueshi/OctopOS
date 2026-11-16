import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DesktopGrid from "./DesktopGrid";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
    i18n: { language: "en" },
  }),
}));

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

const currentUser = vi.hoisted(() => ({
  value: { id: 1, username: "root", role: "admin" } as {
    id: number;
    username: string;
    role: string;
  },
}));

vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => currentUser.value,
}));

vi.mock("../../api/modules/apps", () => ({
  appsApi: {
    accessRequests: vi.fn().mockResolvedValue({ requests: [] }),
  },
}));

const requestMock = vi.hoisted(() => vi.fn());

vi.mock("../../api/request", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/request")>();
  return { ...actual, request: requestMock };
});

const allAgents = [
  {
    agent_id: "agent_owned",
    name: "自建专家",
    state: "running",
    color: "#22c55e",
    icon_name: "sparkles",
    icon_url: null,
    is_owner: true,
    is_shared: false,
  },
  {
    agent_id: "agent_shared",
    name: "共享专家",
    state: "running",
    color: "#6366f1",
    icon_name: "bot",
    icon_url: null,
    is_owner: false,
    is_shared: true,
  },
  {
    agent_id: "agent_blocked",
    name: "无权专家",
    state: "running",
    color: "#ef4444",
    icon_name: "bot",
    icon_url: null,
    is_owner: false,
    is_shared: false,
  },
];

vi.mock("../../context/AgentContext", () => ({
  useAgent: () => ({
    agents: [
      {
        agent_id: "agent_owned",
        name: "自建专家",
        state: "running",
        color: "#22c55e",
        icon_name: "sparkles",
        icon_url: null,
        is_owner: true,
        is_shared: false,
      },
      {
        agent_id: "agent_shared",
        name: "共享专家",
        state: "running",
        color: "#6366f1",
        icon_name: "bot",
        icon_url: null,
        is_owner: false,
        is_shared: true,
      },
      {
        agent_id: "agent_blocked",
        name: "无权专家",
        state: "running",
        color: "#ef4444",
        icon_name: "bot",
        icon_url: null,
        is_owner: false,
        is_shared: false,
      },
    ],
    activeAgentId: null,
    loading: false,
  }),
  selectEnabledExperts: (agents: unknown[]) => agents,
}));

vi.mock("../../components/MiniAppFrame", () => ({
  default: () => <div data-testid="mini-app-frame" />,
}));

vi.mock("./DesktopWebView", () => ({
  default: () => <div data-testid="desktop-webview" />,
}));

vi.mock("./DesktopChatWindow", () => ({
  default: ({ agentId }: { agentId: string }) => (
    <div data-testid="desktop-chat" data-agent-id={agentId} />
  ),
}));

describe("DesktopGrid expert access", () => {
  beforeEach(() => {
    localStorage.clear();
    currentUser.value = { id: 1, username: "root", role: "admin" };
    requestMock.mockReset();
    requestMock.mockResolvedValue(allAgents);
  });

  it("only lists experts the user owns or that are shared", () => {
    render(<DesktopGrid apps={[]} />);

    fireEvent.click(screen.getByTestId("desktop-space-trigger"));

    const options = screen.getAllByTestId("desktop-space-option");
    expect(options).toHaveLength(3); // two accessible experts + All Apps
    expect(screen.getAllByText("自建专家").length).toBeGreaterThan(0);
    expect(screen.getAllByText("共享专家").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("无权专家")).toHaveLength(0);
  });

  it("fetches all experts and reveals unshared ones in admin mode", async () => {
    render(<DesktopGrid apps={[]} />);

    fireEvent.click(screen.getByTestId("desktop-space-trigger"));
    expect(screen.getAllByTestId("desktop-space-option")).toHaveLength(3);
    expect(screen.queryAllByText("无权专家")).toHaveLength(0);
    // The desktop also syncs the notification inbox on mount; only the
    // all-experts fetch must wait for the admin switch.
    expect(requestMock).not.toHaveBeenCalledWith("/agents?scope=all");

    fireEvent.click(screen.getByRole("switch"));
    expect((await screen.findAllByText("无权专家")).length).toBeGreaterThan(0);
    expect(requestMock).toHaveBeenCalledWith("/agents?scope=all");

    if (screen.queryAllByTestId("desktop-space-option").length === 0) {
      fireEvent.click(screen.getByTestId("desktop-space-trigger"));
    }
    expect(screen.getAllByTestId("desktop-space-option")).toHaveLength(4);
  });

  it("hides the admin mode switch from non-admins", () => {
    currentUser.value = { id: 2, username: "bob", role: "user" };
    render(<DesktopGrid apps={[]} />);

    expect(screen.queryByTestId("desktop-admin-mode")).not.toBeInTheDocument();
  });

  it("drops a restored chat window for an inaccessible expert", () => {
    localStorage.setItem(
      "octop:apps-desktop-windows:v1",
      JSON.stringify([
        {
          id: "chat:agent_blocked",
          kind: "chat",
          agentId: "agent_blocked",
          x: 40,
          y: 60,
          w: 520,
          h: 420,
          z: 1,
          minimized: false,
          maximized: false,
        },
        {
          id: "chat:agent_shared",
          kind: "chat",
          agentId: "agent_shared",
          x: 80,
          y: 80,
          w: 520,
          h: 420,
          z: 2,
          minimized: false,
          maximized: false,
        },
      ]),
    );

    render(<DesktopGrid apps={[]} />);

    const chats = screen.getAllByTestId("desktop-chat");
    expect(chats).toHaveLength(1);
    expect(chats[0]).toHaveAttribute("data-agent-id", "agent_shared");
  });
});
