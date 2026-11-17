import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../api/request", () => ({
  request: vi.fn(),
}));

import { request } from "../../../api/request";
import { runSystemAction } from "./actions";

const requestMock = vi.mocked(request);

describe("system app actions", () => {
  beforeEach(() => {
    requestMock.mockReset();
    requestMock.mockResolvedValue({});
  });

  it("enables a skill through the existing endpoint", async () => {
    await runSystemAction(
      "skills.toggle",
      { slug: "sales-report", enabled: true },
      { agentId: "agent_1" },
    );

    expect(requestMock).toHaveBeenCalledWith(
      "/agents/agent_1/skills/sales-report/enable",
      { method: "POST" },
    );
  });

  it("disables a skill through the existing endpoint", async () => {
    await runSystemAction(
      "skills.toggle",
      { slug: "sales-report", enabled: false },
      { agentId: "agent_1" },
    );

    expect(requestMock).toHaveBeenCalledWith(
      "/agents/agent_1/skills/sales-report/disable",
      { method: "POST" },
    );
  });

  it("runs a scheduled job now", async () => {
    await runSystemAction(
      "cron.run",
      { cron_id: "cron_1" },
      { agentId: "agent_1" },
    );

    expect(requestMock).toHaveBeenCalledWith(
      "/agents/agent_1/cron/cron_1/run-now",
      { method: "POST" },
    );
  });

  it("rejects unknown actions and missing identifiers", async () => {
    await expect(
      runSystemAction("skills.delete", {}, { agentId: "agent_1" }),
    ).rejects.toThrow("unknown system action");
    await expect(
      runSystemAction("skills.toggle", {}, { agentId: "agent_1" }),
    ).rejects.toThrow("skill slug is required");
    await expect(
      runSystemAction("cron.run", {}, { agentId: "agent_1" }),
    ).rejects.toThrow("cron id is required");
  });
});
