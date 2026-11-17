import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { ensureBuiltinToolRenderers } from "../../../plugins/toolRenderers";
import { setPluginUiDockHandlers } from "../../../plugins/toolRenderers/host";
import { ToolHostScopeProvider } from "../../../plugins/toolRenderers/ToolHostScope";
import { ToolDetailsInline } from "./MessageBubble";

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
  useLocation: () => ({ pathname: "/apps" }),
}));

const appCard = {
  name: "app_open",
  callId: "call_app",
  output: JSON.stringify({
    octop_ui: { renderer: "app_card", version: 1 },
    data: {
      app_id: "app_1",
      name: "客户跟进台账",
      row_count: 3,
      has_html: true,
    },
  }),
};

beforeAll(() => {
  ensureBuiltinToolRenderers();
});

describe("ToolDetailsInline host scope", () => {
  it("routes AppCard's sidebar action to the scoped desktop handler", () => {
    const scoped = {
      openSidePanel: vi.fn(),
      closeSidePanel: vi.fn(),
      openGamePanel: vi.fn(),
    };
    const globalOpen = vi.fn();
    setPluginUiDockHandlers({ openSidePanel: globalOpen });

    render(
      <ToolHostScopeProvider overrides={scoped}>
        <ToolDetailsInline toolData={appCard} isStreaming={false} />
      </ToolHostScopeProvider>,
    );

    fireEvent.click(screen.getByText("Open in sidebar"));

    expect(scoped.openSidePanel).toHaveBeenCalledWith({
      callId: "call_app",
      title: "客户跟进台账",
      toolName: "app_open",
    });
    expect(globalOpen).not.toHaveBeenCalled();
    setPluginUiDockHandlers({});
  });
});
