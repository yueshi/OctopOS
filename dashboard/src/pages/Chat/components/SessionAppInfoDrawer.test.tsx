import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppDetail } from "../../../api/modules/apps";
import SessionAppInfoDrawer from "./SessionAppInfoDrawer";

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
  setCapabilities: vi.fn(),
  setTableVisibility: vi.fn(),
}));

const currentUserMock = vi.hoisted(() => vi.fn());

const messageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}));

vi.mock("../../../api/modules/apps", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../api/modules/apps")
  >();
  return { ...actual, appsApi: apiMocks };
});

vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: currentUserMock,
}));

vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("../../../utils/antdMessage", () => ({ message: messageMock }));

const detail: AppDetail = {
  app_id: "app_a",
  name: "客户台账",
  description: "按门店汇总",
  agent_id: "agent_1",
  agent_name: "销售助手",
  agent_owner_user_id: 1,
  owner_user_id: 1,
  owner_username: "alice",
  permission: "edit_structure",
  row_count: 12,
  html_bytes: 1024,
  data_bytes: 2048,
  has_html: true,
  has_preview: true,
  capabilities: ["data.read", "data.write"],
  previous_html_at: null,
  created_at: 1700000000,
  updated_at: 1700000100,
  columns: [],
  tables: [
    {
      table_key: "main",
      label: "主表",
      row_count: 12,
      data_version: 3,
      is_main: true,
      public_visible: true,
      member_visible: true,
      row_scope: "app",
      rules: {},
      columns: [],
    },
    {
      table_key: "stores",
      label: "门店表",
      row_count: 3,
      data_version: 1,
      is_main: false,
      public_visible: false,
      member_visible: true,
      row_scope: "user",
      rules: {},
      columns: [],
    },
  ],
};

function renderDrawer(
  props: Partial<Parameters<typeof SessionAppInfoDrawer>[0]>,
) {
  return render(
    <MemoryRouter>
      <SessionAppInfoDrawer open appId="app_a" onClose={() => {}} {...props} />
    </MemoryRouter>,
  );
}

describe("SessionAppInfoDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentUserMock.mockReturnValue({
      id: 1,
      username: "alice",
      role: "admin",
      display_name: "Alice",
      locale: "zh",
    });
    apiMocks.get.mockResolvedValue(detail);
  });

  it("shows the MiniApp metadata", async () => {
    renderDrawer({});

    expect(await screen.findByDisplayValue("客户台账")).toBeInTheDocument();
    expect(screen.getByText("销售助手")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("Owner")).toBeInTheDocument();
    expect(screen.getByText("主表")).toBeInTheDocument();
    expect(screen.getByText("门店表")).toBeInTheDocument();
  });

  it("saves an edited name and description", async () => {
    const onUpdated = vi.fn();
    apiMocks.update.mockResolvedValue({
      ...detail,
      name: "新台账",
      description: "按区域汇总",
    });
    renderDrawer({ onUpdated });

    fireEvent.change(await screen.findByDisplayValue("客户台账"), {
      target: { value: "新台账" },
    });
    fireEvent.change(screen.getByDisplayValue("按门店汇总"), {
      target: { value: "按区域汇总" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(apiMocks.update).toHaveBeenCalledWith("app_a", {
        name: "新台账",
        description: "按区域汇总",
      }),
    );
    expect(onUpdated).toHaveBeenCalledWith(
      expect.objectContaining({ app_id: "app_a", name: "新台账" }),
    );
  });

  it("saves page capabilities for the owner", async () => {
    apiMocks.setCapabilities.mockResolvedValue({
      ...detail,
      capabilities: ["data.read", "data.write", "ai.agent_task"],
    });
    renderDrawer({});

    await screen.findByDisplayValue("客户台账");
    fireEvent.click(screen.getByRole("checkbox", { name: "Run AI tasks" }));
    fireEvent.click(screen.getByRole("button", { name: "Save capabilities" }));

    await waitFor(() =>
      expect(apiMocks.setCapabilities).toHaveBeenCalledWith(
        "app_a",
        expect.arrayContaining(["data.read", "data.write", "ai.agent_task"]),
      ),
    );
  });

  it("toggles public visibility for app-scoped tables", async () => {
    const mainTable = detail.tables[0];
    apiMocks.setTableVisibility.mockResolvedValue({
      ...mainTable,
      public_visible: false,
    });
    renderDrawer({});

    await screen.findByDisplayValue("客户台账");
    const switches = screen.getAllByRole("switch");
    expect(switches[1]).toBeDisabled();
    fireEvent.click(switches[0]);

    await waitFor(() =>
      expect(apiMocks.setTableVisibility).toHaveBeenCalledWith(
        "app_a",
        "main",
        false,
      ),
    );
  });

  it("mounts inside the fullscreen desktop root so it stays on the desktop", async () => {
    const desktopRoot = document.createElement("div");
    document.body.appendChild(desktopRoot);
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      get: () => desktopRoot,
    });
    try {
      renderDrawer({});

      await screen.findByDisplayValue("客户台账");
      expect(desktopRoot.querySelector(".ant-drawer")).not.toBeNull();
    } finally {
      Reflect.deleteProperty(document, "fullscreenElement");
      desktopRoot.remove();
    }
  });

  it("keeps everything read-only without edit_structure", async () => {
    currentUserMock.mockReturnValue({
      id: 2,
      username: "bob",
      role: "user",
      display_name: "Bob",
      locale: "zh",
    });
    apiMocks.get.mockResolvedValue({
      ...detail,
      permission: "view",
      owner_username: "alice",
    });
    renderDrawer({});

    expect(await screen.findByText("客户台账")).toBeInTheDocument();
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Save capabilities" }),
    ).toBeNull();
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
  });
});
