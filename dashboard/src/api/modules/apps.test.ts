import { beforeEach, describe, expect, it, vi } from "vitest";

import { request } from "../request";
import { appsApi } from "./apps";

vi.mock("../request", () => ({
  request: vi.fn(),
  requestBlob: vi.fn(),
  requestStream: vi.fn(),
  requestUpload: vi.fn(),
}));

describe("appsApi.listSetupPlans", () => {
  beforeEach(() => {
    vi.mocked(request).mockReset();
    vi.mocked(request).mockResolvedValue({
      plans: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
  });

  it("serializes only the provided filters", async () => {
    await appsApi.listSetupPlans({
      status: "draft",
      user_id: 2,
      has_error: true,
      limit: 50,
    });
    expect(request).toHaveBeenCalledWith(
      "/apps/setup-plans?status=draft&user_id=2&has_error=true&limit=50",
    );
  });

  it("omits the query string when empty", async () => {
    await appsApi.listSetupPlans();
    expect(request).toHaveBeenCalledWith("/apps/setup-plans");
  });
});
