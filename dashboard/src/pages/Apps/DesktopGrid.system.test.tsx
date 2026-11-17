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

vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({
    id: 2,
    username: "owner",
    role: "user",
    permissions: ["token_usage", "channels", "skills", "tasks"],
  }),
}));

vi.mock("../../context/AgentContext", () => ({
  useAgent: () => ({
    agents: [
      {
        agent_id: "agent_1",
        name: "销售助理",
        state: "running",
        color: "#22c55e",
        icon_name: "sparkles",
        icon_url: null,
        is_owner: true,
        kind: "expert",
      },
    ],
    activeAgentId: "agent_1",
    loading: false,
    setActiveAgent: vi.fn(),
  }),
  selectEnabledExperts: (agents: unknown[]) => agents,
}));

vi.mock("../../components/MiniAppFrame", () => ({
  default: () => <div data-testid="mini-app-frame" />,
}));

vi.mock("./DesktopChatWindow", () => ({
  default: ({ agentId }: { agentId: string }) => (
    <div data-testid="desktop-chat" data-agent-id={agentId} />
  ),
}));

vi.mock("./system/SystemAppFrame", () => ({
  default: ({
    appKey,
    agentId,
    onOpenConsole,
  }: {
    appKey: string;
    agentId: string;
    onOpenConsole?: (path: string) => void;
  }) => (
    <div
      data-testid="system-app-frame"
      data-system-key={appKey}
      data-agent-id={agentId}
    >
      <button
        type="button"
        data-testid="system-open-console"
        onClick={() => onOpenConsole?.("/token-usage")}
      />
    </div>
  ),
}));

describe("DesktopGrid system apps", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("shows the expert's permitted system apps as desktop icons", () => {
    render(<DesktopGrid apps={[]} />);

    const keys = screen
      .getAllByTestId("desktop-system-icon")
      .map((node) => node.getAttribute("data-system-key"));
    expect(keys).toEqual([
      "usage",
      "connections",
      "capabilities",
      "operations",
      "tasks",
    ]);
  });

  it("opens a system app in its own window", async () => {
    render(<DesktopGrid apps={[]} />);

    const usage = screen
      .getAllByTestId("desktop-system-icon")
      .find((node) => node.getAttribute("data-system-key") === "usage")!;
    fireEvent.doubleClick(usage);

    const frame = await screen.findByTestId("system-app-frame");
    expect(frame).toHaveAttribute("data-system-key", "usage");
    expect(frame).toHaveAttribute("data-agent-id", "agent_1");
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-kind",
      "system",
    );
  });

  it("restores a system window from localStorage", async () => {
    localStorage.setItem(
      "octop:apps-desktop-windows:v1",
      JSON.stringify([
        {
          id: "system:agent_1:usage",
          kind: "system",
          systemKey: "usage",
          agentId: "agent_1",
          spaceId: "agent_1",
          title: "Usage & budget",
          x: 40,
          y: 80,
          w: 720,
          h: 560,
          z: 3,
          minimized: false,
          maximized: false,
        },
      ]),
    );

    render(<DesktopGrid apps={[]} />);

    const frame = await screen.findByTestId("system-app-frame");
    expect(frame).toHaveAttribute("data-system-key", "usage");
  });
});
