import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

vi.mock("../i18n", () => ({
  default: { language: "zh" },
}));

describe("scoped token selection", () => {
  let mod: typeof import("./request");

  beforeEach(async () => {
    vi.resetModules();
    localStorage.clear();
    sessionStorage.clear();
    mod = await import("./request");
    mod.setAuthToken("platform-token");
    mod.setAppToken("app_1", "token-app-1", 120);
    mod.setAppToken("app_2", "token-app-2", 120);
    mod.setGuestSession({
      guestToken: "guest-token",
      refreshToken: "refresh-token",
    });
  });

  it("selects the token for the app in the path", () => {
    expect(mod.resolveRequestAuth("/apps/app_1/rows").token).toBe(
      "token-app-1",
    );
    expect(mod.resolveRequestAuth("/apps/app_2").token).toBe("token-app-2");
    expect(mod.resolveRequestAuth("/apps/app_1/rows").kind).toBe("app");
  });

  it("keeps non-app and guest routes on their own credentials", () => {
    expect(mod.resolveRequestAuth("/agents").kind).toBe("platform");
    expect(mod.resolveRequestAuth("/agents").token).toBe("platform-token");
    expect(mod.resolveRequestAuth("/guest/apps").kind).toBe("guest");
    expect(mod.resolveRequestAuth("/guest/apps").token).toBe("guest-token");
  });

  it("does not mistake reserved app routes for an app id", () => {
    expect(mod.resolveRequestAuth("/apps/access-requests").kind).toBe(
      "platform",
    );
    expect(mod.resolveRequestAuth("/apps/setup-plans").kind).toBe("platform");
    expect(mod.resolveRequestAuth("/apps/boards/b1").kind).toBe("platform");
    expect(mod.resolveRequestAuth("/apps/in-thread").kind).toBe("platform");
    expect(mod.resolveRequestAuth("/apps/dataset/import").kind).toBe(
      "platform",
    );
  });

  it("prefers a platform login for join redemption, then the guest identity", () => {
    expect(mod.resolveRequestAuth("/apps/join/redeem").kind).toBe("platform");
    localStorage.removeItem("auth_token");
    sessionStorage.removeItem("auth_token");
    expect(mod.resolveRequestAuth("/apps/join/redeem").kind).toBe("guest");
    expect(mod.resolveRequestAuth("/apps/join/redeem").token).toBe(
      "guest-token",
    );
  });

  it("falls back to the platform token when an app token expired", () => {
    mod.setAppToken("app_1", "expired", -1);
    expect(mod.resolveRequestAuth("/apps/app_1/rows")).toEqual({
      kind: "platform",
      token: "platform-token",
      appId: "app_1",
    });
  });

  it("keeps app tokens when a normal login is saved", () => {
    mod.setAuthToken("new-platform-token");
    expect(mod.getAppToken("app_1")).toBe("token-app-1");
    expect(mod.getAppToken("app_2")).toBe("token-app-2");
  });

  it("does not use a guest portal token on app routes", () => {
    localStorage.removeItem("auth_token");
    sessionStorage.removeItem("auth_token");
    expect(mod.resolveRequestAuth("/apps/app_3/rows")).toEqual({
      kind: "none",
      token: "",
      appId: "app_3",
    });
  });

  it("honours the guest remember preference", () => {
    mod.setGuestSession({ guestToken: "session-guest", remember: false });
    expect(sessionStorage.getItem("guest_token")).toBe("session-guest");
    expect(localStorage.getItem("guest_token")).toBeNull();
    expect(mod.getGuestToken()).toBe("session-guest");
  });
});
