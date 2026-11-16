import { fireEvent, render, screen } from "@testing-library/react";
import { App as AntdApp } from "antd";
import { describe, expect, it, vi } from "vitest";

import type { AppTable } from "../../../api/modules/apps";
import AppPublishModal from "./AppPublishModal";

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

const column = {
  name: "title",
  label: "标题",
  type: "string",
  required: true,
  values: [],
  pii: null,
};

function renderModal(tables: AppTable[]) {
  return render(
    <AntdApp>
      <AppPublishModal
        open
        tables={tables}
        publication={null}
        publishUrl=""
        busy={false}
        timeZone="UTC"
        onClose={vi.fn()}
        onCreate={vi.fn()}
        onPublishNow={vi.fn()}
        onRevoke={vi.fn()}
      />
    </AntdApp>,
  );
}

describe("AppPublishModal public preview", () => {
  it("counts only effectively public tables and their rows", () => {
    renderModal([
      {
        table_key: "main",
        label: "主表",
        row_count: 2,
        is_main: true,
        public_visible: true,
        row_scope: "app",
        columns: [column],
      },
      {
        table_key: "notes",
        label: "个人笔记",
        row_count: 5,
        is_main: false,
        public_visible: true,
        row_scope: "user",
        columns: [column],
      },
      {
        table_key: "catalog",
        label: "目录",
        row_count: 3,
        is_main: false,
        public_visible: true,
        row_scope: "app",
        columns: [{ ...column, name: "phone", pii: "phone" }],
      },
    ]);

    expect(screen.getByText("将公开 2 张表、5 行数据。")).toBeInTheDocument();
    expect(screen.getByText("主表 (2) · 目录 (3)")).toBeInTheDocument();
    expect(screen.queryByText(/个人笔记/)).not.toBeInTheDocument();
    expect(screen.getByText(/该应用包含敏感列/)).toBeInTheDocument();
  });

  it("shows the snapshot auto-refresh switch for an active link", () => {
    const onToggle = vi.fn();
    render(
      <AntdApp>
        <AppPublishModal
          open
          tables={[]}
          publication={{ active: true, published_at: 1 }}
          publishUrl=""
          busy={false}
          timeZone="UTC"
          snapshotSchedule={{
            schedule_id: "sch_1",
            action: "snapshot_publication",
            target: {},
            interval_seconds: 3600,
            next_run_at: 2,
            last_run_at: null,
            last_status: "error",
            last_error: "boom",
            failure_count: 1,
            enabled: true,
            created_at: 1,
            updated_at: 1,
          }}
          onToggleSnapshotSchedule={onToggle}
          onClose={vi.fn()}
          onCreate={vi.fn()}
          onPublishNow={vi.fn()}
          onRevoke={vi.fn()}
        />
      </AntdApp>,
    );

    expect(screen.getByText("自动刷新公开快照")).toBeInTheDocument();
    expect(screen.getByText("上次刷新失败：boom")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch"));
    expect(onToggle).toHaveBeenCalledWith(false);
  });

  it("warns when no table is public", () => {
    renderModal([
      {
        table_key: "notes",
        label: "个人笔记",
        row_count: 5,
        is_main: true,
        public_visible: true,
        row_scope: "user",
        columns: [column],
      },
    ]);

    expect(
      screen.getByText("没有公开表，公开链接只展示应用页面。"),
    ).toBeInTheDocument();
  });
});
