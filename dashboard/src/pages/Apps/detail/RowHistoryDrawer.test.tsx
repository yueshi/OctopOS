import { render, screen, waitFor } from "@testing-library/react";
import { App as AntdApp } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { appsApi, type AppColumn } from "../../../api/modules/apps";
import RowHistoryDrawer from "./RowHistoryDrawer";

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
  appsApi: { rowHistory: vi.fn() },
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

function renderDrawer(canSeePii = true) {
  return render(
    <AntdApp>
      <RowHistoryDrawer
        open
        onClose={vi.fn()}
        appId="app_test"
        rowId="row_1"
        columns={columns}
        canSeePii={canSeePii}
      />
    </AntdApp>,
  );
}

describe("RowHistoryDrawer", () => {
  beforeEach(() => {
    vi.mocked(appsApi.rowHistory).mockReset();
  });

  it("renders actions, actor and a before/after diff", async () => {
    vi.mocked(appsApi.rowHistory).mockResolvedValue({
      entries: [
        {
          history_id: 2,
          table: "main",
          row_id: "row_1",
          version: 2,
          action: "update",
          actor: "user:7",
          source: "manual",
          changed_at: 1_700_000_000,
          before: { title: "A", phone: "••••" },
          after: { title: "B", phone: "••••" },
        },
        {
          history_id: 1,
          table: "main",
          row_id: "row_1",
          version: 1,
          action: "delete",
          actor: "_system",
          source: "import",
          changed_at: 1_699_999_000,
          before: { title: "A", phone: "••••" },
          after: null,
        },
      ],
      limit: 50,
      offset: 0,
    });

    renderDrawer();

    expect(await screen.findByText("变更历史")).toBeInTheDocument();
    expect(screen.getByText("修改")).toBeInTheDocument();
    expect(screen.getByText("删除")).toBeInTheDocument();
    expect(screen.getByText("用户 #7")).toBeInTheDocument();
    expect(screen.getByText("系统")).toBeInTheDocument();
    expect(screen.getByText("版本 2")).toBeInTheDocument();
    expect(screen.getByText("B")).toBeInTheDocument();
    expect(screen.getAllByText("A").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/敏感字段已脱敏/)).not.toBeInTheDocument();
  });

  it("warns viewers when the app has masked PII columns", async () => {
    vi.mocked(appsApi.rowHistory).mockResolvedValue({
      entries: [],
      limit: 50,
      offset: 0,
    });

    renderDrawer(false);

    await waitFor(() =>
      expect(screen.getByText("暂无变更记录。")).toBeInTheDocument(),
    );
    expect(screen.getByText(/敏感字段已脱敏/)).toBeInTheDocument();
  });
});
