import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppAssistantEvent } from "../../../api/modules/apps";
import AppAssistantDrawer from "./AppAssistantDrawer";

const api = vi.hoisted(() => ({
  history: vi.fn(),
  ask: vi.fn(),
}));

vi.mock("../../../api/modules/apps", () => ({
  appsApi: { assistantHistory: api.history },
  askAppAssistant: api.ask,
}));

vi.mock("../../../components/Markdown", () => ({
  default: ({ content }: { content: string }) => (
    <div data-testid="markdown">{content}</div>
  ),
}));

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
      return typeof options === "string"
        ? options
        : (options as { defaultValue?: string } | undefined)?.defaultValue ||
            key;
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

const historyMessage = {
  role: "assistant" as const,
  content: "有一个客户表。",
  created_at: 0,
  process: [
    { kind: "thinking" as const, text: "先查看表结构" },
    {
      kind: "tool" as const,
      call_id: "c1",
      name: "app_tables",
      arguments: { table: "main" },
      result: '{"tables":[]}',
      status: "ok" as const,
    },
  ],
  citations: [{ table: "main", row_id: "r1" }],
};

function renderDrawer() {
  return render(
    <AppAssistantDrawer
      appId="app_1"
      open
      onClose={vi.fn()}
      onOpenCitation={vi.fn()}
    />,
  );
}

describe("AppAssistantDrawer process history", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.history.mockResolvedValue({
      messages: [
        { role: "user", content: "有哪些数据？", created_at: 0 },
        historyMessage,
      ],
    });
  });

  it("shows persisted thinking and tool calls with arguments and result", async () => {
    renderDrawer();

    expect(await screen.findByText("有一个客户表。")).toBeInTheDocument();
    const summary = screen.getByText("已调用 1 次工具，1 次深度思考");
    expect(screen.queryByText("先查看表结构")).not.toBeInTheDocument();

    fireEvent.click(summary);
    expect(screen.getByText("先查看表结构")).toBeInTheDocument();
    expect(screen.getByText("查看数据表")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("引用:")).toBeInTheDocument();
    expect(screen.getByText("r1")).toBeInTheDocument();

    fireEvent.click(screen.getByText("查看数据表"));
    expect(screen.getByText("参数")).toBeInTheDocument();
    expect(screen.getByText("结果")).toBeInTheDocument();
    expect(screen.getByText(/"table": "main"/)).toBeInTheDocument();
    expect(screen.getByText('{"tables":[]}')).toBeInTheDocument();
  });

  it("renders thinking and tool events while the answer streams", async () => {
    api.history.mockResolvedValue({ messages: [] });
    api.ask.mockImplementation(
      async (
        _appId: string,
        _question: string,
        onEvent: (event: AppAssistantEvent) => void,
      ) => {
        onEvent({ type: "thinking", text: "先查询数据" });
        onEvent({
          type: "tool_start",
          call_id: "c1",
          name: "app_query_rows",
          arguments: { table: "main" },
        });
        onEvent({
          type: "tool_end",
          call_id: "c1",
          name: "app_query_rows",
          result: '{"total":1}',
          status: "ok",
        });
        onEvent({ type: "token", text: "查到 1 行。" });
        onEvent({
          type: "done",
          rounds: 2,
          input_tokens: 0,
          output_tokens: 0,
          citations: [],
          charts: [],
        });
      },
    );

    renderDrawer();
    await screen.findByText("可以询问关于该应用数据的任何问题。");
    const input = screen.getByPlaceholderText("询问数据相关的问题…");
    fireEvent.change(input, { target: { value: "查一下" } });
    fireEvent.click(screen.getByText(/发\s*送/));

    expect(await screen.findByText("查到 1 行。")).toBeInTheDocument();
    const summary = await screen.findByText("已调用 1 次工具，1 次深度思考");
    fireEvent.click(summary);
    expect(screen.getByText("先查询数据")).toBeInTheDocument();
    expect(screen.getByText("查询数据")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("已完成")).toBeInTheDocument());
  });
});
