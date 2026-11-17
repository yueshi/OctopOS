import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import type { AppGameSession } from "../../api/modules/apps";
import type { ChatMessage } from "../Chat/hooks/useChat";
import DesktopChatToolPanel from "./DesktopChatToolPanel";

vi.mock("../../components/MiniAppFrame", () => ({
  default: (props: {
    appId: string;
    onComposeMessage?: (text: string) => void;
    onOpenExternal?: (url: string) => void;
  }) => (
    <div data-testid="miniapp-frame" data-app-id={props.appId}>
      <button
        type="button"
        data-testid="miniapp-compose"
        onClick={() => props.onComposeMessage?.("帮我看看")}
      />
      <button
        type="button"
        data-testid="miniapp-external"
        onClick={() => props.onOpenExternal?.("https://example.com")}
      />
    </div>
  ),
}));

vi.mock("../../components/MiniAppFrame/GameFrame", () => ({
  default: (props: { appId: string; session: AppGameSession }) => (
    <div data-testid="game-frame" data-app-id={props.appId}>
      {props.session.name}
    </div>
  ),
}));

const appCardMessage: ChatMessage = {
  id: "m1",
  role: "tool",
  content: "",
  status: "done",
  timestamp: 0,
  toolData: {
    name: "app_open",
    callId: "call_1",
    output: JSON.stringify({
      octop_ui: { renderer: "app_card", version: 1 },
      data: {
        app_id: "app_1",
        name: "客户跟进台账",
        row_count: 3,
        has_html: true,
        capabilities: ["compose_message"],
      },
    }),
  },
};

function renderPanel(
  overrides: Partial<ComponentProps<typeof DesktopChatToolPanel>>,
) {
  return render(
    <DesktopChatToolPanel
      state={{ kind: "tool", callId: "call_1", title: "客户跟进台账" }}
      messages={[appCardMessage]}
      agentId="agent_1"
      isStreaming={false}
      active
      onClose={vi.fn()}
      onComposeMessage={vi.fn()}
      onGameSession={vi.fn()}
      {...overrides}
    />,
  );
}

describe("DesktopChatToolPanel", () => {
  it("renders app cards in a MiniAppFrame and keeps compose inside the window", () => {
    const onComposeMessage = vi.fn();
    const onOpenExternal = vi.fn();
    renderPanel({ onComposeMessage, onOpenExternal });

    expect(screen.getByTestId("miniapp-frame")).toHaveAttribute(
      "data-app-id",
      "app_1",
    );
    expect(screen.getByText("客户跟进台账")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("miniapp-compose"));
    expect(onComposeMessage).toHaveBeenCalledWith("帮我看看");

    fireEvent.click(screen.getByTestId("miniapp-external"));
    expect(onOpenExternal).toHaveBeenCalledWith("https://example.com");
  });

  it("shows a fallback when the tool call left the conversation", () => {
    renderPanel({ messages: [] });

    expect(screen.getByText(/no longer available/)).toBeInTheDocument();
  });

  it("renders a launched game in the same in-window panel", () => {
    const session: AppGameSession = {
      session_id: "s1",
      game_id: "g1",
      name: "大转盘",
      url: "https://games.example.com/play",
      token: "t",
      expires_at: 0,
      sandbox: "allow-scripts",
    };
    renderPanel({
      state: { kind: "game", appId: "app_1", session },
    });

    expect(screen.getByTestId("game-frame")).toHaveTextContent("大转盘");
  });

  it("closes from the panel header", () => {
    const onClose = vi.fn();
    renderPanel({ onClose });

    fireEvent.click(screen.getByTestId("desktop-chat-tool-panel-close"));

    expect(onClose).toHaveBeenCalled();
  });
});
