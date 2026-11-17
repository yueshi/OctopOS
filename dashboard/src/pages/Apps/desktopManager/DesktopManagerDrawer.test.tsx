import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DesktopManagerDrawer from "./DesktopManagerDrawer";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) =>
      typeof fallback === "string" ? fallback : key,
    i18n: { language: "en" },
  }),
}));

vi.mock("../../Agent/Skills/components/SkillsTabs", () => ({
  default: ({ agentId }: { agentId: string }) => (
    <div data-testid="manager-skills" data-agent-id={agentId} />
  ),
}));

vi.mock("../../Agent/Tools/ToolsTabs", () => ({
  default: ({ agentId }: { agentId: string }) => (
    <div data-testid="manager-tools" data-agent-id={agentId} />
  ),
}));

vi.mock("../../Agent/Channels/ChannelsPanel", () => ({
  default: ({ agentId }: { agentId: string }) => (
    <div data-testid="manager-channels" data-agent-id={agentId} />
  ),
}));

vi.mock("./DesktopConnectorsPanel", () => ({
  default: () => <div data-testid="manager-connectors" />,
}));

vi.mock("./DesktopTasksPanel", () => ({
  default: () => <div data-testid="manager-tasks" />,
}));

describe("DesktopManagerDrawer", () => {
  it("renders the requested native panel with the target expert", () => {
    const { rerender } = render(
      <DesktopManagerDrawer
        open
        agentId="agent_1"
        agentName="Sales"
        managerKey="skills"
        onClose={() => undefined}
      />,
    );

    expect(screen.getByTestId("desktop-manager-drawer")).toHaveAttribute(
      "data-manager-key",
      "skills",
    );
    expect(screen.getByTestId("manager-skills")).toHaveAttribute(
      "data-agent-id",
      "agent_1",
    );

    rerender(
      <DesktopManagerDrawer
        open
        agentId="agent_1"
        agentName="Sales"
        managerKey="connectors"
        onClose={() => undefined}
      />,
    );
    expect(screen.getByTestId("manager-connectors")).toBeInTheDocument();
    expect(screen.queryByTestId("manager-skills")).toBeNull();
  });
});
