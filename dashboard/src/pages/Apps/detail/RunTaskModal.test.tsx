import { render, screen, waitFor } from "@testing-library/react";
import { App as AntdApp } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { appsApi, type AppColumn } from "../../../api/modules/apps";
import RunTaskModal from "./RunTaskModal";

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
  appsApi: { listTaskTypes: vi.fn(), createTask: vi.fn() },
}));

vi.mock("../../../utils/antdMessage", () => ({
  message: { success: vi.fn(), error: vi.fn() },
}));

const columns: AppColumn[] = [
  {
    name: "title",
    label: "标题",
    type: "string",
    required: true,
    values: [],
    pii: null,
  },
  {
    name: "phone",
    label: "手机号",
    type: "string",
    required: false,
    values: [],
    pii: "phone",
  },
];

beforeEach(() => {
  vi.mocked(appsApi.listTaskTypes).mockReset();
  vi.mocked(appsApi.createTask).mockReset();
});

describe("RunTaskModal", () => {
  it("queues the default task type for the selected row", async () => {
    vi.mocked(appsApi.listTaskTypes).mockResolvedValue([
      {
        name: "summarize_row",
        label: "摘要单行",
        tier: 1,
        output_fields: ["summary"],
        supports_write: true,
      },
      {
        name: "table_digest",
        label: "整表摘要",
        tier: 2,
        output_fields: ["summary"],
        supports_write: true,
      },
    ]);
    vi.mocked(appsApi.createTask).mockResolvedValue({
      task_id: "tsk_1",
      app_id: "app_1",
      table: "main",
      row_id: "row_1",
      task_type: "summarize_row",
      status: "queued",
      result: null,
      error: "",
      created_at: 1,
      started_at: null,
      finished_at: null,
    });
    const onCreated = vi.fn();
    const onClose = vi.fn();

    render(
      <AntdApp>
        <RunTaskModal
          open
          appId="app_1"
          tableKey="main"
          rowId="row_1"
          columns={columns}
          onClose={onClose}
          onCreated={onCreated}
        />
      </AntdApp>,
    );

    await waitFor(() =>
      expect(appsApi.listTaskTypes).toHaveBeenCalledWith("app_1"),
    );
    const submit = await screen.findByRole("button", { name: "加入队列" });
    submit.click();

    await waitFor(() =>
      expect(appsApi.createTask).toHaveBeenCalledWith("app_1", {
        task_type: "summarize_row",
        table: "main",
        row_id: "row_1",
        input: {},
      }),
    );
    expect(onCreated).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});
