import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import JoinPage from "./index";

const api = vi.hoisted(() => ({
  previewInvite: vi.fn(),
  redeemInvite: vi.fn(),
  previewJoinCode: vi.fn(),
  redeemJoinCode: vi.fn(),
}));

const auth = vi.hoisted(() => ({
  getAuthToken: vi.fn(() => ""),
  hasGuestSession: vi.fn(() => false),
  setAppToken: vi.fn(),
  setGuestSession: vi.fn(),
}));

vi.mock("../../api/modules/apps", () => ({ appsApi: api }));

vi.mock("../../api/request", () => auth);

vi.mock("../../utils/antdMessage", () => ({
  message: { warning: vi.fn(), success: vi.fn(), error: vi.fn() },
}));

vi.mock("react-i18next", async () => {
  const zh = (await import("../../locales/zh.json")).default;
  const lookup = (key: string): unknown => {
    let value: unknown = zh;
    for (const part of key.split(".")) {
      value = (value as Record<string, unknown> | undefined)?.[part];
    }
    return value;
  };
  const t = (key: string, options?: unknown): string => {
    const value = lookup(key);
    if (typeof value !== "string") return key;
    if (options && typeof options === "object" && !Array.isArray(options)) {
      return value.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
        String((options as Record<string, unknown>)[name] ?? ""),
      );
    }
    return value;
  };
  return { useTranslation: () => ({ t, i18n: { language: "zh" } }) };
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/join" element={<JoinPage />} />
        <Route path="/apps/:appId" element={<div>runtime-app</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("JoinPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.getAuthToken.mockReturnValue("platform-token");
    auth.hasGuestSession.mockReturnValue(false);
  });

  it("redeems a member invite and stores only the app token", async () => {
    api.previewInvite.mockResolvedValue({
      app_id: "app_1",
      app_name: "销售台账",
      audience: "member",
      permission: "view",
      host_status: "not_required",
    });
    api.redeemInvite.mockResolvedValue({
      app_id: "app_1",
      user_id: 2,
      role: "view",
      status: "active",
      display_name: "小明",
      guest_token: null,
      refresh_token: null,
      app_token: "app-token",
      expires_in: 7200,
      guest_expires_in: null,
      refresh_expires_in: null,
    });
    renderAt("/join?invite=tok");

    expect(await screen.findByText("加入「销售台账」")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /加\s*入/ }));

    await waitFor(() => expect(api.redeemInvite).toHaveBeenCalled());
    expect(api.redeemInvite).toHaveBeenCalledWith("tok", "");
    expect(auth.setAppToken).toHaveBeenCalledWith("app_1", "app-token", 7200);
    expect(auth.setGuestSession).not.toHaveBeenCalled();
    expect(await screen.findByText("runtime-app")).toBeInTheDocument();
  });

  it("requires consent before redeeming a guest join code", async () => {
    api.previewJoinCode.mockResolvedValue({
      app_id: "app_1",
      app_name: "销售台账",
      audience: "guest",
      role: "participate",
      requires_approval: false,
      host_approval_required: false,
    });
    api.redeemJoinCode.mockResolvedValue({
      app_id: "app_1",
      user_id: 9,
      role: "participate",
      status: "active",
      display_name: "小明",
      guest_token: "guest-token",
      refresh_token: "refresh-token",
      app_token: "app-token",
      expires_in: 7200,
      guest_expires_in: 604800,
      refresh_expires_in: 2592000,
    });
    renderAt("/join?code=abc123");

    const submit = await screen.findByRole("button", { name: /加\s*入/ });
    expect(submit).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /我同意/ }));
    await userEvent.type(
      screen.getByPlaceholderText("应用所有者看到的名称"),
      "小明",
    );
    await userEvent.click(submit);

    await waitFor(() => expect(api.redeemJoinCode).toHaveBeenCalled());
    expect(api.redeemJoinCode).toHaveBeenCalledWith("abc123", "小明");
    expect(auth.setGuestSession).toHaveBeenCalledWith(
      expect.objectContaining({
        guestToken: "guest-token",
        refreshToken: "refresh-token",
        remember: true,
      }),
    );
    expect(auth.setAppToken).toHaveBeenCalledWith("app_1", "app-token", 7200);
  });

  it("shows the host-pending screen when an invite awaits the host", async () => {
    api.previewInvite.mockRejectedValue(
      new Error(
        'Request failed: 403 Forbidden - {"error":{"code":"APP_HOST_APPROVAL_REQUIRED","message":"pending"}}',
      ),
    );
    renderAt("/join?invite=tok");

    expect(await screen.findByText("等待宿主审批")).toBeInTheDocument();
  });

  it("keeps the manual code entry on an empty query", async () => {
    renderAt("/join");
    expect(screen.getByText("加入小程序")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("加入码")).toBeInTheDocument();
  });
});
