import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AppColumn } from "../../../api/modules/apps";
import AppDataTab from "./AppDataTab";

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

const columns: AppColumn[] = [
  {
    name: "title",
    label: "标题",
    type: "string",
    required: true,
    values: [],
    pii: null,
  },
];

function renderTab(canEditRows: boolean, onViewHistory = vi.fn()) {
  return render(
    <AppDataTab
      tables={[
        {
          table_key: "main",
          label: "主表",
          row_count: 1,
          is_main: true,
          public_visible: true,
          columns,
        },
      ]}
      dataTable="main"
      onTableChange={vi.fn()}
      canEditRows={canEditRows}
      columns={columns}
      rows={[
        {
          row: { title: "A" },
          meta: { row_id: "row_1", version: 1, source: "manual" },
        },
      ]}
      loading={false}
      page={1}
      pageSize={50}
      total={1}
      tableWidth={500}
      search=""
      onSearchChange={vi.fn()}
      onPageChange={vi.fn()}
      onAddRow={vi.fn()}
      onExport={vi.fn()}
      onBackup={vi.fn()}
      onEditRow={vi.fn()}
      onDeleteRow={vi.fn()}
      onViewHistory={onViewHistory}
    />,
  );
}

describe("AppDataTab row actions", () => {
  it("lets a viewer open history without edit or delete", () => {
    const onViewHistory = vi.fn();
    renderTab(false, onViewHistory);

    expect(screen.getByText("历史")).toBeInTheDocument();
    expect(screen.queryByText("编辑")).not.toBeInTheDocument();
    expect(screen.queryByText("删除")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("历史"));
    expect(onViewHistory).toHaveBeenCalledWith({
      row_id: "row_1",
      version: 1,
      source: "manual",
    });
  });

  it("shows edit and delete to an editor", () => {
    renderTab(true);

    expect(screen.getByText("历史")).toBeInTheDocument();
    expect(screen.getByText("编辑")).toBeInTheDocument();
    expect(screen.getByText("删除")).toBeInTheDocument();
  });
});
