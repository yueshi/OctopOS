import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { appsApi } from "../../api/modules/apps";
import MiniAppFrame from "./index";

vi.mock("react-i18next", async () => {
  const zh = (await import("../../locales/zh.json")).default;
  const lookup = (key: string): unknown => {
    let value: unknown = zh;
    for (const part of key.split(".")) {
      value = (value as Record<string, unknown> | undefined)?.[part];
    }
    return value;
  };
  const t = (key: string, fallback?: unknown): string => {
    const value = lookup(key);
    return typeof value === "string"
      ? value
      : typeof fallback === "string"
      ? fallback
      : key;
  };
  return { useTranslation: () => ({ t, i18n: { language: "zh" } }) };
});

vi.mock("../../api/modules/apps", () => ({
  appsApi: {
    getHtml: vi.fn(async () => "<html><body>app</body></html>"),
    rows: vi.fn(async () => ({ rows: [] })),
    appendRow: vi.fn(async () => ({ row_id: "row_1" })),
    updateRow: vi.fn(async () => ({ row_id: "row_1", row: {}, version: 2 })),
    deleteRow: vi.fn(async () => ({ deleted: "row_1" })),
    get: vi.fn(async () => ({ media_paths: [] })),
    asset: vi.fn(),
    generate: vi.fn(),
    reportViolation: vi.fn(),
    dataVersion: vi.fn(async () => ({ tables: [] })),
    callService: vi.fn(async () => ({
      value: null,
      cached: false,
      expires_at: null,
    })),
    serviceImage: vi.fn(async () => ({ asset: "cover.png" })),
    events: vi.fn(async () => ({ events: [], latest_id: 0 })),
    ackEvents: vi.fn(async () => ({ acknowledged: 0 })),
  },
}));

async function mountFrame(capabilities: readonly string[]) {
  const view = render(
    <MiniAppFrame appId="app_1" capabilities={capabilities} />,
  );
  const frame = await waitFor(() => {
    const element = view.container.querySelector("iframe");
    if (!element) throw new Error("frame not rendered");
    return element as HTMLIFrameElement;
  });
  return { view, frame };
}

function send(
  frame: HTMLIFrameElement,
  method: string,
  params: unknown,
  id = 1,
) {
  window.dispatchEvent(
    new MessageEvent("message", {
      data: { source: "octop-app", type: "request", id, method, params },
      source: frame.contentWindow,
    }),
  );
}

describe("MiniAppFrame write bridge", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("refetches the HTML when reloadToken changes", async () => {
    const view = render(<MiniAppFrame appId="app_1" reloadToken={0} />);
    await waitFor(() => expect(appsApi.getHtml).toHaveBeenCalledTimes(1));

    view.rerender(<MiniAppFrame appId="app_1" reloadToken={1} />);

    await waitFor(() => expect(appsApi.getHtml).toHaveBeenCalledTimes(2));
  });

  it("updates one row", async () => {
    const { frame } = await mountFrame(["data.read", "data.write"]);

    send(frame, "update", {
      rowId: "row_1",
      row: { status: "done" },
      expectedVersion: 3,
    });

    await waitFor(() =>
      expect(appsApi.updateRow).toHaveBeenCalledWith(
        "app_1",
        "row_1",
        { status: "done" },
        3,
      ),
    );
  });

  it("updates a batch row by row and reports the failures", async () => {
    vi.mocked(appsApi.updateRow).mockImplementation(async (appId, rowId) => {
      if (rowId === "row_bad") throw new Error("row changed since it was read");
      return { row_id: rowId, row: {}, version: 2 };
    });

    const { frame } = await mountFrame(["data.read", "data.write"]);

    send(frame, "updateMany", {
      rows: [
        { row_id: "row_ok", row: { status: "done" } },
        { row_id: "row_bad", row: { status: "done" } },
      ],
    });

    await waitFor(() => expect(appsApi.updateRow).toHaveBeenCalledTimes(2));
    expect(vi.mocked(appsApi.updateRow).mock.calls[0]?.[1]).toBe("row_ok");
    expect(vi.mocked(appsApi.updateRow).mock.calls[1]?.[1]).toBe("row_bad");
  });

  it("deletes a row", async () => {
    const { frame } = await mountFrame(["data.read", "data.write"]);

    send(frame, "remove", { rowId: "row_1", expectedVersion: 4 });

    await waitFor(() =>
      expect(appsApi.deleteRow).toHaveBeenCalledWith("app_1", "row_1", 4),
    );
  });

  it("calls a declared external service when external.fetch is allowed", async () => {
    vi.mocked(appsApi.callService).mockResolvedValue({
      value: { ok: true },
      cached: false,
      expires_at: null,
    });
    const { frame } = await mountFrame(["external.fetch"]);

    send(frame, "service", {
      name: "douban",
      tool: "fetch_json",
      args: { url: "https://api.example.com/books" },
    });

    await waitFor(() =>
      expect(appsApi.callService).toHaveBeenCalledWith(
        "app_1",
        "douban",
        "fetch_json",
        { url: "https://api.example.com/books" },
      ),
    );
  });

  it("reads the app event inbox with a cursor", async () => {
    const { frame } = await mountFrame(["events.read"]);

    send(frame, "events", { since: 7, limit: 20 });

    await waitFor(() =>
      expect(appsApi.events).toHaveBeenCalledWith("app_1", 7, 20),
    );
  });

  it("passes filters and sort to the row query", async () => {
    const { frame } = await mountFrame(["data.read"]);

    send(frame, "rows", {
      limit: 50,
      offset: 0,
      filters: { status: "pending" },
      sort: "-qty",
    });

    await waitFor(() =>
      expect(appsApi.rows).toHaveBeenCalledWith(
        "app_1",
        50,
        0,
        "main",
        { status: "pending" },
        "-qty",
        false,
      ),
    );
  });

  it("refuses a write when the app did not declare data.write", async () => {
    const { frame } = await mountFrame(["data.read"]);

    send(frame, "update", { rowId: "row_1", row: { status: "done" } });

    await waitFor(() => expect(appsApi.updateRow).not.toHaveBeenCalled());
  });
});
