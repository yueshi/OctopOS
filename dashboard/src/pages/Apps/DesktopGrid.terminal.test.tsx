import type { ReactElement } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DesktopGrid from "./DesktopGrid";

function renderGrid(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

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
  value: {
    id: 1,
    username: "owner",
    role: "user",
    permissions: ["terminal"],
  } as { id: number; username: string; role: string; permissions: string[] },
}));

vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => currentUser.value,
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
        is_shared: false,
        kind: "expert",
      },
    ],
    activeAgentId: "agent_1",
    loading: false,
    setActiveAgent: vi.fn(),
  }),
  selectEnabledExperts: (agents: unknown[]) => agents,
}));

vi.mock("../Control/Terminal", () => ({
  default: ({
    agentId,
    isVisible,
  }: {
    agentId?: string;
    isVisible?: boolean;
  }) => (
    <div
      data-testid="desktop-terminal"
      data-agent-id={agentId ?? ""}
      data-visible={isVisible ? "true" : "false"}
    />
  ),
}));

vi.mock("../../components/MiniAppFrame", () => ({
  default: () => <div data-testid="mini-app-frame" />,
}));

vi.mock("./DesktopChatWindow", () => ({
  default: ({ agentId }: { agentId: string }) => (
    <div data-testid="desktop-chat" data-agent-id={agentId} />
  ),
}));

function openTerminalFromDock() {
  fireEvent.click(screen.getByTestId("desktop-dock-terminal"));
}

describe("DesktopGrid terminal", () => {
  beforeEach(() => {
    localStorage.clear();
    currentUser.value = {
      id: 1,
      username: "owner",
      role: "user",
      permissions: ["terminal"],
    };
  });

  it("opens the pinned terminal window from the dock", async () => {
    renderGrid(<DesktopGrid apps={[]} />);

    openTerminalFromDock();

    expect(await screen.findByTestId("desktop-terminal")).toHaveAttribute(
      "data-agent-id",
      "agent_1",
    );
    const win = screen
      .getAllByTestId("desktop-window")
      .find((node) => node.getAttribute("data-kind") === "terminal");
    expect(win).toHaveAttribute("data-agent-id", "agent_1");
  });

  it("reopens the same terminal window instead of stacking duplicates", async () => {
    renderGrid(<DesktopGrid apps={[]} />);

    openTerminalFromDock();
    openTerminalFromDock();

    expect(await screen.findByTestId("desktop-terminal")).toBeInTheDocument();
    expect(
      screen
        .getAllByTestId("desktop-window")
        .filter((node) => node.getAttribute("data-kind") === "terminal"),
    ).toHaveLength(1);
  });

  it("hides the launcher when the user lacks the terminal permission", () => {
    currentUser.value = {
      id: 1,
      username: "owner",
      role: "user",
      permissions: [],
    };
    renderGrid(<DesktopGrid apps={[]} />);

    expect(
      screen.queryByTestId("desktop-dock-terminal"),
    ).not.toBeInTheDocument();
  });
});
