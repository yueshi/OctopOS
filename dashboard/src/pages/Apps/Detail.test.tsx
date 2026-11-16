import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppDetail } from "../../api/modules/apps";
import AppDetailPage from "./Detail";

const mobile = vi.hoisted(() => ({ value: false }));
const currentUser = vi.hoisted(() => ({
  value: { username: "alice", role: "user" } as {
    username: string;
    role: string;
  } | null,
}));
const api = vi.hoisted(() => ({
  get: vi.fn(),
  files: vi.fn(),
  listSources: vi.fn(),
  listBindings: vi.fn(),
  listServices: vi.fn(),
  sourceTools: vi.fn(),
  accessRequests: vi.fn(),
  dataVersion: vi.fn(),
  publication: vi.fn(),
  listSchedules: vi.fn(),
  saveSchedule: vi.fn(),
  updateSchedule: vi.fn(),
  deleteSchedule: vi.fn(),
}));

vi.mock("../../hooks/useIsMobile", () => ({
  useIsMobile: () => mobile.value,
}));

vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => currentUser.value,
}));

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("react-i18next", async () => {
  const zh = (await import("../../locales/zh.json")).default;
  const lookup = (key: string): unknown => {
    let value: unknown = zh;
    for (const part of key.split(".")) {
      value = (value as Record<string, unknown> | undefined)?.[part];
    }
    return value;
  };
  const t = (key: string, options?: unknown): string => {
    const value = lookup(key);
    if (typeof value !== "string") {
      return typeof options === "string" ? options : key;
    }
    if (options && typeof options === "object" && !Array.isArray(options)) {
      return value.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
        String((options as Record<string, unknown>)[name] ?? ""),
      );
    }
    return value;
  };
  return { useTranslation: () => ({ t, i18n: { language: "zh" } }) };
});

vi.mock("../../api/modules/apps", () => ({
  appsApi: {
    get: api.get,
    files: api.files,
    listSources: api.listSources,
    listBindings: api.listBindings,
    listServices: api.listServices,
    sourceTools: api.sourceTools,
    accessRequests: api.accessRequests,
    dataVersion: api.dataVersion,
    publication: api.publication,
    listSchedules: api.listSchedules,
    saveSchedule: api.saveSchedule,
    updateSchedule: api.updateSchedule,
    deleteSchedule: api.deleteSchedule,
  },
  askAppAssistant: vi.fn(),
}));

vi.mock("../../api/modules/connectors", () => ({
  connectorsApi: { listInstances: vi.fn(async () => []) },
}));

const detail: AppDetail = {
  app_id: "app_1",
  name: "客户跟进台账",
  description: "",
  agent_id: "agent_1",
  agent_name: "销售助理",
  owner_user_id: 1,
  owner_username: "alice",
  permission: "edit_structure",
  row_count: 0,
  html_bytes: 0,
  data_bytes: 0,
  has_html: false,
  has_preview: false,
  capabilities: [],
  previous_html_at: null,
  created_at: 0,
  updated_at: 0,
  columns: [],
  tables: [
    {
      table_key: "main",
      label: "客户表",
      row_count: 0,
      is_main: true,
      public_visible: false,
      columns: [],
    },
  ],
};

function renderPage() {
  return render(
    <AntdApp>
      <MemoryRouter initialEntries={["/apps/app_1"]}>
        <Routes>
          <Route path="/apps/:appId" element={<AppDetailPage />} />
        </Routes>
      </MemoryRouter>
    </AntdApp>,
  );
}

beforeEach(() => {
  mobile.value = false;
  currentUser.value = { username: "alice", role: "user" };
  api.get.mockReset();
  api.get.mockResolvedValue(detail);
  api.files.mockReset();
  api.files.mockResolvedValue({ files: [] });
  api.listSources.mockReset();
  api.listSources.mockResolvedValue({ sources: [] });
  api.listBindings.mockReset();
  api.listBindings.mockResolvedValue({ bindings: [] });
  api.listServices.mockReset();
  api.listServices.mockResolvedValue({ services: [] });
  api.sourceTools.mockReset();
  api.sourceTools.mockResolvedValue({ tools: [] });
  api.accessRequests.mockReset();
  api.accessRequests.mockResolvedValue({ requests: [] });
  api.dataVersion.mockReset();
  api.dataVersion.mockResolvedValue({ tables: [] });
  api.publication.mockReset();
  api.publication.mockResolvedValue({ active: false });
  api.listSchedules.mockReset();
  api.listSchedules.mockResolvedValue({ schedules: [] });
  api.saveSchedule.mockReset();
  api.updateSchedule.mockReset();
  api.deleteSchedule.mockReset();
});

describe("app detail header actions", () => {
  it("groups owner actions under share/publish and more menus", async () => {
    renderPage();

    expect(
      await screen.findByRole("button", { name: "分享与发布" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI 助手" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "信息" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更多" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "返回小程序" }),
    ).toBeInTheDocument();
    // The owner does not need a permission badge.
    expect(screen.queryByText("所有者")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "分享与发布" }));
    expect(await screen.findByText("公开链接")).toBeInTheDocument();
    expect(
      screen.getByText("分享", {
        selector: ".ant-dropdown-menu-title-content",
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    expect(await screen.findByText("重命名")).toBeInTheDocument();
    expect(screen.getByText("删除小程序")).toBeInTheDocument();
    // No previous HTML yet, so restore is not offered.
    expect(screen.queryByText("恢复上一版")).not.toBeInTheDocument();
  });

  it("hides owner-only actions from an edit_structure collaborator", async () => {
    currentUser.value = { username: "bob", role: "user" };
    renderPage();

    expect(
      await screen.findByRole("button", { name: "更多" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "分享与发布" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    expect(await screen.findByText("重命名")).toBeInTheDocument();
    expect(screen.queryByText("删除小程序")).not.toBeInTheDocument();
  });

  it("lets an admin manage an app they do not own", async () => {
    currentUser.value = { username: "root", role: "admin" };
    renderPage();

    expect(
      await screen.findByRole("button", { name: "分享与发布" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    expect(await screen.findByText("删除小程序")).toBeInTheDocument();
  });

  it("offers restore only when a previous HTML version exists", async () => {
    api.get.mockResolvedValue({ ...detail, previous_html_at: 123 });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "更多" }));

    expect(await screen.findByText("恢复上一版")).toBeInTheDocument();
  });

  it("confirms destructive actions from the more menu", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "更多" }));
    await user.click(
      await screen.findByRole("menuitem", { name: /删除小程序/ }),
    );

    expect(
      await screen.findByText("删除这个小程序？", {
        selector: ".ant-modal-confirm-title",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/将删除小程序、它的界面和全部数据/, {
        selector: ".ant-modal-confirm-content",
      }),
    ).toBeInTheDocument();
  });

  it("shows a read-only badge and hides edit menus for viewers", async () => {
    currentUser.value = { username: "bob", role: "user" };
    api.get.mockResolvedValue({ ...detail, permission: "view" });

    renderPage();

    expect(await screen.findByText("仅查看")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI 助手" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "信息" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "分享与发布" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "更多" }),
    ).not.toBeInTheDocument();
  });

  it("labels a non-owner editor as an administrator", async () => {
    currentUser.value = { username: "bob", role: "admin" };

    renderPage();

    expect(await screen.findByText("管理员")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "分享与发布" }),
    ).toBeInTheDocument();
  });

  it("opens the details drawer with separated sections", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "信息" }));

    expect(await screen.findByText("小程序信息")).toBeInTheDocument();
    expect(screen.getByText("页面能力")).toBeInTheDocument();
    expect(screen.getByText("数据表")).toBeInTheDocument();
    expect(screen.getByText("应用文件")).toBeInTheDocument();
    // The table card keeps its actions reachable below the table name.
    expect(screen.getByText("客户表")).toBeInTheDocument();
    expect(screen.getByText("主表")).toBeInTheDocument();
    expect(screen.getByText("公开链接")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /导入/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /数据源/ })).toBeInTheDocument();
  });

  it("lists declared external services", async () => {
    api.listServices.mockResolvedValue({
      services: [
        {
          service_id: "svc_1",
          name: "douban",
          provider: "external",
          connector_instance_id: "connector_1",
          allowed_tools: ["fetch_json"],
          allowed_url_prefixes: ["https://api.example.com"],
          cache_ttl_seconds: 300,
          max_calls_per_minute: 30,
          public_live: false,
          enabled: true,
          created_at: 0,
          updated_at: 0,
        },
      ],
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "信息" }));

    expect(await screen.findByText("douban")).toBeInTheDocument();
    expect(screen.getByText("fetch_json")).toBeInTheDocument();
    expect(screen.getByText("TTL 300 秒 · 30 次/分")).toBeInTheDocument();
  });

  it("shows per-user tables as never public", async () => {
    api.get.mockResolvedValue({
      ...detail,
      tables: [
        ...detail.tables,
        {
          table_key: "notes",
          label: "个人笔记",
          row_count: 2,
          is_main: false,
          public_visible: true,
          row_scope: "user",
          columns: [],
        },
      ],
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "信息" }));
    expect(await screen.findByText("个人笔记")).toBeInTheDocument();
    expect(screen.getByText("个人表")).toBeInTheDocument();
    const switches = screen.getAllByRole("switch");
    expect(switches[0]).not.toBeDisabled();
    expect(switches[1]).not.toBeChecked();
    expect(switches[1]).toBeDisabled();
  });

  it("keeps icon-only controls on mobile", async () => {
    mobile.value = true;

    renderPage();

    expect(
      await screen.findByRole("button", { name: "AI 助手" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "分享与发布" }),
    ).toBeInTheDocument();
    // Labels collapse to icons; the accessible name still carries them.
    await waitFor(() =>
      expect(screen.queryByText("AI 助手")).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("分享与发布")).not.toBeInTheDocument();
  });
});
