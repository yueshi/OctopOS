import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppSummary } from "../../api/modules/apps";
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
  useCurrentUser: () => ({ id: 2, username: "Test1", role: "user" }),
}));

vi.mock("../../context/AgentContext", () => ({
  useAgent: () => ({
    agents: [
      {
        agent_id: "agent_empty",
        name: "空桌面专家",
        state: "running",
        color: "#22c55e",
        icon_name: "sparkles",
        icon_url: null,
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

describe("DesktopGrid expert workspaces", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("gives an enabled expert an empty desktop that opens chat", () => {
    render(<DesktopGrid apps={[]} />);

    expect(screen.getByTestId("desktop-empty-space")).toBeInTheDocument();
    expect(screen.getAllByText("空桌面专家").length).toBeGreaterThan(0);

    fireEvent.click(
      within(screen.getByTestId("desktop-empty-space")).getByRole("button"),
    );
    expect(screen.getByTestId("desktop-chat")).toHaveAttribute(
      "data-agent-id",
      "agent_empty",
    );
    expect(screen.getByTestId("desktop-pet")).toHaveAttribute(
      "data-pet-state",
      "working",
    );

    fireEvent.click(screen.getByTestId("desktop-space-trigger"));
    expect(screen.getAllByTestId("desktop-space-option")).toHaveLength(2);
    expect(screen.getByText("All Apps")).toBeInTheDocument();
  });

  it("gathers MiniApps shared from an inaccessible expert into Shared with me", () => {
    const sharedApp: AppSummary = {
      app_id: "app_shared",
      name: "共享小程序",
      description: "",
      agent_id: "agent_admin",
      agent_name: "老A",
      owner_user_id: 1,
      owner_username: "admin",
      permission: "view",
      row_count: 0,
      html_bytes: 0,
      data_bytes: 0,
      has_html: false,
      has_preview: false,
      created_at: 0,
      updated_at: 0,
    };
    const ownApp: AppSummary = {
      ...sharedApp,
      app_id: "app_own",
      name: "我的小程序",
      agent_id: "agent_empty",
      agent_name: "空桌面专家",
      owner_user_id: 2,
      owner_username: "Test1",
      permission: "edit_structure",
    };

    render(<DesktopGrid apps={[sharedApp, ownApp]} />);

    fireEvent.click(screen.getByTestId("desktop-space-trigger"));
    const options = screen.getAllByTestId("desktop-space-option");
    // The viewer's own expert desktop, the shared bucket, and All Apps.
    expect(options).toHaveLength(3);
    expect(screen.getByText("Shared with me")).toBeInTheDocument();
    expect(screen.queryByText("老A")).not.toBeInTheDocument();

    const sharedOption = options.find(
      (node) => node.getAttribute("data-space-id") === "__shared__",
    )!;
    fireEvent.click(sharedOption);
    expect(screen.getByText("共享小程序")).toBeInTheDocument();
  });
});
