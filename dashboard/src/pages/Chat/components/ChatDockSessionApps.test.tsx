import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { AppDetail } from "../../../api/modules/apps";
import ChatDockSessionApps from "./ChatDockSessionApps";

vi.mock("../../../components/MiniAppFrame", () => ({
  default: ({
    appId,
    reloadToken = 0,
  }: {
    appId: string;
    reloadToken?: number;
  }) => (
    <div
      data-testid="mini-app-frame"
      data-app-id={appId}
      data-reload-token={reloadToken}
    />
  ),
}));

vi.mock("./SessionAppInfoDrawer", () => ({
  default: ({
    open,
    appId,
    onUpdated,
  }: {
    open: boolean;
    appId: string | null;
    onUpdated?: (detail: AppDetail) => void;
  }) =>
    open ? (
      <div data-testid="app-info-drawer" data-app-id={appId}>
        <button
          type="button"
          onClick={() =>
            onUpdated?.({
              app_id: appId,
              name: "新名字",
              description: "新描述",
              capabilities: ["data.read"],
            } as unknown as AppDetail)
          }
        >
          mock-save
        </button>
      </div>
    ) : null,
}));

vi.mock("../../../hooks/usePreviewImage", () => ({
  usePreviewImage: () => null,
}));

const apps = [
  {
    app_id: "app_a",
    name: "客户台账",
    row_count: 12,
    has_preview: true,
  },
  {
    app_id: "app_b",
    name: "业绩看板",
    description: "按门店汇总",
  },
];

function renderDock(dockApps: typeof apps) {
  return render(
    <MemoryRouter>
      <ChatDockSessionApps apps={dockApps} />
    </MemoryRouter>,
  );
}

describe("ChatDockSessionApps", () => {
  it("explains when the conversation has no MiniApp", () => {
    renderDock([]);
    expect(
      screen.getByText("No MiniApp in this conversation yet."),
    ).toBeInTheDocument();
  });

  it("lists the session MiniApps", () => {
    renderDock(apps);
    expect(screen.getByText("客户台账")).toBeInTheDocument();
    expect(screen.getByText("业绩看板")).toBeInTheDocument();
    expect(screen.queryByTestId("mini-app-frame")).not.toBeInTheDocument();
  });

  it("runs the selected MiniApp inside the panel and goes back", () => {
    renderDock(apps);
    fireEvent.click(screen.getByText("客户台账"));

    expect(screen.getByTestId("mini-app-frame")).toHaveAttribute(
      "data-app-id",
      "app_a",
    );

    fireEvent.click(screen.getByRole("button", { name: "Back to list" }));
    expect(screen.queryByTestId("mini-app-frame")).not.toBeInTheDocument();
    expect(screen.getByText("业绩看板")).toBeInTheDocument();
  });

  it("notifies the host before opening a MiniApp", () => {
    const onSelectApp = vi.fn();
    render(
      <MemoryRouter>
        <ChatDockSessionApps apps={apps} onSelectApp={onSelectApp} />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByText("业绩看板"));

    expect(onSelectApp).toHaveBeenCalledWith(apps[1]);
    expect(screen.getByTestId("mini-app-frame")).toHaveAttribute(
      "data-app-id",
      "app_b",
    );
  });

  it("opens the info drawer for the running MiniApp", () => {
    renderDock(apps);
    fireEvent.click(screen.getByText("客户台账"));

    expect(screen.queryByTestId("app-info-drawer")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Info" }));

    expect(screen.getByTestId("app-info-drawer")).toHaveAttribute(
      "data-app-id",
      "app_a",
    );
  });

  it("reflects name changes made in the info drawer", () => {
    renderDock(apps);
    fireEvent.click(screen.getByText("客户台账"));
    fireEvent.click(screen.getByRole("button", { name: "Info" }));
    fireEvent.click(screen.getByRole("button", { name: "mock-save" }));

    expect(screen.getAllByText("新名字").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Back to list" }));
    expect(screen.getByText("新名字")).toBeInTheDocument();
  });

  it("reloads the running MiniApp on demand", () => {
    renderDock(apps);
    fireEvent.click(screen.getByText("客户台账"));

    const frame = screen.getByTestId("mini-app-frame");
    expect(frame).toHaveAttribute("data-reload-token", "0");

    fireEvent.click(screen.getByRole("button", { name: "Reload MiniApp" }));
    expect(frame).toHaveAttribute("data-reload-token", "1");
  });
});
