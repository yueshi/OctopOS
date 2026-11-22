import { render, screen } from "@testing-library/react";
import { App as AntdApp } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { appsApi, type AppTask } from "../../../api/modules/apps";
import AppTasksPanel from "./AppTasksPanel";

vi.mock("react-i18next", async () => {
  const zh = (await import("../../../locales/zh.json")).default;
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
    if (options && typeof options === "object") {
      return value.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
        String((options as Record<string, unknown>)[name] ?? ""),
      );
    }
    return value;
  };
  return { useTranslation: () => ({ t, i18n: { language: "zh" } }) };
});

vi.mock("../../../api/modules/apps", () => ({
  appsApi: { listTasks: vi.fn(), cancelTask: vi.fn() },
}));

vi.mock("../../../utils/antdMessage", () => ({
  message: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

const task: AppTask = {
  task_id: "tsk_1",
  app_id: "app_1",
  table: "main",
  row_id: "row_1",
  task_type: "summarize_row",
  status: "queued",
  result: { summary: "一条摘要" },
  error: "",
  created_at: 1_700_000_000,
  started_at: null,
  finished_at: null,
};

function renderPanel() {
  return render(
    <AntdApp>
      <AppTasksPanel appId="app_1" canCancel />
    </AntdApp>,
  );
}

beforeEach(() => {
  vi.mocked(appsApi.listTasks).mockReset();
  vi.mocked(appsApi.cancelTask).mockReset();
});

describe("AppTasksPanel", () => {
  it("renders queued tasks with their result", async () => {
    vi.mocked(appsApi.listTasks).mockResolvedValue([task]);
    renderPanel();

    expect(await screen.findByText("摘要单行")).toBeInTheDocument();
    expect(screen.getByText("排队中")).toBeInTheDocument();
    expect(screen.getByText("一条摘要")).toBeInTheDocument();
    expect(appsApi.listTasks).toHaveBeenCalledWith("app_1");
  });

  it("shows the empty state when there are no tasks", async () => {
    vi.mocked(appsApi.listTasks).mockResolvedValue([]);
    renderPanel();

    expect(await screen.findByText("还没有 AI 任务")).toBeInTheDocument();
  });

  it("shows a load error instead of a stale table", async () => {
    vi.mocked(appsApi.listTasks).mockRejectedValue(new Error("boom"));
    renderPanel();

    expect(await screen.findByText("无法加载 AI 任务")).toBeInTheDocument();
  });
});
