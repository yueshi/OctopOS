import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../api/request", () => ({
  request: vi.fn(),
}));

import { request } from "../../../api/request";
import {
  projectAgentInbox,
  projectAgentMetrics,
  projectAgentStatus,
  projectChannels,
  projectConnectors,
  projectUsageSummary,
  runSystemQuery,
} from "./queries";

const requestMock = vi.mocked(request);

describe("system app query projections", () => {
  beforeEach(() => {
    requestMock.mockReset();
  });

  it("keeps only the safe usage fields", () => {
    const projected = projectUsageSummary({
      window: "last_30d",
      total_tokens: 1200,
      buckets: [
        {
          key: "2026-11-17",
          label: "11-17",
          total_tokens: 1200,
          secret: "should not leak",
        },
      ],
      api_key: "sk-secret",
      credentials: { token: "secret" },
    });

    expect(projected.total_tokens).toBe(1200);
    expect(projected.buckets).toEqual([
      expect.objectContaining({ key: "2026-11-17", total_tokens: 1200 }),
    ]);
    expect(JSON.stringify(projected)).not.toContain("sk-secret");
    expect(JSON.stringify(projected)).not.toContain("credentials");
  });

  it("projects agent metrics without leaking task payloads", () => {
    const projected = projectAgentMetrics({
      agent_id: "agent_1",
      window: "last_7d",
      tasks: { open: 2, failed: 1 },
      usage: { total_tokens: 99, model_calls: 3 },
      budget: { used: 10, limit: 100, remaining: 90 },
      secret: "nope",
    });

    expect(projected.tasks).toEqual({ open: 2, failed: 1 });
    expect(projected.usage).toEqual({
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 99,
      model_calls: 3,
    });
    expect(projected.budget).toEqual({
      used: 10,
      limit: 100,
      remaining: 90,
    });
  });

  it("truncates agent inbox details", () => {
    const projected = projectAgentInbox({
      counts: { approval: 1 },
      items: [
        {
          kind: "approval",
          task_id: "task_1",
          title: "Approve",
          detail: "x".repeat(900),
        },
      ],
    });

    expect(projected.counts).toEqual({ approval: 1 });
    expect(
      (projected.items as Array<{ detail: string }>)[0].detail,
    ).toHaveLength(500);
  });

  it("drops channel config and credentials from the list payload", () => {
    const projected = projectChannels([
      {
        id: "channel_1",
        agent_id: "agent_1",
        kind: "wecom",
        name: "WeCom",
        enabled: true,
        runtime: { connected: true, error: null },
        config: { secret: "never" },
      },
    ]);

    expect(projected.channels).toEqual([
      expect.objectContaining({
        id: "channel_1",
        kind: "wecom",
        connected: true,
      }),
    ]);
    expect(JSON.stringify(projected)).not.toContain("never");
  });

  it("drops connector credentials and raw config", () => {
    const projected = projectConnectors([
      {
        instance_id: "conn_1",
        kind: "github",
        display_name: "GitHub",
        status: "active",
        has_credentials: true,
        credentials: { token: "secret" },
      },
    ]);

    expect(projected.connectors).toEqual([
      expect.objectContaining({
        instance_id: "conn_1",
        status: "active",
        has_credentials: true,
      }),
    ]);
    expect(JSON.stringify(projected)).not.toContain("secret");
  });

  it("truncates the last runtime error", () => {
    const projected = projectAgentStatus({
      state: "failed",
      last_error: "e".repeat(900),
    });

    expect((projected.last_error as string).length).toBe(500);
  });

  it("rejects unknown query keys", async () => {
    await expect(
      runSystemQuery("usage.export", {}, { agentId: "agent_1" }),
    ).rejects.toThrow("unknown system query");
  });

  it("sanitizes the usage window and granularity before calling the API", async () => {
    requestMock.mockResolvedValue({ total_tokens: 0, buckets: [] });

    await runSystemQuery(
      "usage.summary",
      { window: "day:../../etc", granularity: "bogus" },
      { agentId: "agent_1" },
    );

    const path = String(requestMock.mock.calls[0]?.[0] ?? "");
    expect(path).toContain("window=last_30d");
    expect(path).toContain("granularity=by_day");
    expect(path).toContain("agent_id=agent_1");
  });
});
