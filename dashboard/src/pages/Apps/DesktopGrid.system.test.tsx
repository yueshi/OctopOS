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
    onOpenManager,
  }: {
    appKey: string;
    agentId: string;
    onOpenManager?: (key: string) => void;
  }) => (
    <div
      data-testid="system-app-frame"
      data-system-key={appKey}
      data-agent-id={agentId}
    >
      <button
        type="button"
        data-testid="system-open-manager"
        onClick={() => onOpenManager?.("skills")}
      />
    </div>
  ),
}));

vi.mock("./desktopManager/DesktopManagerDrawer", () => ({
  default: ({
    managerKey,
    agentId,
  }: {
    managerKey: string;
    agentId: string;
  }) => (
    <div
      data-testid="desktop-manager"
      data-manager-key={managerKey}
      data-agent-id={agentId}
    />
  ),
}));

describe("DesktopGrid system apps", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("groups the expert's system apps in one desktop folder", () => {
    renderGrid(<DesktopGrid apps={[]} />);

    expect(screen.getByTestId("desktop-system-folder")).toBeInTheDocument();
    expect(screen.queryAllByTestId("desktop-system-icon")).toHaveLength(0);

    fireEvent.click(screen.getByTestId("desktop-system-folder"));
    const keys = screen
      .getAllByTestId("desktop-system-folder-item")
      .map((node) => node.getAttribute("data-system-key"));
    expect(keys).toEqual([
      "usage",
      "connections",
      "capabilities",
      "operations",
      "tasks",
    ]);
  });

  it("opens a system app from the folder in its own window", async () => {
    renderGrid(<DesktopGrid apps={[]} />);
    fireEvent.click(screen.getByTestId("desktop-system-folder"));

    const usage = screen
      .getAllByTestId("desktop-system-folder-item")
      .find((node) => node.getAttribute("data-system-key") === "usage")!;
    fireEvent.click(usage);

    const frame = await screen.findByTestId("system-app-frame");
    expect(frame).toHaveAttribute("data-system-key", "usage");
    expect(frame).toHaveAttribute("data-agent-id", "agent_1");
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-kind",
      "system",
    );
  });

  it("opens a native desktop manager instead of navigating away", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderGrid(<DesktopGrid apps={[]} />);
    fireEvent.click(screen.getByTestId("desktop-system-folder"));
    const capabilities = screen
      .getAllByTestId("desktop-system-folder-item")
      .find((node) => node.getAttribute("data-system-key") === "capabilities")!;
    fireEvent.click(capabilities);
    fireEvent.click(await screen.findByTestId("system-open-manager"));

    const manager = await screen.findByTestId("desktop-manager");
    expect(manager).toHaveAttribute("data-manager-key", "skills");
    expect(manager).toHaveAttribute("data-agent-id", "agent_1");
    expect(open).not.toHaveBeenCalled();
    open.mockRestore();
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

    renderGrid(<DesktopGrid apps={[]} />);

    const frame = await screen.findByTestId("system-app-frame");
    expect(frame).toHaveAttribute("data-system-key", "usage");
  });
});
