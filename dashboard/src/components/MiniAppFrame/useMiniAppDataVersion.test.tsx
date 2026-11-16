import { act, renderHook } from "@testing-library/react";
import type { RefObject } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { appsApi } from "../../api/modules/apps";
import {
  MINIAPP_DATA_VERSION_POLL_MS,
  useMiniAppDataVersion,
} from "./useMiniAppDataVersion";

vi.mock("../../api/modules/apps", () => ({
  appsApi: { dataVersion: vi.fn() },
}));

function fakeFrameRef(postMessage: ReturnType<typeof vi.fn>) {
  return {
    current: { contentWindow: { postMessage } },
  } as unknown as RefObject<HTMLIFrameElement | null>;
}

describe("useMiniAppDataVersion", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(appsApi.dataVersion).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("forwards a data-changed event when a table version moves", async () => {
    const postMessage = vi.fn();
    vi.mocked(appsApi.dataVersion)
      .mockResolvedValueOnce({
        tables: [{ table_key: "main", data_version: 1, row_count: 1 }],
      })
      .mockResolvedValueOnce({
        tables: [{ table_key: "main", data_version: 2, row_count: 2 }],
      });

    const { result } = renderHook(() =>
      useMiniAppDataVersion("app_1", fakeFrameRef(postMessage)),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(postMessage).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(MINIAPP_DATA_VERSION_POLL_MS);
    });
    expect(result.current).toBe(1);
    expect(postMessage).toHaveBeenCalledWith(
      {
        source: "octop-app-host",
        type: "data-changed",
        data: {
          tables: [{ table_key: "main", data_version: 2, row_count: 2 }],
        },
      },
      "*",
    );
  });

  it("does not poll while disabled and resumes when enabled", async () => {
    const postMessage = vi.fn();
    vi.mocked(appsApi.dataVersion).mockResolvedValue({
      tables: [{ table_key: "main", data_version: 1, row_count: 1 }],
    });

    const { rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useMiniAppDataVersion("app_1", fakeFrameRef(postMessage), enabled),
      { initialProps: { enabled: false } },
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MINIAPP_DATA_VERSION_POLL_MS * 2);
    });
    expect(appsApi.dataVersion).not.toHaveBeenCalled();

    rerender({ enabled: true });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(appsApi.dataVersion).toHaveBeenCalledTimes(1);
  });

  it("stays quiet while the versions are unchanged", async () => {
    const postMessage = vi.fn();
    vi.mocked(appsApi.dataVersion).mockResolvedValue({
      tables: [{ table_key: "main", data_version: 7, row_count: 3 }],
    });

    renderHook(() => useMiniAppDataVersion("app_1", fakeFrameRef(postMessage)));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MINIAPP_DATA_VERSION_POLL_MS);
    });
    expect(postMessage).not.toHaveBeenCalled();
  });
});
